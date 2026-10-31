"""Context-aware entity resolution and disambiguation of candidate spans."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from app.detect.known import CandidateSpan, KnownAutomaton
from app.detect.patterns import scan_patterns
from app.link import split_colombian_name
from app.normalize import decode_spelled_email, norm, solo_digitos
from app.pseudonym import PseudonymLedger, infer_gender
from app.registry import EntityRegistry, KeptEntity, PersonEntity
from app.render import (
    ResolvedSpan,
    render_account_surface,
    render_birth_surface,
    render_cedula_surface,
    render_email_surface,
    render_handle_surface,
    render_name_surface,
    render_phone_surface,
    render_plate_surface,
)

GENERIC_CORPORATE_EMAIL_LOCALS = {
    "soporte",
    "info",
    "legal",
    "notificaciones",
    "ayuda",
    "contacto",
    "seguridad",
    "cobranza",
    "cartera",
    "privacidad",
    "cumplimiento",
    "talento",
    "riesgo",
    "operaciones",
    "alertas",
    "no-reply",
    "noreply",
}

NON_PERSON_GIVEN_WORDS = {
    "julio",
    "agosto",
    "abril",
    "mayo",
    "junio",
    "marzo",
    "enero",
    "febrero",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
}

RX_SURNAME_TITLE_BEFORE = re.compile(
    r"\b(?:se[ñn]or(?:a|ita)?|sr\.?|sra\.?|agente|doctor(?:a)?|dr\.?|dra\.?|don|do[ñn]a|profe(?:sor(?:a)?)?|ingenier[oa]|abogad[oa]|analista|coordinador(?:a)?|gerente|director(?:a)?|l[ií]der|apellido)\s+$",
    re.I,
)

RX_PLACE_PREP_BEFORE = re.compile(
    r"\b(?:en|desde|hacia|hasta|por|sucursal|sede|oficina|cajero\s+de|estaci[oó]n|parque\s+de|plaza|barrio|municipio\s+de|ciudad\s+de|v[ií]a|autopista|avenida|calle|carrera|banco|cuenta\s+de|transferencia\s+a)\s+$",
    re.I,
)

RX_GIVEN_CUE_BEFORE = re.compile(
    r"(?:^|[.!?¡¿,\n—\-]\s*|\b(?:hola|buenas(?:\s+tardes|\s+d[ií]as|\s+noches)?|buen\s+d[ií]a|gracias|listo|hecho|tranquil[ao]|soy|habla|atiende|saluda|por\s+ac[aá]|con|para|de|a|y|ojo\s+con|hermano|hermana|esposo|esposa|mam[aá]|pap[aá]|hijo|hija|t[ií]o|t[ií]a|primo|prima)\s+)$",
    re.I,
)

RX_UNSEEN_INTRO = re.compile(
    r"(?:\b(?:me\s+llamo|mi\s+nombre\s+es|soy|habla|te\s+atiende|preparado\s+por|elabor[oó]\s*:?|revisado\s+por|reviewed\s+by|auditora?\s*:?|responsable\s*:?|aprueba\s*:?|familiar\s+de|a\s+nombre\s+de|de\s+parte\s+de|en\s+mi\s+familia)\s+)"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})"
)

RX_UNSEEN_PAREN_ID = re.compile(
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})\s*\(((?:CL|KP)-\d+)\)"
)

STOP_INTRO_WORDS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "de",
    "del",
    "en",
    "por",
    "para",
    "con",
    "sin",
    "que",
    "también",
    "tambien",
    "tiene",
    "kipu",
    "kipi",
    "soporte",
    "agente",
    "cliente",
    "contadora",
    "contador",
    "abogada",
    "abogado",
    "profesional",
}


@dataclass
class ResolutionContext:
    record_id: str
    source_type: str  # "chats", "docs", "tables"
    channel_or_table: str = ""
    field_name: str = ""
    author_entity: PersonEntity | None = None
    thread_client: PersonEntity | None = None
    thread_agents: list[PersonEntity] = field(default_factory=list)
    doc_participants: list[PersonEntity] = field(default_factory=list)
    row_person: PersonEntity | None = None
    llm_spans: list[dict[str, Any]] | None = None


def _ensure_person_pseudonyms(p: PersonEntity, ledger: PseudonymLedger) -> None:
    first_handle = next(iter(sorted(p.handles)), "") if p.handles else ""
    bundle = ledger.get_or_create(
        canonical_id=p.canonical_id,
        orig_name=p.full_name or p.short_name or p.given_name or "Persona",
        gender=p.gender or None,
        orig_email=p.email,
        orig_phone=p.phone,
        orig_cedula=p.cedula,
        orig_address=p.address,
        orig_birth_iso=p.birth_iso,
        orig_plate=p.plate,
        orig_passport=p.passport,
        orig_account=p.account,
        orig_handle=first_handle,
        is_employee=p.is_employee,
    )
    p.pseudonyms.update(bundle)


def _get_or_register_dynamic_person(
    name_surface: str,
    reg: EntityRegistry,
    ledger: PseudonymLedger,
    explicit_id: str = "",
    is_employee: bool = False,
) -> PersonEntity | None:
    tokens = [w for w in name_surface.strip().split() if w.lower() not in STOP_INTRO_WORDS]
    if len(tokens) < 2:
        return None
    clean_name = " ".join(tokens)
    n_name = norm(clean_name)
    if not n_name or n_name in reg.kept or n_name in ("kipu pay", "kipi"):
        return None

    if explicit_id and explicit_id in reg.persons:
        return reg.persons[explicit_id]

    cands = reg.by_full_name_norm.get(n_name) or reg.by_short_name_norm.get(n_name)
    if cands:
        return cands[0]

    given, s1, s2, short = split_colombian_name(clean_name)
    short_n = norm(short)
    if short_n in reg.by_short_name_norm:
        return reg.by_short_name_norm[short_n][0]

    cid = explicit_id if explicit_id else f"external:{short_n}"
    if cid in reg.persons:
        return reg.persons[cid]

    p = PersonEntity(
        canonical_id=cid,
        entity_id=cid,
        full_name=clean_name,
        short_name=short,
        given_name=given,
        surname1=s1,
        surname2=s2,
        gender=infer_gender(clean_name),
        is_employee=is_employee or cid.startswith("KP-"),
    )
    _ensure_person_pseudonyms(p, ledger)
    reg.add_or_update_person(p)
    return p


def _scan_unseen_names(
    text: str,
    reg: EntityRegistry,
    ledger: PseudonymLedger,
    ctx: ResolutionContext,
) -> list[CandidateSpan]:
    spans: list[CandidateSpan] = []
    for m in RX_UNSEEN_PAREN_ID.finditer(text):
        raw_name = m.group(1)
        explicit_id = m.group(2)
        p = _get_or_register_dynamic_person(raw_name, reg, ledger, explicit_id=explicit_id)
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=m.end(1),
                    surface=raw_name,
                    kind="short_name" if len(raw_name.split()) <= 2 else "full_name",
                    candidates=[p],
                    priority=91,
                )
            )

    for m in RX_UNSEEN_INTRO.finditer(text):
        raw_name = m.group(1)
        # Trim trailing stop words if regex captured e.g. "Nohora Salazar también"
        words = raw_name.split()
        kept_words: list[str] = []
        for w in words:
            if w.lower() in STOP_INTRO_WORDS or not w[0].isupper():
                break
            kept_words.append(w)
        if len(kept_words) < 2:
            continue
        trimmed = " ".join(kept_words)
        end_pos = m.start(1) + len(trimmed)
        p = _get_or_register_dynamic_person(trimmed, reg, ledger)
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=end_pos,
                    surface=text[m.start(1) : end_pos],
                    kind="short_name" if len(kept_words) <= 2 else "full_name",
                    candidates=[p],
                    priority=89,
                )
            )

    if ctx.llm_spans:
        for item in ctx.llm_spans:
            surf = str(item.get("surface", "")).strip()
            stype = str(item.get("type", "person")).strip()
            if not surf or stype != "person" or len(surf) < 3:
                continue
            for m in re.finditer(re.escape(surf), text):
                p = _get_or_register_dynamic_person(surf, reg, ledger)
                if p is not None:
                    spans.append(
                        CandidateSpan(
                            start=m.start(),
                            end=m.end(),
                            surface=surf,
                            kind="short_name" if len(surf.split()) <= 2 else "full_name",
                            candidates=[p],
                            priority=87,
                        )
                    )
    return spans


def _disambiguate_person_candidates(
    span: CandidateSpan,
    text: str,
    reg: EntityRegistry,
    ctx: ResolutionContext,
    already_picked_on_line: dict[tuple[int, str], list[tuple[int, PersonEntity]]],
) -> PersonEntity | None:
    cands = [c for c in span.candidates if c is not None]
    if not cands:
        return None

    # Find line boundaries around span
    line_start = text.rfind("\n", 0, span.start) + 1
    line_end = text.find("\n", span.end)
    if line_end == -1:
        line_end = len(text)
    line_text = text[line_start:line_end]
    line_norm = norm(line_text)
    before_text = text[max(0, span.start - 45) : span.start]
    after_text = text[span.end : min(len(text), span.end + 55)]
    local_window = text[max(0, span.start - 55) : min(len(text), span.end + 65)]

    # Special rules for single-word `surname1` and `given_name`
    if span.kind == "surname1":
        if not span.surface or not (span.surface[0].isupper() or span.surface.isupper()):
            return None
        # If Preceded by place/bank preposition, reject as person
        if RX_PLACE_PREP_BEFORE.search(before_text):
            return None
        # Must have a title/role cue before it (e.g., "señor Santander", "agente Robledo", "agente Zapata")
        if not RX_SURNAME_TITLE_BEFORE.search(before_text):
            return None
        if re.search(r"\bagente\s+$", before_text, re.I):
            emp_cands = [c for c in cands if c.is_employee]
            if len(emp_cands) == 1:
                return emp_cands[0]

    if span.kind == "given_name":
        if not span.surface or not span.surface[0].isupper():
            return None
        gn = norm(span.surface)
        if gn in NON_PERSON_GIVEN_WORDS:
            return None
        if gn == "paz" and re.match(r"^\s*y\s+salvo\b", after_text, re.I):
            return None
        if gn == "maria" and ('"' in before_text[-3:] or '"' in after_text[:3] or "campaña" in line_norm):
            return None
        # Check if followed by "(agente de <Ciudad>)" -> e.g. "Fernanda (agente de Cali)"
        m_ag_city = re.match(r"^\s*\(\s*agente\s+de\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s*\)", after_text, re.I)
        if m_ag_city:
            target_city = norm(m_ag_city.group(1))
            city_emps = [c for c in cands if c.is_employee and norm(c.ciudad) == target_city]
            if len(city_emps) == 1:
                return city_emps[0]

        # Prefer active participants in this record/thread/document
        active_pool: list[PersonEntity] = []
        if ctx.thread_client:
            active_pool.append(ctx.thread_client)
        if ctx.author_entity and ctx.author_entity not in active_pool:
            active_pool.append(ctx.author_entity)
        for ag in ctx.thread_agents:
            if ag not in active_pool:
                active_pool.append(ag)
        if ctx.row_person and ctx.row_person not in active_pool:
            active_pool.append(ctx.row_person)
        for dp in ctx.doc_participants:
            if dp not in active_pool:
                active_pool.append(dp)

        # If greeting at start of agent message ("Hola Camila!"), prefer thread_client
        if re.search(r"\b(?:hola|buenas|estimad[ao]|querid[ao])\s+$", before_text, re.I) and ctx.thread_client in cands:
            return ctx.thread_client
        # If self-intro ("soy Yesenia de soporte"), prefer author_entity
        if re.search(r"\b(?:soy|habla|saluda|atiende)\s+$", before_text, re.I) and ctx.author_entity in cands:
            return ctx.author_entity

        active_matches = [p for p in active_pool if p in cands]
        if active_matches:
            return active_matches[0]

        # Otherwise only allow if there is a clear person cue right before
        if not RX_GIVEN_CUE_BEFORE.search(before_text):
            return None
        if len(cands) == 1:
            return cands[0]
        return None

    if len(cands) == 1:
        return cands[0]

    # Disambiguate multiple candidates (homonyms!)
    # 1. Check immediate local window / line for explicit IDs (CL-..., KP-..., PQR-..., S-26...)
    for m_id in re.finditer(r"\b((?:CL|KP)-\d+|PQR-\d{4}-\d{4}|S-\d{4}-\d{2}|INC-\d{4}-\d{3}|\d{4}-\d{4})\b", line_text):
        token_id = m_id.group(1)
        for c in cands:
            if (
                token_id == c.canonical_id
                or token_id in c.pqrs
                or token_id in c.solicitudes
                or token_id in c.obligaciones
                or token_id in c.incidentes
            ):
                # Special check: if the line has TWO mentions of the same homonym name (e.g. chat c710e9b6)
                key = (line_start, norm(span.surface))
                prev_entries = already_picked_on_line.get(key, [])
                for prev_st, prev_p in prev_entries:
                    if prev_st == span.start:
                        return prev_p
                prev_used = [prev_p for _, prev_p in prev_entries]
                if c not in prev_used:
                    already_picked_on_line.setdefault(key, []).append((span.start, c))
                    return c
                else:
                    others = [x for x in cands if x is not c]
                    if others:
                        already_picked_on_line.setdefault(key, []).append((span.start, others[0]))
                        return others[0]

    # 2. Check line for cedula, email, phone, plate, or last-4 digits of cedula/phone
    for c in cands:
        if c.cedula and c.cedula in line_text.replace(".", ""):
            return c
        if c.email and c.email.lower() in line_text.lower():
            return c
        if c.phone and c.phone in solo_digitos(line_text):
            return c
        if c.plate and norm(c.plate) in line_norm:
            return c

    m_last4 = re.search(r"(?:termina(?:da|do)?\s+en|ending\s+in)\s+(\d{4})\b", line_text, re.I)
    if m_last4:
        d4 = m_last4.group(1)
        for c in cands:
            if (c.cedula and c.cedula.endswith(d4)) or (c.phone and c.phone.endswith(d4)):
                return c

    # 3. Check bot handoff / employee role cues in local window
    if re.search(
        r"\b(?:te\s+atiende|agente\s+de|equipo\s+de\s+soporte|coordinador\s+de\s+fraude|analista\s+de\s+datos|arm[oó]\s+el\s+dashboard|organigrama)\b",
        local_window,
        re.I,
    ):
        emp_cands = [c for c in cands if c.is_employee]
        if len(emp_cands) == 1:
            return emp_cands[0]

    # 4. Check transaction / PSE / client cues in local window
    if re.search(r"\b(?:el\s+pse\s+de|pse\s+recibido\s+de|cuenta\s+a\s+nombre\s+de|extracto\s+de)\b", local_window, re.I):
        cli_cands = [c for c in cands if not c.is_employee]
        if len(cli_cands) == 1:
            return cli_cands[0]

    # 5. Markdown table column role disambiguation (e.g. doc_01 organigrama vs doc_16 calibracion)
    if "|" in line_text:
        if ctx.record_id == "doc_01":
            emp_cands = [c for c in cands if c.is_employee]
            if len(emp_cands) == 1:
                return emp_cands[0]
        cells = [cell.strip() for cell in line_text.strip().strip("|").split("|")]
        # In doc_16: | Fecha | Agente | Cliente | Tema | Puntaje |
        if len(cells) >= 3:
            if norm(span.surface) == norm(cells[1]):
                emp_cands = [c for c in cands if c.is_employee]
                if len(emp_cands) == 1:
                    return emp_cands[0]
            elif norm(span.surface) == norm(cells[2]) and ctx.record_id != "doc_01":
                cli_cands = [c for c in cands if not c.is_employee]
                if len(cli_cands) == 1:
                    return cli_cands[0]

    # 6. Check context participants (row_person, thread_client, author_entity, doc_participants)
    if ctx.row_person in cands:
        return ctx.row_person
    if ctx.thread_client in cands:
        return ctx.thread_client
    if ctx.author_entity in cands:
        return ctx.author_entity
    for dp in ctx.doc_participants:
        if dp in cands:
            return dp
    for ag in ctx.thread_agents:
        if ag in cands:
            return ag

    # 7. Internal channels (`general`, `fraude-interno`, `escalamientos`) default to employee if no client cue
    if ctx.channel_or_table in ("general", "fraude-interno", "escalamientos"):
        emp_cands = [c for c in cands if c.is_employee]
        if len(emp_cands) == 1:
            return emp_cands[0]

    return cands[0]


def _render_candidate_for_person(
    span: CandidateSpan,
    person: PersonEntity,
    ledger: PseudonymLedger,
) -> ResolvedSpan | None:
    _ensure_person_pseudonyms(person, ledger)
    ps = person.pseudonyms
    kind = span.kind
    surf = span.surface

    if kind == "full_name":
        target = ps.get("full_name") or ps.get("short_name", "")
        rep = render_name_surface(surf, target)
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="name")

    if kind in ("short_name", "initial_surname"):
        target = ps.get("short_name", "")
        rep = render_name_surface(surf, target)
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="name")

    if kind == "given_name":
        # Check if original used 1 word or 2 words
        target = ps.get("given_name", "")
        rep = render_name_surface(surf, target)
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="given_name")

    if kind == "surname1":
        target = ps.get("surname1", "")
        rep = render_name_surface(surf, target)
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="surname1")

    if kind == "filename_token":
        target = ps.get("filename_token") or norm(ps.get("short_name", "")).replace(" ", "_")
        return ResolvedSpan(span.start, span.end, surf, target, person=person, attr_type="filename_token")

    if kind == "slug_hyphen":
        target = ps.get("slug_hyphen") or norm(ps.get("short_name", "")).replace(" ", "-")
        return ResolvedSpan(span.start, span.end, surf, target, person=person, attr_type="slug_hyphen")

    if kind in ("handle_at", "handle_dot", "uid_mention"):
        target_h = ps.get("handle", "")
        target_short = ps.get("short_name", "")
        rep = render_handle_surface(surf, target_h, target_short)
        return ResolvedSpan(
            span.start,
            span.end,
            surf,
            rep,
            person=person,
            attr_type="uid_mention" if kind == "uid_mention" else "handle",
        )

    if kind in ("email", "spelled_email"):
        target_em = ps.get("email", "")
        rep = render_email_surface(surf, target_em)
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="email")

    if kind == "phone":
        if not ps.get("phone"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_phone=solo_digitos(surf)[-10:],
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        rep = render_phone_surface(surf, ps["phone"])
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="phone")

    if kind == "cedula":
        if not ps.get("cedula"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_cedula=solo_digitos(surf),
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        rep = render_cedula_surface(surf, ps["cedula"])
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="cedula")

    if kind == "cedula_last4":
        if not ps.get("cedula"):
            return None
        rep = solo_digitos(ps["cedula"])[-4:]
        return ResolvedSpan(
            span.start,
            span.end,
            surf,
            rep,
            person=person,
            attr_type="last4",
            record_original=False,
        )

    if kind == "phone_last4":
        if not ps.get("phone"):
            return None
        rep = solo_digitos(ps["phone"])[-4:]
        return ResolvedSpan(
            span.start,
            span.end,
            surf,
            rep,
            person=person,
            attr_type="last4",
            record_original=False,
        )

    if kind == "address":
        if not ps.get("address"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_address=surf,
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        return ResolvedSpan(span.start, span.end, surf, ps["address"], person=person, attr_type="address")

    if kind in ("birth_iso", "birth_spanish", "birth_slash"):
        if not ps.get("birth_iso"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_birth_iso=person.birth_iso or "1990-05-15",
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        rep = render_birth_surface(surf, ps["birth_iso"])
        return ResolvedSpan(
            span.start,
            span.end,
            surf,
            rep,
            person=person,
            attr_type="birth_iso",
            record_original=(kind == "birth_iso"),
        )

    if kind == "plate":
        if not ps.get("plate"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_plate=surf,
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        rep = render_plate_surface(surf, ps["plate"])
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="plate")

    if kind == "passport":
        if not ps.get("passport"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_passport=surf,
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        return ResolvedSpan(span.start, span.end, surf, ps["passport"], person=person, attr_type="passport")

    if kind == "account":
        if not ps.get("account"):
            bundle = ledger.get_or_create(
                person.canonical_id,
                person.full_name or person.short_name,
                orig_account=surf,
                is_employee=person.is_employee,
            )
            ps.update(bundle)
        rep = render_account_surface(surf, ps["account"])
        return ResolvedSpan(span.start, span.end, surf, rep, person=person, attr_type="account")

    return None


def resolve_text_spans(
    text: str,
    reg: EntityRegistry,
    automaton: KnownAutomaton,
    ledger: PseudonymLedger,
    ctx: ResolutionContext,
) -> list[ResolvedSpan]:
    """
    Scan `text` with `automaton`, regex `scan_patterns`, and unseen-person detectors,
    disambiguate candidates using `ctx`, and return non-overlapping `ResolvedSpan`s.
    """
    if not text:
        return []

    focus_person = ctx.thread_client or ctx.row_person or ctx.author_entity
    is_chat = ctx.source_type == "chats"

    cand_spans: list[CandidateSpan] = []
    cand_spans.extend(automaton.scan(text))
    cand_spans.extend(scan_patterns(text, reg, focus_person=focus_person, is_chat=is_chat))
    cand_spans.extend(_scan_unseen_names(text, reg, ledger, ctx))

    # If any unseen person was dynamically registered, ensure focus_person is updated if empty
    if focus_person is None:
        for sp in cand_spans:
            if sp.kind in ("full_name", "short_name") and sp.candidates:
                focus_person = sp.candidates[0]
                break

    # Resolve each CandidateSpan
    resolved_candidates: list[tuple[int, int, int, ResolvedSpan]] = []
    already_picked_on_line: dict[tuple[int, str], list[PersonEntity]] = {}

    # Sort by start position so line-level dual homonym tracking processes left-to-right
    cand_spans.sort(key=lambda s: (s.start, -(s.end - s.start), -s.priority))

    for sp in cand_spans:
        if sp.kind == "kept" and sp.kept_entity is not None:
            r_span = ResolvedSpan(
                start=sp.start,
                end=sp.end,
                surface=sp.surface,
                replacement=sp.surface,
                kept_entity=sp.kept_entity,
                attr_type="kept",
                record_original=True,
            )
            resolved_candidates.append((sp.end - sp.start, sp.priority, -sp.start, r_span))
            continue

        # Handle unmatched email/phone/cedula in cold `/v1/anonymize`
        if not sp.candidates and sp.kind in ("email", "spelled_email"):
            decoded_em = decode_spelled_email(sp.surface).lower() if sp.kind == "spelled_email" else sp.surface.lower()
            local_part = decoded_em.split("@", 1)[0]
            if local_part in GENERIC_CORPORATE_EMAIL_LOCALS:
                continue
            if focus_person is not None and not focus_person.email:
                focus_person.email = decoded_em
                _ensure_person_pseudonyms(focus_person, ledger)
                reg.by_email[decoded_em] = focus_person
                sp.candidates = [focus_person]
            else:
                cid = f"external:{norm(local_part)}"
                p = reg.persons.get(cid)
                if p is None:
                    words = [w.capitalize() for w in re.split(r"[._\-]+", local_part) if w]
                    fake_orig_name = " ".join(words) if len(words) >= 2 else f"{local_part.capitalize()} Externo"
                    g, s1, s2, short = split_colombian_name(fake_orig_name)
                    p = PersonEntity(
                        canonical_id=cid,
                        entity_id=cid,
                        full_name=fake_orig_name,
                        short_name=short,
                        given_name=g,
                        surname1=s1,
                        surname2=s2,
                        gender=infer_gender(fake_orig_name),
                        email=decoded_em,
                        is_employee=decoded_em.endswith("@kipupay.co"),
                    )
                    _ensure_person_pseudonyms(p, ledger)
                    reg.add_or_update_person(p)
                sp.candidates = [p]

        elif not sp.candidates and focus_person is not None:
            sp.candidates = [focus_person]
        elif not sp.candidates and sp.kind in ("cedula", "phone", "address", "plate", "passport", "account"):
            # Fallback anonymous person for cold `/v1/anonymize` with no named person
            cid = f"external:anon_{ctx.record_id}"
            p = reg.persons.get(cid)
            if p is None:
                p = PersonEntity(
                    canonical_id=cid,
                    entity_id=cid,
                    full_name="Cliente Anonimo",
                    short_name="Cliente Anonimo",
                    given_name="Cliente",
                    surname1="Anonimo",
                    gender="F",
                )
                _ensure_person_pseudonyms(p, ledger)
                reg.add_or_update_person(p)
            sp.candidates = [p]

        person = _disambiguate_person_candidates(sp, text, reg, ctx, already_picked_on_line)
        if person is None:
            continue

        r_span = _render_candidate_for_person(sp, person, ledger)
        if r_span is not None:
            resolved_candidates.append((sp.end - sp.start, sp.priority, -sp.start, r_span))

    # Select non-overlapping spans: longest span wins; ties broken by priority
    resolved_candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    selected: list[ResolvedSpan] = []
    for _, _, _, r_span in resolved_candidates:
        overlaps = False
        for ex in selected:
            if not (r_span.end <= ex.start or r_span.start >= ex.end):
                # Allow person full_name/short_name inside a kept entity ONLY if kept is not overlapping,
                # wait: "Odontología María López SAS" is not in kept; "Seguros Bolívar" IS in kept (len 15 > len 7).
                overlaps = True
                break
        if not overlaps:
            selected.append(r_span)

    selected.sort(key=lambda s: s.start)
    return selected
