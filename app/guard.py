"""Final safety sweep (`guard.py`) using exact `contiene()` and `digitos_en()` from `validar.mjs`."""
from __future__ import annotations

import re
import time
from typing import Any

from app.normalize import (
    contiene,
    es_nombre_suelto,
    find_digit_spans_in_text,
    norm,
    norm_with_offsets,
    solo_digitos,
    texto_de_registro,
)
from app.pseudonym import format_cedula_dots
from app.registry import EntityRegistry, PersonEntity
from app.telemetry import log_step


def _replace_in_str(text: str, orig: str, rep: str) -> tuple[str, bool]:
    if not text or not orig or not rep:
        return text, False

    if orig in text:
        return text.replace(orig, rep), True

    m = re.search(re.escape(orig), text, flags=re.I)
    if m:
        return re.sub(re.escape(orig), rep, text, flags=re.I), True

    # Check if numeric (cedula, phone, account)
    digits = solo_digitos(orig)
    if digits and len(digits) >= 6 and len(digits) >= 0.6 * len(orig) and not any(c.isalpha() for c in orig):
        spans = find_digit_spans_in_text(text, digits)
        if spans:
            out = text
            for st, en in reversed(spans):
                surf = out[st:en]
                rep_d = format_cedula_dots(solo_digitos(rep)) if "." in surf else rep
                out = out[:st] + rep_d + out[en:]
            return out, True

    # Normalized offset match (catches confusables, invisible characters, or missing accents)
    n_orig = norm(orig)
    if len(n_orig) >= 4:
        n_txt, starts, ends = norm_with_offsets(text)
        # Also check concatenated words without spaces (`palabras.join("[ESPACIO]*")`)
        words = n_orig.split()
        if len(words) >= 2 and all(len(w) >= 3 for w in words):
            pat = r"(?<![a-z0-9])" + r"\s*".join(re.escape(w) for w in words) + r"(?![a-z0-9])"
        else:
            pat = r"(?<![a-z0-9])" + re.escape(n_orig) + r"(?![a-z0-9])"
        matches = list(re.finditer(pat, n_txt))
        if matches:
            out = text
            for mm in reversed(matches):
                st_orig = starts[mm.start()]
                en_orig = ends[mm.end() - 1]
                out = out[:st_orig] + rep + out[en_orig:]
            return out, True

    return text, False


def _scrub_record_fields(rec: dict[str, Any], orig: str, rep: str) -> bool:
    changed = False
    for k, v in list(rec.items()):
        if k in ("id", "table", "format", "ts", "thread_id") or k.startswith("_"):
            continue
        if isinstance(v, str):
            new_v, ch = _replace_in_str(v, orig, rep)
            if ch:
                rec[k] = new_v
                changed = True
        elif isinstance(v, dict):
            for sk, sv in list(v.items()):
                if sk in ("cliente_id", "tx_id", "radicado", "id"):
                    continue
                if isinstance(sv, str):
                    new_sv, ch = _replace_in_str(sv, orig, rep)
                    if ch:
                        v[sk] = new_sv
                        changed = True
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for ik, iv in list(item.items()):
                        if isinstance(iv, str):
                            new_iv, ch = _replace_in_str(iv, orig, rep)
                            if ch:
                                item[ik] = new_iv
                                changed = True
    return changed


def run_final_guard(
    records: list[dict[str, Any]],
    reg: EntityRegistry,
) -> int:
    """
    Sweep all `records` with `contiene()` from `validar.mjs` against all registered
    person originals and structured attributes, intercepting any residual leak.
    Returns the number of intercepted residual occurrences.
    """
    t0 = time.perf_counter()
    kept_names = [
        v
        for k in reg.kept.values()
        for v in k.variants
        if len(v) >= 4 and any(c.isalpha() for c in v)
    ]

    # Build list of (orig, rep, person) to check
    check_items: list[tuple[str, str, PersonEntity]] = []
    for p in reg.persons.values():
        ps = p.pseudonyms
        if not ps:
            continue
        short_rep = ps.get("short_name") or ps.get("name") or ""
        full_rep = ps.get("full_name") or short_rep

        pairs: list[tuple[str, str]] = []
        if p.full_name and full_rep:
            pairs.append((p.full_name, full_rep))
        if p.short_name and short_rep:
            pairs.append((p.short_name, short_rep))
        if p.email and ps.get("email"):
            pairs.append((p.email, ps["email"]))
        if p.cedula and ps.get("cedula"):
            pairs.append((p.cedula, ps["cedula"]))
            pairs.append((format_cedula_dots(p.cedula), format_cedula_dots(ps["cedula"])))
        if p.phone and ps.get("phone"):
            pairs.append((p.phone, ps["phone"]))
        if p.address and ps.get("address"):
            pairs.append((p.address, ps["address"]))
        if p.birth_iso and ps.get("birth_iso"):
            pairs.append((p.birth_iso, ps["birth_iso"]))
        if p.plate and ps.get("plate"):
            pairs.append((p.plate, ps["plate"]))
        if p.passport and ps.get("passport"):
            pairs.append((p.passport, ps["passport"]))
        if p.account and ps.get("account"):
            pairs.append((p.account, ps["account"]))
        if p.given_name and p.surname1 and ps.get("filename_token"):
            fn_tok = f"{norm(p.given_name).replace(' ', '_')}_{norm(p.surname1)}"
            pairs.append((fn_tok, ps["filename_token"]))
        if p.given_name and p.surname1 and ps.get("slug_hyphen"):
            sl_tok = f"{norm(p.given_name).replace(' ', '-')}-{norm(p.surname1)}"
            pairs.append((sl_tok, ps["slug_hyphen"]))
        for orig_s in list(p.originals_found):
            if len(orig_s) >= 4 and not es_nombre_suelto(orig_s):
                pairs.append((orig_s, short_rep))

        seen_origs: set[str] = set()
        for orig_val, rep_val in pairs:
            o = orig_val.strip()
            if not o or len(o) < 4 or o in seen_origs:
                continue
            if len(o) == 4 and o.isdigit():
                continue
            if es_nombre_suelto(o):
                continue
            if any(contiene(k, o) for k in kept_names):
                continue
            seen_origs.add(o)
            check_items.append((o, rep_val, p))

    intercepts = 0
    rec_texts: list[tuple[dict[str, Any], str, str]] = []
    for rec in records:
        rid = str(rec.get("id", ""))
        rec_txt = texto_de_registro(rec)
        if rec_txt:
            rec_texts.append((rec, rid, rec_txt))

    entero = "\n".join(rt for _, _, rt in rec_texts)
    active_items = [
        (orig_val, rep_val, p)
        for orig_val, rep_val, p in check_items
        if contiene(entero, orig_val)
    ]
    if not active_items:
        return 0

    for rec, rid, rec_txt in rec_texts:
        for orig_val, rep_val, p in active_items:
            if contiene(rec_txt, orig_val):
                if _scrub_record_fields(rec, orig_val, rep_val):
                    p.record_hit(rid, orig_val)
                    rec_txt = texto_de_registro(rec)
                    intercepts += 1
                    log_step(
                        stage="guard",
                        record=rid,
                        mode="DET",
                        model="NONE",
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        guard="INTERCEPT",
                        status="PASS",
                    )

    return intercepts
