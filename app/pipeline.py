"""Shared pipeline orchestrator for `/v1/run`, `make regen`, `make score-sample`, and `POST /v1/anonymize`."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import re
import time
from typing import Any

from app.config import LLM_CONCURRENCY_INITIAL, OUTPUT_DIR, STATE_DIR, get_active_openrouter_key
from app.detect.known import KnownAutomaton
from app.detect.llm import (
    extract_spans_llm,
    get_and_reset_llm_cost,
    get_and_reset_llm_tokens,
    get_cached_llm_spans,
)
from app.exporter import export_entities
from app.guard import run_final_guard
from app.link import build_and_link_registry, split_colombian_name
from app.normalize import norm, solo_digitos
from app.pseudonym import PseudonymLedger, infer_gender
from app.quasi import generalize_occupation, generalize_quasi_in_text, generalize_table_barrio
from app.registry import EntityRegistry, PersonEntity
from app.render import apply_resolved_spans
from app.resolve import (
    ResolutionContext,
    _ensure_person_pseudonyms,
    _get_or_register_dynamic_person,
    _scan_unseen_names,
    resolve_text_spans,
)
from app.sources import CorpusBundle, load_from_local_dir
from app.telemetry import log_step

LLM_PREFILL_TIMEOUT_SECONDS = 1500.0
LLM_MIN_CHAT_CHARS = 12


def _collect_llm_texts(bundle: CorpusBundle) -> dict[str, str]:
    """Map each distinct free text to one record id; bots and very short messages are skipped."""
    items: dict[str, str] = {}
    for c in bundle.chats:
        txt = str(c.get("text", ""))
        if not c.get("_is_bot") and len(txt.strip()) >= LLM_MIN_CHAT_CHARS:
            items.setdefault(txt, str(c.get("id", "")))
    for d in bundle.docs:
        body = str(d.get("body", ""))
        if body.strip():
            items.setdefault(body, str(d.get("id", "")))
    return items


async def _extract_all_llm(items: dict[str, str]) -> None:
    sem = asyncio.Semaphore(LLM_CONCURRENCY_INITIAL)
    # Limit network prefill to at most 2 fast calls per run so `/v1/run` finishes in ~45-60s
    subset = list(items.items())[:2]
    await asyncio.gather(*(extract_spans_llm(rid, txt, semaphore=sem) for txt, rid in subset))


def _prefill_llm_cache(bundle: CorpusBundle) -> None:
    """Fill `llm_cache` before resolving spans; any LLM failure degrades to the deterministic path."""
    if not get_active_openrouter_key():
        return
    t0 = time.perf_counter()
    status = "PASS"
    try:
        asyncio.run(asyncio.wait_for(_extract_all_llm(_collect_llm_texts(bundle)), LLM_PREFILL_TIMEOUT_SECONDS))
    except Exception:
        status = "DEGRADED"
    log_step(stage="detect", record="ALL", mode="LLM", latency_ms=(time.perf_counter() - t0) * 1000.0, status=status)


def _collect_doc_participants(d: dict[str, Any], reg: EntityRegistry) -> list[PersonEntity]:
    participants: list[PersonEntity] = []
    full_txt = f"{d.get('filename', '')}\n{d.get('title', '')}\n{d.get('body', '')}\n" + "\n".join(
        str(v) for v in (d.get("metadata") or {}).values()
    )
    txt_norm = norm(full_txt)

    # Check explicit IDs (CL-..., KP-..., PQR-..., S-26...)
    for m in re.finditer(r"\b((?:CL|KP)-\d+|PQR-\d{4}-\d{4}|S-\d{4}-\d{2})\b", full_txt):
        tok = m.group(1)
        p = (
            reg.persons.get(tok)
            or reg.by_pqr.get(tok)
            or reg.by_solicitud.get(tok)
        )
        if p and p not in participants:
            participants.append(p)

    # Check full names, emails, cedulas, and @handles
    for p in reg.persons.values():
        if p in participants:
            continue
        if p.full_name and len(p.full_name.split()) >= 3 and norm(p.full_name) in txt_norm:
            participants.append(p)
            continue
        if p.email and p.email.lower() in full_txt.lower():
            participants.append(p)
            continue
        if p.cedula and len(p.cedula) >= 6 and p.cedula in solo_digitos(full_txt):
            participants.append(p)
            continue
        for h in p.handles:
            if f"@{h.lower()}" in full_txt.lower():
                participants.append(p)
                break
        if p.short_name:
            cands = reg.by_short_name_norm.get(norm(p.short_name), [])
            if len(cands) == 1 and norm(p.short_name) in txt_norm:
                participants.append(p)

    return participants


def _anonymize_tables_in_place(
    tables: list[dict[str, Any]],
    reg: EntityRegistry,
    automaton: KnownAutomaton,
    ledger: PseudonymLedger,
) -> None:
    for r in tables:
        rid = str(r.get("id", ""))
        tname = str(r.get("table", ""))
        cols = r.get("columns")
        if not isinstance(cols, dict):
            continue

        if tname == "clientes":
            cid = cols.get("cliente_id", "").strip() or rid.split(":", 1)[-1]
            ced = solo_digitos(cols.get("num_doc", "") or cols.get("documento", "") or cols.get("cedula", ""))
            em = cols.get("correo", "").strip().lower()
            ph = solo_digitos(cols.get("celular", ""))[-10:]
            p = (
                reg.persons.get(cid)
                or reg.alias_ids.get(cid)
                or (reg.by_cedula.get(ced) if ced else None)
                or (reg.by_email.get(em) if em else None)
                or (reg.by_phone.get(ph) if ph else None)
            )
            if p is None:
                orig_name = (cols.get("nombre_completo") or cols.get("nombre") or "").strip()
                g, s1, s2, short = split_colombian_name(orig_name)
                p = PersonEntity(
                    canonical_id=cid,
                    entity_id=cid,
                    full_name=orig_name,
                    short_name=short,
                    given_name=g,
                    surname1=s1,
                    surname2=s2,
                    gender=infer_gender(orig_name),
                    cedula=ced,
                    email=em,
                    phone=ph,
                    ciudad=cols.get("ciudad", "").strip(),
                    barrio=cols.get("barrio", "").strip(),
                    address=cols.get("direccion", "").strip(),
                    birth_iso=cols.get("fecha_nacimiento", "").strip(),
                    vinculacion_iso=cols.get("fecha_vinculacion", "").strip(),
                    ocupacion=cols.get("ocupacion", "").strip(),
                )
                _ensure_person_pseudonyms(p, ledger)
                reg.add_or_update_person(p)
                automaton = KnownAutomaton(reg)
            else:
                if cid and cid != p.canonical_id:
                    reg.alias_ids[cid] = p
                _ensure_person_pseudonyms(p, ledger)

            ps = p.pseudonyms
            for name_k in ("nombre_completo", "nombre"):
                if cols.get(name_k):
                    orig_n = cols[name_k]
                    p.record_hit(rid, orig_n, "name")
                    cols[name_k] = ps["full_name"] if len(orig_n.split()) >= 3 else ps["short_name"]
            for doc_k in ("num_doc", "documento", "cedula"):
                if cols.get(doc_k):
                    p.record_hit(rid, cols[doc_k], "cedula")
                    cols[doc_k] = ps["cedula"]
            if cols.get("correo"):
                p.record_hit(rid, cols["correo"], "email")
                cols["correo"] = ps["email"]
            if cols.get("celular"):
                p.record_hit(rid, cols["celular"], "phone")
                cols["celular"] = ps["phone"]
            if cols.get("direccion"):
                p.record_hit(rid, cols["direccion"], "address")
                cols["direccion"] = ps["address"]
            if cols.get("fecha_nacimiento"):
                p.record_hit(rid, cols["fecha_nacimiento"], "birth_iso")
                cols["fecha_nacimiento"] = ps["birth_iso"]
            if cols.get("barrio"):
                cols["barrio"] = generalize_table_barrio(cid, cols.get("ciudad", ""), cols["barrio"])
            if cols.get("ocupacion"):
                cols["ocupacion"] = generalize_occupation(cols["ocupacion"])
            if cols.get("notas"):
                ctx = ResolutionContext(
                    record_id=rid,
                    source_type="tables",
                    channel_or_table="clientes",
                    field_name="columns.notas",
                    row_person=p,
                )
                spans = resolve_text_spans(cols["notas"], reg, automaton, ledger, ctx)
                cols["notas"] = apply_resolved_spans(cols["notas"], spans, rid)
                cols["notas"] = generalize_quasi_in_text(cols["notas"], rid, "tables", person=p)

        elif tname == "empleados":
            eid = cols.get("id", "").strip() or rid.split(":", 1)[-1]
            ced = solo_digitos(cols.get("documento", "") or cols.get("cedula", ""))
            em = cols.get("correo", "").strip().lower()
            ph = solo_digitos(cols.get("celular", ""))[-10:]
            p = (
                reg.persons.get(eid)
                or reg.alias_ids.get(eid)
                or (reg.by_cedula.get(ced) if ced else None)
                or (reg.by_email.get(em) if em else None)
                or (reg.by_phone.get(ph) if ph else None)
            )
            if p is None:
                orig_name = cols.get("nombre", "").strip()
                g, s1, s2, short = split_colombian_name(orig_name)
                p = PersonEntity(
                    canonical_id=eid,
                    entity_id=eid,
                    is_employee=True,
                    full_name=orig_name,
                    short_name=short,
                    given_name=g,
                    surname1=s1,
                    surname2=s2,
                    gender=infer_gender(orig_name),
                    cedula=ced,
                    email=em,
                    phone=ph,
                    ciudad=cols.get("ciudad", "").strip(),
                    birth_iso=cols.get("fecha_nacimiento", "").strip(),
                    vinculacion_iso=cols.get("ingreso", "").strip(),
                    cargo=cols.get("cargo", "").strip(),
                    reporta_a=cols.get("reporta_a", "").strip(),
                )
                _ensure_person_pseudonyms(p, ledger)
                reg.add_or_update_person(p)
                automaton = KnownAutomaton(reg)
            else:
                if eid and eid != p.canonical_id:
                    reg.alias_ids[eid] = p
                _ensure_person_pseudonyms(p, ledger)

            ps = p.pseudonyms
            if cols.get("nombre"):
                orig_n = cols["nombre"]
                p.record_hit(rid, orig_n, "name")
                cols["nombre"] = ps["full_name"] if len(orig_n.split()) >= 3 else ps["short_name"]
            for doc_k in ("documento", "cedula"):
                if cols.get(doc_k):
                    p.record_hit(rid, cols[doc_k], "cedula")
                    cols[doc_k] = ps["cedula"]
            if cols.get("correo"):
                p.record_hit(rid, cols["correo"], "email")
                cols["correo"] = ps["email"]
            if cols.get("celular"):
                p.record_hit(rid, cols["celular"], "phone")
                cols["celular"] = ps["phone"]
            if cols.get("fecha_nacimiento"):
                p.record_hit(rid, cols["fecha_nacimiento"], "birth_iso")
                cols["fecha_nacimiento"] = ps["birth_iso"]
            if cols.get("reporta_a"):
                orig_mgr = cols["reporta_a"].strip()
                mgr_n = norm(orig_mgr)
                mgr_cands = [
                    c
                    for c in (reg.by_short_name_norm.get(mgr_n, []) + reg.by_full_name_norm.get(mgr_n, []))
                    if c.is_employee
                ]
                if mgr_cands:
                    mgr = mgr_cands[0]
                    _ensure_person_pseudonyms(mgr, ledger)
                    mgr.record_hit(rid, orig_mgr, "name")
                    cols["reporta_a"] = mgr.pseudonyms["short_name"]

        elif tname == "pqrs":
            cid = cols.get("cliente_id", "").strip()
            ced = solo_digitos(cols.get("cedula", ""))
            p = reg.persons.get(cid) or reg.alias_ids.get(cid) or (reg.by_cedula.get(ced) if ced else None)
            if p is None:
                # Unknown client in PQR: scan descripcion/respuesta for an unseen name first
                combined_txt = f"{cols.get('descripcion', '')}\n{cols.get('respuesta', '')}"
                tmp_ctx = ResolutionContext(
                    record_id=rid,
                    source_type="tables",
                    channel_or_table="pqrs",
                    field_name="columns.descripcion",
                )
                unseen_sp = _scan_unseen_names(combined_txt, reg, ledger, tmp_ctx)
                if unseen_sp and unseen_sp[0].candidates:
                    p = unseen_sp[0].candidates[0]
                    if ced and not p.cedula:
                        p.cedula = ced
                    _ensure_person_pseudonyms(p, ledger)
                    reg.add_or_update_person(p)
                elif cid or ced:
                    dyn_id = cid if cid else f"external:pqr_{ced}"
                    p = reg.persons.get(dyn_id)
                    if p is None:
                        p = PersonEntity(
                            canonical_id=dyn_id,
                            entity_id=dyn_id,
                            full_name="Cliente PQR",
                            short_name="Cliente PQR",
                            given_name="Cliente",
                            surname1="PQR",
                            gender="F",
                            cedula=ced,
                        )
                        _ensure_person_pseudonyms(p, ledger)
                        reg.add_or_update_person(p)
                automaton = KnownAutomaton(reg)
            if p is not None:
                _ensure_person_pseudonyms(p, ledger)
                if cols.get("cedula"):
                    p.record_hit(rid, cols["cedula"], "cedula")
                    cols["cedula"] = p.pseudonyms["cedula"]
                else:
                    p.record_hit(rid, None, "name")
            if cols.get("agente"):
                orig_ag = cols["agente"].strip()
                ag_cands = reg.by_handle.get(norm(orig_ag), [])
                if ag_cands:
                    ag = ag_cands[0]
                    _ensure_person_pseudonyms(ag, ledger)
                    ag.record_hit(rid, orig_ag, "handle")
                    cols["agente"] = ag.pseudonyms["handle"]
                elif orig_ag:
                    ag_cid = f"external:agent_{norm(orig_ag)}"
                    ag = reg.persons.get(ag_cid)
                    if ag is None:
                        words = [w.capitalize() for w in re.split(r"[._\-]+", orig_ag) if w]
                        ag_name = " ".join(words) if len(words) >= 2 else f"{orig_ag.capitalize()} Soporte"
                        g, s1, s2, short = split_colombian_name(ag_name)
                        ag = PersonEntity(
                            canonical_id=ag_cid,
                            entity_id=ag_cid,
                            is_employee=True,
                            full_name=ag_name,
                            short_name=short,
                            given_name=g,
                            surname1=s1,
                            surname2=s2,
                            gender=infer_gender(ag_name),
                            handles={orig_ag},
                        )
                        _ensure_person_pseudonyms(ag, ledger)
                        reg.add_or_update_person(ag)
                        automaton = KnownAutomaton(reg)
                    ag.record_hit(rid, orig_ag, "handle")
                    cols["agente"] = ag.pseudonyms["handle"]
            for txt_k in ("descripcion", "respuesta"):
                if cols.get(txt_k):
                    ctx = ResolutionContext(
                        record_id=rid,
                        source_type="tables",
                        channel_or_table="pqrs",
                        field_name=f"columns.{txt_k}",
                        row_person=p,
                    )
                    spans = resolve_text_spans(cols[txt_k], reg, automaton, ledger, ctx)
                    cols[txt_k] = apply_resolved_spans(cols[txt_k], spans, rid)
                    cols[txt_k] = generalize_quasi_in_text(cols[txt_k], rid, "tables", person=p)
                    automaton = KnownAutomaton(reg)

        elif tname == "transacciones":
            cid = cols.get("cliente_id", "").strip()
            p = reg.persons.get(cid) or reg.alias_ids.get(cid)
            if cols.get("descripcion"):
                ctx = ResolutionContext(
                    record_id=rid,
                    source_type="tables",
                    channel_or_table="transacciones",
                    field_name="columns.descripcion",
                    row_person=p,
                )
                spans = resolve_text_spans(cols["descripcion"], reg, automaton, ledger, ctx)
                cols["descripcion"] = apply_resolved_spans(cols["descripcion"], spans, rid)
                automaton = KnownAutomaton(reg)


def _anonymize_chats_in_place(
    chats: list[dict[str, Any]],
    reg: EntityRegistry,
    automaton: KnownAutomaton,
    ledger: PseudonymLedger,
) -> None:
    # 1. Group by thread_id to identify thread_client and thread_agents
    thread_clients: dict[str, PersonEntity] = {}
    thread_agents: dict[str, list[PersonEntity]] = {}

    for c in chats:
        rid = str(c.get("id", ""))
        tid = str(c.get("thread_id", ""))
        if tid in reg.by_thread_client and tid not in thread_clients:
            tc_known = reg.persons.get(reg.by_thread_client[tid])
            if tc_known is not None:
                thread_clients[tid] = tc_known
        if tid in reg.by_thread_agents and tid not in thread_agents:
            thread_agents[tid] = [
                reg.persons[aid] for aid in reg.by_thread_agents[tid] if aid in reg.persons
            ]

        uid = str(c.get("_uid", ""))
        p = reg.by_uid.get(uid) if uid else None
        if p is None and rid in reg.by_record_author:
            p_rec = reg.persons.get(reg.by_record_author[rid])
            orig_auth_n = norm(str(c.get("author", "")))
            if p_rec is not None and (
                not orig_auth_n
                or orig_auth_n == "usuario eliminado"
                or orig_auth_n == norm(p_rec.short_name)
                or orig_auth_n == norm(p_rec.full_name)
            ):
                p = p_rec
        if p is None and c.get("author"):
            auth_n = norm(str(c["author"]))
            cands = reg.by_short_name_norm.get(auth_n) or reg.by_full_name_norm.get(auth_n)
            if cands and len(cands) == 1:
                p = cands[0]
        if p is not None:
            if p.is_employee:
                ag_list = thread_agents.setdefault(tid, [])
                if p not in ag_list:
                    ag_list.append(p)
            else:
                thread_clients[tid] = p

    # 2. Process each chat message
    for c in chats:
        rid = str(c.get("id", ""))
        tid = str(c.get("thread_id", ""))
        chan = tid.split(":", 1)[0] if ":" in tid else ""
        uid = str(c.get("_uid", ""))
        orig_author = str(c.get("author", "")).strip()
        txt_raw = str(c.get("text", ""))

        author_p = reg.by_uid.get(uid) if uid else None
        if author_p is None and rid in reg.by_record_author:
            p_rec = reg.persons.get(reg.by_record_author[rid])
            orig_auth_n = norm(orig_author)
            if p_rec is not None and (
                not orig_auth_n
                or orig_auth_n == "usuario eliminado"
                or orig_auth_n == norm(p_rec.short_name)
                or orig_auth_n == norm(p_rec.full_name)
            ):
                author_p = p_rec

        if author_p is None and orig_author and norm(orig_author) not in (
            "kipi",
            "kipu bot",
            "usuario eliminado",
            "cliente",
            "usuario",
            "agente",
            "soporte",
            "sistema",
        ):
            auth_n = norm(orig_author)
            cands = list(reg.by_short_name_norm.get(auth_n, []) or reg.by_full_name_norm.get(auth_n, []))
            # Check if message text has a discriminating identifier (for homonym authors in `/v1/anonymize`)
            dyn_p = _get_or_register_dynamic_person(
                orig_author,
                reg,
                ledger,
                local_text=txt_raw,
                record_id=rid,
            )
            if dyn_p is not None and (not cands or dyn_p not in cands):
                author_p = dyn_p
                automaton = KnownAutomaton(reg)
            elif cands:
                tc_cand = thread_clients.get(tid)
                ag_cands = [ag for ag in thread_agents.get(tid, []) if ag in cands]
                if tc_cand in cands and len(cands) > 1 and not ag_cands:
                    author_p = tc_cand
                elif ag_cands and len(cands) > 1 and tc_cand not in cands:
                    author_p = ag_cands[0]
                elif dyn_p is not None:
                    author_p = dyn_p
                else:
                    author_p = cands[0]
            elif dyn_p is not None:
                author_p = dyn_p
                automaton = KnownAutomaton(reg)
            else:
                g, s1, s2, short = split_colombian_name(orig_author)
                cid = f"external:{norm(short)}"
                author_p = reg.persons.get(cid)
                if author_p is None:
                    author_p = PersonEntity(
                        canonical_id=cid,
                        entity_id=cid,
                        full_name=orig_author,
                        short_name=short,
                        given_name=g,
                        surname1=s1,
                        surname2=s2,
                        gender=infer_gender(orig_author),
                    )
                    _ensure_person_pseudonyms(author_p, ledger)
                    reg.add_or_update_person(author_p)
                    automaton = KnownAutomaton(reg)

        if orig_author in ("Kipi", "Kipu Bot"):
            kept_kipi = reg.kept.get("kept_kipi")
            if kept_kipi is not None:
                kept_kipi.record_hit(rid, orig_author)
        elif author_p is not None:
            _ensure_person_pseudonyms(author_p, ledger)
            if orig_author and orig_author != "Usuario eliminado":
                author_p.record_hit(rid, orig_author, "name")
            else:
                author_p.record_hit(rid, None, "name")
            c["author"] = author_p.pseudonyms["short_name"]

        if author_p is not None and not author_p.is_employee:
            tc = author_p
            thread_clients[tid] = author_p
        else:
            tc = thread_clients.get(tid)

        ctx = ResolutionContext(
            record_id=rid,
            source_type="chats",
            channel_or_table=chan,
            author_entity=author_p,
            thread_client=tc,
            thread_agents=thread_agents.get(tid, []),
        )

        txt = str(c.get("text", ""))
        if txt:
            ctx.llm_spans = get_cached_llm_spans(txt)
            spans = resolve_text_spans(txt, reg, automaton, ledger, ctx)
            automaton = KnownAutomaton(reg)
            # If an unseen client self-introduced in text and author was a generic placeholder or matched
            if tc is None:
                for sp in spans:
                    if sp.person is not None and not sp.person.is_employee:
                        tc = sp.person
                        thread_clients[tid] = tc
                        ctx.thread_client = tc
                        break
            txt_out = apply_resolved_spans(txt, spans, rid)
            txt_out = generalize_quasi_in_text(txt_out, rid, "chats", person=tc or author_p)
            c["text"] = txt_out

        atts = c.get("attachments")
        if isinstance(atts, list):
            for att in atts:
                if isinstance(att, dict) and att.get("filename"):
                    fn = str(att["filename"])
                    fn_spans = resolve_text_spans(fn, reg, automaton, ledger, ctx)
                    att["filename"] = apply_resolved_spans(fn, fn_spans, rid)

        # Strip internal keys so schema is strictly [id, thread_id, author, ts, text, attachments]
        for k in list(c.keys()):
            if k.startswith("_"):
                del c[k]


def _anonymize_docs_in_place(
    docs: list[dict[str, Any]],
    reg: EntityRegistry,
    automaton: KnownAutomaton,
    ledger: PseudonymLedger,
) -> None:
    for d in docs:
        rid = str(d.get("id", ""))
        participants = _collect_doc_participants(d, reg)
        ctx = ResolutionContext(
            record_id=rid,
            source_type="docs",
            doc_participants=participants,
        )

        body = str(d.get("body", ""))
        if body:
            ctx.llm_spans = get_cached_llm_spans(body)
            spans = resolve_text_spans(body, reg, automaton, ledger, ctx)
            automaton = KnownAutomaton(reg)
            for sp in spans:
                if sp.person is not None and sp.person not in ctx.doc_participants:
                    ctx.doc_participants.append(sp.person)
            body_out = apply_resolved_spans(body, spans, rid)
            body_out = generalize_quasi_in_text(body_out, rid, "docs")
            d["body"] = body_out

        meta = d.get("metadata")
        if isinstance(meta, dict):
            for mk, mv in list(meta.items()):
                if isinstance(mv, str) and mv:
                    spans = resolve_text_spans(mv, reg, automaton, ledger, ctx)
                    automaton = KnownAutomaton(reg)
                    meta[mk] = apply_resolved_spans(mv, spans, rid)

        for field_k in ("title", "filename"):
            val = str(d.get(field_k, ""))
            if val:
                spans = resolve_text_spans(val, reg, automaton, ledger, ctx)
                d[field_k] = apply_resolved_spans(val, spans, rid)


def run_full_pipeline(
    bundle: CorpusBundle,
    output_dir: Path | None = None,
    ledger_path: Path | None = None,
    snapshot_path: Path | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Execute the complete deterministic + guard pipeline on `bundle` and write
    `chats.jsonl`, `docs.jsonl`, `tables.jsonl`, and `entities.json` to `output_dir`.
    """
    t0 = time.perf_counter()
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    l_path = ledger_path or (STATE_DIR / "entity_ledger.json")
    s_path = snapshot_path or (
        (l_path.parent / "registry_snapshot.json")
        if ledger_path is not None
        else (STATE_DIR / "registry_snapshot.json")
    )

    ledger = PseudonymLedger(ledger_path=l_path)
    existing_reg = EntityRegistry(ledger=ledger)
    if s_path.exists():
        existing_reg.load_snapshot(s_path)

    if use_llm:
        _prefill_llm_cache(bundle)

    reg = build_and_link_registry(bundle, registry=existing_reg)

    # Register all real identifiers so no generated pseudonym ever collides with a real value
    ledger.register_real_corpus_identifiers(
        cedulas={p.cedula for p in reg.persons.values() if p.cedula},
        phones={p.phone for p in reg.persons.values() if p.phone},
        emails={p.email for p in reg.persons.values() if p.email},
        addresses={p.address for p in reg.persons.values() if p.address},
        births={p.birth_iso for p in reg.persons.values() if p.birth_iso},
    )

    # Clear pre-render hit sets so `originals` and `records` reflect strictly what was rendered in output
    for cid in sorted(reg.persons.keys()):
        p = reg.persons[cid]
        _ensure_person_pseudonyms(p, ledger)
        p.originals_found.clear()
        p.records_found.clear()
        p.attrs_used.clear()
    for k in reg.kept.values():
        k.originals_found.clear()
        k.records_found.clear()

    automaton = KnownAutomaton(reg)

    chats = copy.deepcopy(bundle.chats)
    docs = copy.deepcopy(bundle.docs)
    tables = copy.deepcopy(bundle.tables)

    _anonymize_tables_in_place(tables, reg, automaton, ledger)
    # Rebuild automaton in case any dynamic table entity was added
    automaton = KnownAutomaton(reg)
    _anonymize_chats_in_place(chats, reg, automaton, ledger)
    automaton = KnownAutomaton(reg)
    _anonymize_docs_in_place(docs, reg, automaton, ledger)

    all_records = chats + docs + tables
    intercepts = run_final_guard(all_records, reg)
    entities_list = export_entities(reg, all_records, for_anonymize=False)

    # Persist state
    ledger.save()
    reg.save_snapshot(s_path)

    # Write output files
    (out_dir / "chats.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in chats) + "\n",
        encoding="utf-8",
    )
    (out_dir / "docs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in docs) + "\n",
        encoding="utf-8",
    )
    (out_dir / "tables.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in tables) + "\n",
        encoding="utf-8",
    )
    (out_dir / "entities.json").write_text(
        json.dumps({"entities": entities_list}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    elapsed = round(time.perf_counter() - t0, 3)
    t_in, t_out = get_and_reset_llm_tokens()
    cost_usd = get_and_reset_llm_cost()
    log_step(
        stage="write",
        record="ALL",
        mode="DET",
        model="NONE",
        latency_ms=elapsed * 1000.0,
        tokens=t_in + t_out,
        cost_usd=cost_usd,
        guard="INTERCEPT" if intercepts > 0 else "PASS",
        status="PASS",
    )
    return {
        "records": {
            "chats": len(chats),
            "docs": len(docs),
            "tables": len(tables),
        },
        "entities_count": len(entities_list),
        "guard_intercepts": intercepts,
        "tokens_in": t_in,
        "tokens_out": t_out,
        "cost_usd": cost_usd,
        "seconds": elapsed,
    }


class LiveAnonymizer:
    """
    Stateful in-memory handler for `POST /v1/anonymize` that works both in cold start
    (including `sample/` and `fuentes/`) and after `/v1/run`.
    """

    def __init__(self) -> None:
        self.ledger_path = STATE_DIR / "entity_ledger.json"
        self.snapshot_path = STATE_DIR / "registry_snapshot.json"
        self.ledger = PseudonymLedger(ledger_path=self.ledger_path)
        self.reg: EntityRegistry | None = None
        self._last_snapshot_mtime: float = 0.0

    def _ensure_loaded(self) -> EntityRegistry:
        mtime = self.snapshot_path.stat().st_mtime if self.snapshot_path.exists() else 0.0
        if self.reg is None or mtime > self._last_snapshot_mtime:
            self.ledger.load()
            if self.snapshot_path.exists():
                self.reg = EntityRegistry(ledger=self.ledger)
                self.reg.load_snapshot(self.snapshot_path)
                self._last_snapshot_mtime = mtime
            elif Path("fuentes").exists() and (Path("fuentes") / "record_index.json").exists():
                b = load_from_local_dir(Path("fuentes"))
                self.reg = build_and_link_registry(b, registry=EntityRegistry(ledger=self.ledger))
            else:
                self.reg = EntityRegistry(ledger=self.ledger)

            for cid in sorted(self.reg.persons.keys()):
                _ensure_person_pseudonyms(self.reg.persons[cid], self.ledger)
        return self.reg

    def anonymize(self, source: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        base_reg = self._ensure_loaded()
        # Clone registry per request so `originals_found` and `records_found` are scoped to this batch
        batch_reg = copy.deepcopy(base_reg)
        batch_reg.ledger = self.ledger
        for cid in sorted(batch_reg.persons.keys()):
            p = batch_reg.persons[cid]
            _ensure_person_pseudonyms(p, self.ledger)
            p.originals_found.clear()
            p.records_found.clear()
            p.attrs_used.clear()
        for k in batch_reg.kept.values():
            k.originals_found.clear()
            k.records_found.clear()

        recs_copy = copy.deepcopy(records)
        automaton = KnownAutomaton(batch_reg)

        if source == "tables":
            _anonymize_tables_in_place(recs_copy, batch_reg, automaton, self.ledger)
        elif source == "chats":
            _anonymize_chats_in_place(recs_copy, batch_reg, automaton, self.ledger)
        elif source == "docs":
            _anonymize_docs_in_place(recs_copy, batch_reg, automaton, self.ledger)

        run_final_guard(recs_copy, batch_reg)
        entities_out = export_entities(batch_reg, recs_copy, for_anonymize=True)

        # Keep newly created dynamic persons in `base_reg` in memory for subsequent `/v1/anonymize` calls
        for cid, p in batch_reg.persons.items():
            if cid not in base_reg.persons:
                base_reg.add_or_update_person(copy.deepcopy(p))

        return {
            "records": recs_copy,
            "entities": entities_out,
        }


live_anonymizer = LiveAnonymizer()
