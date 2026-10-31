"""Cross-source entity linking across tables, Slack users, chats, and documents."""
from __future__ import annotations

import re
from typing import Any

from app.normalize import RX_SPELLED_EMAIL, decode_spelled_email, norm, solo_digitos
from app.pseudonym import infer_gender
from app.registry import EntityRegistry, PersonEntity, split_colombian_name
from app.sources import CorpusBundle

RX_PQR = re.compile(r"\b(PQR-\d{4}-\d{3,5})\b")
RX_SOL = re.compile(r"\b(S-\d{4}-\d{2,4})\b")
RX_OBL = re.compile(r"\b(4471-\d{4})\b")
RX_INC = re.compile(r"\b(INC-\d{4}-\d{2,4})\b")
RX_CL = re.compile(r"\b(CL-\d{4,6})\b")
RX_PLATE = re.compile(r"\bplaca\s+([A-Z]{3}[-\s]?\d{2}[0-9A-Z])\b", re.I)
RX_PLATE_EN = re.compile(r"\bmy\s+car\s+is\s+([A-Z]{3}[-\s]?\d{2}[0-9A-Z])\b", re.I)
RX_PASSPORT = re.compile(r"\bpasaporte\s+([A-Z]{1,3}\d{5,8})\b", re.I)
RX_ACCOUNT_TX = re.compile(
    r"Transferencia\s+a\s+cuenta\s+(\d{4}\s+\d{4}\s+\d{3})\s*-\s*([A-ZÁÉÍÓÚÑ])\.\s*([A-ZÁÉÍÓÚÑa-záéíóúñ]+)",
    re.I,
)
RX_ADDR = re.compile(
    r"\b(?:Calle|Cl|Carrera|Cra|Cr|Diagonal|Diag|Dg|Transversal|Tv|Tr|Avenida|Av)\.?\s+\d+[A-Za-z]?(?:\s+(?:Bis|Sur|Norte|Este|Oeste))*\s*(?:#|No\.?)\s*\d+[A-Za-z]?-\d+(?:\s+(?:apto|of|oficina|casa|interior|int|torre|piso)\s+\w+)*",
    re.I,
)
RX_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
RX_PHONE = re.compile(r"(?:\+?57[\s.-]*)?(3\d{2}[\s.-]*\d{3}[\s.-]*\d{4})\b")
RX_DOC_DOTS = re.compile(r"\b(\d{1,2}\.\d{3}\.\d{3}(?:\.\d{3})?)\b")


def _extract_markers_into(text: str, p: PersonEntity) -> None:
    for m in RX_PQR.finditer(text):
        p.pqrs.add(m.group(1))
    for m in RX_SOL.finditer(text):
        p.solicitudes.add(m.group(1))
    for m in RX_OBL.finditer(text):
        p.obligaciones.add(m.group(1))
    for m in RX_INC.finditer(text):
        p.incidentes.add(m.group(1))
    m_pl = RX_PLATE.search(text) or RX_PLATE_EN.search(text)
    if m_pl and not p.plate:
        p.plate = m_pl.group(1).upper().replace(" ", "")
    m_pp = RX_PASSPORT.search(text)
    if m_pp and not p.passport:
        p.passport = m_pp.group(1).upper().strip()


def build_and_link_registry(
    bundle: CorpusBundle,
    registry: EntityRegistry | None = None,
) -> EntityRegistry:
    """
    Build or update `EntityRegistry` from `bundle`, linking all tables, Slack UIDs,
    cross-source identifiers, and external third-party persons.
    """
    reg = registry or EntityRegistry()

    # 1. Ingest `clientes` and `empleados`
    for t in bundle.tables:
        tname = t.get("table")
        cols = t.get("columns", {})
        if tname == "clientes":
            cid = cols.get("cliente_id", "").strip()
            if not cid:
                continue
            full_name = cols.get("nombre_completo", "").strip()
            given, s1, s2, short = split_colombian_name(full_name)
            p = reg.persons.get(cid) or PersonEntity(canonical_id=cid, entity_id=cid)
            p.is_employee = False
            p.full_name = full_name
            p.short_name = short
            p.given_name = given
            p.surname1 = s1
            p.surname2 = s2
            p.gender = infer_gender(full_name)
            p.tipo_doc = cols.get("tipo_doc", "CC").strip()
            p.cedula = solo_digitos(cols.get("num_doc", ""))
            p.email = cols.get("correo", "").strip().lower()
            p.phone = solo_digitos(cols.get("celular", ""))[-10:]
            p.ciudad = cols.get("ciudad", "").strip()
            p.barrio = cols.get("barrio", "").strip()
            p.address = cols.get("direccion", "").strip()
            p.birth_iso = cols.get("fecha_nacimiento", "").strip()
            p.vinculacion_iso = cols.get("fecha_vinculacion", "").strip()
            p.ocupacion = cols.get("ocupacion", "").strip()
            if p.email and "@" in p.email:
                p.handles.add(p.email.split("@", 1)[0])
            notas = cols.get("notas", "")
            if notas:
                _extract_markers_into(notas, p)
            reg.add_or_update_person(p)

        elif tname == "empleados":
            eid = cols.get("id", "").strip()
            if not eid:
                continue
            full_name = cols.get("nombre", "").strip()
            given, s1, s2, short = split_colombian_name(full_name)
            p = reg.persons.get(eid) or PersonEntity(canonical_id=eid, entity_id=eid)
            p.is_employee = True
            p.full_name = full_name
            p.short_name = short
            p.given_name = given
            p.surname1 = s1
            p.surname2 = s2
            p.gender = infer_gender(full_name)
            p.tipo_doc = "CC"
            p.cedula = solo_digitos(cols.get("documento", ""))
            p.email = cols.get("correo", "").strip().lower()
            p.phone = solo_digitos(cols.get("celular", ""))[-10:]
            p.ciudad = cols.get("ciudad", "").strip()
            p.birth_iso = cols.get("fecha_nacimiento", "").strip()
            p.vinculacion_iso = cols.get("ingreso", "").strip()
            p.cargo = cols.get("cargo", "").strip()
            p.reporta_a = cols.get("reporta_a", "").strip()
            if p.email and "@" in p.email:
                p.handles.add(p.email.split("@", 1)[0])
            reg.add_or_update_person(p)

    # 2. Ingest `pqrs` and `transacciones`
    for t in bundle.tables:
        tname = t.get("table")
        cols = t.get("columns", {})
        if tname == "pqrs":
            rad = cols.get("radicado", "").strip()
            cid = cols.get("cliente_id", "").strip()
            ced = solo_digitos(cols.get("cedula", ""))
            desc = cols.get("descripcion", "")
            p = reg.persons.get(cid) or (reg.by_cedula.get(ced) if ced else None)
            if p is None and cid:
                # Prospective / unstructured client referenced by PQR (e.g. CL-55481)
                p = PersonEntity(
                    canonical_id=cid,
                    entity_id=cid,
                    is_employee=False,
                    cedula=ced,
                    ciudad=cols.get("ciudad", "").strip(),
                )
                reg.add_or_update_person(p)
            if p is not None:
                if rad:
                    p.pqrs.add(rad)
                if ced and not p.cedula:
                    p.cedula = ced
                _extract_markers_into(desc, p)
                reg.add_or_update_person(p)

    # Link solicitudes in docs (e.g. S-2609-05 -> Felipe Castilla -> CL-55481)
    for d in bundle.docs:
        for line in d.get("body", "").splitlines():
            m_sol = RX_SOL.search(line)
            if m_sol and "|" in line:
                sol_id = m_sol.group(1)
                p_sol = reg.by_solicitud.get(sol_id)
                if p_sol and not p_sol.full_name:
                    parts = [x.strip() for x in line.split("|") if x.strip()]
                    if len(parts) >= 2:
                        client_cell = parts[1]
                        m_name = re.match(r"^([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)", client_cell)
                        if m_name:
                            nm = m_name.group(1).strip()
                            given, s1, s2, short = split_colombian_name(nm)
                            p_sol.full_name = nm
                            p_sol.short_name = short
                            p_sol.given_name = given
                            p_sol.surname1 = s1
                            p_sol.surname2 = s2
                            p_sol.gender = infer_gender(nm)
                            if "," in client_cell:
                                occ = client_cell.split(",", 1)[1].strip()
                                p_sol.ocupacion = occ
                            reg.add_or_update_person(p_sol)

    for t in bundle.tables:
        if t.get("table") == "transacciones":
            cols = t.get("columns", {})
            cid = cols.get("cliente_id", "").strip()
            fecha = cols.get("fecha", "").strip()
            monto = cols.get("monto", "").strip()
            desc = cols.get("descripcion", "")
            p = reg.persons.get(cid)
            if p is not None:
                if fecha and monto:
                    p.tx_dates_amounts.add((fecha, monto))
                _extract_markers_into(desc, p)
                reg._index_person(p)
            m_acc = RX_ACCOUNT_TX.search(desc)
            if m_acc:
                acc_num, init_letter, surname_str = m_acc.group(1), m_acc.group(2), m_acc.group(3)
                s_cands = reg.by_surname1_norm.get(norm(surname_str), [])
                matched_p = None
                for cand in s_cands:
                    if norm(cand.given_name).startswith(norm(init_letter)):
                        matched_p = cand
                        break
                if matched_p is not None and not matched_p.account:
                    matched_p.account = acc_num.strip()
                    reg._index_person(matched_p)

    # 3. Link Slack users (`users.json`) to canonical entities
    unlinked_users: list[dict[str, Any]] = []
    for u in bundle.users:
        uid = u.get("id", "")
        if not uid or u.get("is_bot") or uid == "UKIPIBOT01":
            continue
        prof = u.get("profile", {}) or {}
        u_email = (prof.get("email") or "").strip().lower()
        u_phone = solo_digitos(prof.get("phone") or "")[-10:]
        u_real = (u.get("real_name") or prof.get("real_name") or "").strip()
        u_handle = (u.get("name") or "").strip().lower()

        matched: PersonEntity | None = None
        if u_email and u_email in reg.by_email:
            matched = reg.by_email[u_email]
        elif u_phone and u_phone in reg.by_phone:
            matched = reg.by_phone[u_phone]
        elif u_real:
            _, _, _, u_short = split_colombian_name(u_real)
            fn_cands = reg.by_full_name_norm.get(norm(u_real), [])
            if len(fn_cands) == 1:
                matched = fn_cands[0]
            else:
                sn_cands = reg.by_short_name_norm.get(norm(u_short), [])
                if len(sn_cands) == 1:
                    matched = sn_cands[0]

        if matched is not None:
            matched.uids.add(uid)
            if u_handle and u_handle != "deactivateduser":
                matched.handles.add(u_handle)
            if u_email and not matched.email:
                matched.email = u_email
            if u_phone and not matched.phone:
                matched.phone = u_phone
            if u_real and len(u_real.split()) > len(matched.full_name.split()):
                given, s1, s2, short = split_colombian_name(u_real)
                matched.full_name = u_real
                matched.short_name = short
                matched.given_name = given
                matched.surname1 = s1
                matched.surname2 = s2
            reg._index_person(matched)
        else:
            unlinked_users.append(u)

    # Group chat messages by thread_id and by _uid to resolve remaining unlinked users (homonyms & deactivateduser)
    chats_by_thread: dict[str, list[dict[str, Any]]] = {}
    chats_by_uid: dict[str, list[dict[str, Any]]] = {}
    for c in bundle.chats:
        chats_by_thread.setdefault(c["thread_id"], []).append(c)
        if c.get("_uid"):
            chats_by_uid.setdefault(c["_uid"], []).append(c)

    still_unlinked: list[dict[str, Any]] = []
    for u in unlinked_users:
        uid = u["id"]
        prof = u.get("profile", {}) or {}
        u_real = (u.get("real_name") or prof.get("real_name") or "").strip()
        u_handle = (u.get("name") or "").strip().lower()
        is_emp_title = prof.get("title", "Cliente") != "Cliente"

        # Gather all messages sent by uid and all messages in threads where uid is a participant
        user_msgs = chats_by_uid.get(uid, [])
        thread_ids = {m["thread_id"] for m in user_msgs}
        thread_msgs = [m for tid in thread_ids for m in chats_by_thread.get(tid, [])]
        combined_text = "\n".join(m["text"] for m in thread_msgs)
        own_text = "\n".join(m["text"] for m in user_msgs)

        # Candidate pool: matching short_name (if known) or all unlinked clients (if deactivateduser)
        if u_real and u_real.lower() != "usuario desactivado" and u_handle != "deactivateduser":
            _, _, _, u_short = split_colombian_name(u_real)
            cands = [
                p
                for p in reg.by_short_name_norm.get(norm(u_short), [])
                if p.is_employee == is_emp_title
            ]
            if not cands:
                cands = list(reg.by_short_name_norm.get(norm(u_short), []))
        else:
            cands = [p for p in reg.persons.values() if not p.is_employee and not p.uids]

        matched = _disambiguate_user_from_threads(uid, u_handle, own_text, combined_text, cands, reg)
        if matched is not None:
            matched.uids.add(uid)
            if u_handle and u_handle != "deactivateduser":
                matched.handles.add(u_handle)
            reg._index_person(matched)
        else:
            still_unlinked.append(u)

    # Elimination pass: if 1 unlinked client remains for a homonym short_name, link it!
    for u in still_unlinked:
        uid = u["id"]
        prof = u.get("profile", {}) or {}
        u_real = (u.get("real_name") or prof.get("real_name") or "").strip()
        u_handle = (u.get("name") or "").strip().lower()
        u_email = (prof.get("email") or "").strip().lower()
        u_phone = solo_digitos(prof.get("phone") or "")[-10:]
        is_emp_title = prof.get("title", "Cliente") != "Cliente"

        _, _, _, u_short = split_colombian_name(u_real)
        cands = [
            p
            for p in reg.by_short_name_norm.get(norm(u_short), [])
            if p.is_employee == is_emp_title and not p.uids
        ]
        if len(cands) == 1:
            matched = cands[0]
            matched.uids.add(uid)
            if u_handle and u_handle != "deactivateduser":
                matched.handles.add(u_handle)
            reg._index_person(matched)
        elif u_real and u_handle != "deactivateduser":
            # User exists only in users.json (e.g. Yesenia Ramírez Zapata in sample/input!)
            given, s1, s2, short = split_colombian_name(u_real)
            cid = f"user:{uid}"
            eid = f"p_{uid.lower()}"
            p = PersonEntity(
                canonical_id=cid,
                entity_id=eid,
                is_employee=is_emp_title,
                gender=infer_gender(u_real),
                full_name=u_real,
                short_name=short,
                given_name=given,
                surname1=s1,
                surname2=s2,
                email=u_email,
                phone=u_phone,
                cargo=prof.get("title", ""),
            )
            p.uids.add(uid)
            if u_handle:
                p.handles.add(u_handle)
            reg.add_or_update_person(p)

    # 4. Harvest extra attributes from chats/docs for linked entities (plates, passports, CEO home address)
    for c in bundle.chats:
        uid = c.get("_uid", "")
        p = reg.by_uid.get(uid)
        txt = c.get("text", "")
        if p is not None:
            if not p.is_employee:
                _extract_markers_into(txt, p)
                # Check if client only gave last 4 digits of phone in chat (e.g. CL-55481 -> 1765)
                if not p.phone and re.fullmatch(r"\s*\d{4}\s*", txt):
                    p.phone = "300000" + txt.strip()
            elif "en mi casa" in txt.lower():
                m_ad = RX_ADDR.search(txt)
                if m_ad and not p.address:
                    p.address = m_ad.group(0).strip()
                if "buenos aires" in txt.lower() and not p.barrio:
                    p.barrio = "Buenos Aires"
            reg._index_person(p)

    # 5. Harvest external third-party persons from docs and chats
    _harvest_external_persons(bundle, reg)

    # 6. Assign deterministic pseudonyms to all registered entities
    reg.assign_all_pseudonyms()
    reg.rebuild_indexes()
    return reg


def _disambiguate_user_from_threads(
    uid: str,
    u_handle: str,
    own_text: str,
    combined_text: str,
    cands: list[PersonEntity],
    reg: EntityRegistry,
) -> PersonEntity | None:
    if len(cands) == 1:
        return cands[0]

    # Check direct identifiers in combined_text
    scores: dict[str, int] = {p.canonical_id: 0 for p in cands}

    for p in cands:
        if p.canonical_id in combined_text:
            scores[p.canonical_id] += 100
        for pqr in p.pqrs:
            if pqr in combined_text:
                scores[p.canonical_id] += 100
        if p.cedula and p.cedula in solo_digitos(combined_text):
            scores[p.canonical_id] += 100
        for m_doc in RX_DOC_DOTS.finditer(combined_text):
            if solo_digitos(m_doc.group(1)) == p.cedula:
                scores[p.canonical_id] += 100
        if p.phone and p.phone in solo_digitos(combined_text):
            scores[p.canonical_id] += 100
        if p.email:
            if p.email in combined_text.lower():
                scores[p.canonical_id] += 100
            for m_sp in RX_SPELLED_EMAIL.finditer(combined_text):
                if decode_spelled_email(m_sp.group(0)).lower() == p.email:
                    scores[p.canonical_id] += 100
        if p.full_name and len(p.full_name.split()) >= 3 and norm(p.full_name) in norm(combined_text):
            scores[p.canonical_id] += 90
        if p.address and norm(p.address) in norm(combined_text):
            scores[p.canonical_id] += 90
        if p.plate and norm(p.plate) in norm(combined_text):
            scores[p.canonical_id] += 90
        if p.cedula and len(p.cedula) >= 4:
            if re.search(rf"\b{re.escape(p.cedula[-4:])}\b", combined_text):
                scores[p.canonical_id] += 40
        if p.phone and len(p.phone) >= 4:
            if re.search(rf"\b{re.escape(p.phone[-4:])}\b", combined_text):
                scores[p.canonical_id] += 40
        if p.barrio and norm(p.barrio) in norm(own_text):
            scores[p.canonical_id] += 25
        if p.ciudad and norm(p.ciudad) in norm(own_text):
            scores[p.canonical_id] += 15
        if p.ocupacion and norm(p.ocupacion) in norm(own_text):
            scores[p.canonical_id] += 25
        if u_handle and p.email and p.email.split("@", 1)[0] == u_handle:
            scores[p.canonical_id] += 10

    if not scores:
        return None
    best_cid = max(scores, key=lambda k: scores[k])
    if scores[best_cid] >= 10:
        return reg.persons[best_cid]
    return None


def _register_external_person(
    reg: EntityRegistry,
    full_name: str,
    cedula: str = "",
    email: str = "",
    phone: str = "",
    cargo: str = "",
    is_employee: bool = False,
) -> PersonEntity:
    clean_name = re.sub(r"\s+", " ", full_name).strip()
    norm_name = norm(clean_name)
    # Check if already in registry by email, cedula, or full/short name
    if email and email.lower() in reg.by_email:
        return reg.by_email[email.lower()]
    if cedula and solo_digitos(cedula) in reg.by_cedula:
        return reg.by_cedula[solo_digitos(cedula)]
    existing = reg.by_full_name_norm.get(norm_name) or reg.by_short_name_norm.get(norm_name)
    if existing:
        p = existing[0]
        if cedula and not p.cedula:
            p.cedula = solo_digitos(cedula)
        if email and not p.email:
            p.email = email.lower().strip()
        if phone and not p.phone:
            p.phone = solo_digitos(phone)[-10:]
        reg._index_person(p)
        return p

    slug = re.sub(r"[^a-z0-9]+", "_", norm_name).strip("_")
    cid = f"external:{norm_name}"
    eid = f"ext_{slug}"
    given, s1, s2, short = split_colombian_name(clean_name)
    p = PersonEntity(
        canonical_id=cid,
        entity_id=eid,
        is_employee=is_employee,
        gender=infer_gender(clean_name),
        full_name=clean_name,
        short_name=short,
        given_name=given,
        surname1=s1,
        surname2=s2,
        cedula=solo_digitos(cedula),
        email=email.lower().strip(),
        phone=solo_digitos(phone)[-10:] if phone else "",
        cargo=cargo,
    )
    return reg.add_or_update_person(p)


def _harvest_external_persons(bundle: CorpusBundle, reg: EntityRegistry) -> None:
    """Harvest third-party external persons from documents and chats."""
    for d in bundle.docs:
        body = d.get("body", "")
        meta = d.get("metadata", {}) or {}

        # Pattern 1: Legal representative in English contract (e.g. Jesús Sánchez, ID No. 71.772.071)
        for m in re.finditer(
            r"represented\s+by\s+its\s+(?:legal\s+representative|CEO),\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+),\s*identified\s+with\s+ID\s+No\.\s*([\d.]+)",
            body,
        ):
            _register_external_person(reg, m.group(1), cedula=m.group(2))

        # Pattern 2: HTML comment `<!-- ... by Name (email) -->` (e.g. Pamela Agudelo)
        for m in re.finditer(
            r"<!--[^>]*?\b(?:by|por)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)\s*\(([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\)[^>]*?-->",
            body,
        ):
            _register_external_person(reg, m.group(1), email=m.group(2))

        # Pattern 3: Auditora in markdown (e.g. Kelly Marulanda, kmarulanda@hotmail.com)
        m_aud = re.search(r"\*\*Auditor[ao]:\*\*\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)", body)
        if m_aud:
            aud_email = meta.get("auditora", "")
            _register_external_person(reg, m_aud.group(1), email=aud_email)

        # Pattern 4: External lawyer letter signature (e.g. Hernando Gómez / Abogado externo / hgomez@gmail.com)
        for m in re.finditer(
            r"Atentamente,\s*\n+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)\s*\n+Abogad[oa]\s+extern[oa]\s*\n+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
            body,
        ):
            _register_external_person(reg, m.group(1), email=m.group(2), cargo="Abogado externo")

        # Pattern 5: Markdown author email + Elaborado por <Name> (e.g. Kelly Castaño in sample/sdoc_01)
        auth_meta = meta.get("author", "")
        if auth_meta and "@" in auth_meta and auth_meta.lower() not in reg.by_email:
            m_elab = re.search(
                r"(?:Elaborad[oa]\s+por|Preparad[oa]\s+por)\s*:?\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)",
                body,
            )
            if m_elab:
                _register_external_person(
                    reg,
                    m_elab.group(1),
                    email=auth_meta,
                    is_employee=auth_meta.lower().endswith("@kipupay.co"),
                )

    # Pattern 6: Family / third-party mentions in chats not yet in registry
    rx_rel = re.compile(
        r"\b(?:mi\s+(?:hermana|hermano|esposo|esposa|mamá|mama|papá|papa|hijo|hija|tía|tío|prima|primo|novia|novio|pareja|socio|socia|cuñado|cuñada|jefe|jefa))\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
    )
    for c in bundle.chats:
        for m in rx_rel.finditer(c.get("text", "")):
            _register_external_person(reg, m.group(1))
