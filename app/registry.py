"""Canonical entity registry built from tables, chat users, and unstructured corpus."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import STATE_DIR
from app.normalize import RX_SPELLED_EMAIL, decode_spelled_email, norm, solo_digitos
from app.pseudonym import PseudonymLedger, format_cedula_dots, infer_gender


KEPT_ENTITIES_CATALOG: list[tuple[str, list[str]]] = [
    ("Kipu Pay", ["Kipu Pay S.A.S.", "Kipu Pay SAS", "Kipu Pay", "Kipu Core", "Kipu Docs"]),
    ("Kipi", ["Kipi"]),
    ("Bancolombia", ["Bancolombia"]),
    ("Nequi", ["Nequi"]),
    ("Daviplata", ["Daviplata"]),
    ("Davivienda", ["Davivienda"]),
    ("Colpatria", ["Colpatria"]),
    ("Superfinanciera", ["Superintendencia Financiera", "Superfinanciera", "SFC"]),
    ("SIC", ["Superintendencia de Industria y Comercio", "SIC"]),
    ("DataCrédito", ["DataCrédito", "Datacrédito"]),
    ("Fondo Nacional de Garantías", ["Fondo Nacional de Garantías"]),
    ("PSE", ["PSE"]),
    ("SURA", ["SURA"]),
    ("Seguros Bolívar", ["Seguros Bolívar"]),
    ("Carulla", ["Carulla"]),
    ("Éxito", ["Éxito"]),
    ("D1", ["D1"]),
    ("Oxxo", ["Oxxo"]),
    ("Ara", ["Ara"]),
    ("Jumbo", ["Jumbo"]),
    ("Alkosto", ["Alkosto"]),
    ("Olímpica", ["Olímpica"]),
    ("Farmatodo", ["Farmatodo"]),
    ("Juan Valdez", ["Juan Valdez"]),
    ("Terpel", ["Terpel"]),
    ("Avianca", ["Avianca"]),
    ("Rappi", ["Rappi"]),
    ("Nutresa", ["Nutresa"]),
    ("Claro", ["Claro"]),
    ("Tigo", ["Tigo"]),
    ("EPM", ["EPM"]),
    ("Enel", ["Enel"]),
    ("Emcali", ["Emcali"]),
    ("WeWork", ["WeWork"]),
    ("Los Andes", ["Los Andes"]),
    ("Mensajería Veloz", ["Mensajería Veloz Ltda.", "Mensajería Veloz Ltda", "Mensajeria Veloz", "Veloz"]),
    ("Auditores Andinos", ["Auditores Andinos"]),
    ("Andes Capital", ["Andes Capital"]),
    ("Gabriel García Márquez", ["Gabriel García Márquez"]),
    ("Jorge Isaacs", ["Jorge Isaacs"]),
    ("Shakira", ["Shakira"]),
    ("Egan Bernal", ["Egan Bernal"]),
    ("Nairo Quintana", ["Nairo Quintana"]),
    ("Medellín", ["Medellín"]),
    ("Bogotá", ["Bogotá"]),
    ("Cali", ["Cali"]),
    ("Barranquilla", ["Barranquilla"]),
    ("Bucaramanga", ["Bucaramanga"]),
    ("Cartagena", ["Cartagena"]),
    ("Pereira", ["Pereira"]),
    ("Santander", ["Santander"]),
    ("Boyacá", ["Boyacá"]),
]


def split_colombian_name(full_name: str) -> tuple[str, str, str, str]:
    """
    Split a Colombian name into `(given_name, surname1, surname2, short_name)`.
    - 4 words: `G1 G2 S1 S2` -> `("G1 G2", "S1", "S2", "G1 G2 S1")`
    - 3 words: `G1 S1 S2` -> `("G1", "S1", "S2", "G1 S1")`
    - 2 words: `G1 S1` -> `("G1", "S1", "", "G1 S1")`
    - 1 word:  `G1` -> `("G1", "", "", "G1")`
    """
    words = [w for w in full_name.strip().split() if w]
    if len(words) >= 4:
        given = " ".join(words[:-2])
        s1 = words[-2]
        s2 = words[-1]
        short = f"{given} {s1}"
        return given, s1, s2, short
    if len(words) == 3:
        given, s1, s2 = words[0], words[1], words[2]
        return given, s1, s2, f"{given} {s1}"
    if len(words) == 2:
        given, s1 = words[0], words[1]
        return given, s1, "", f"{given} {s1}"
    if len(words) == 1:
        return words[0], "", "", words[0]
    return "", "", "", ""


@dataclass
class PersonEntity:
    canonical_id: str
    entity_id: str
    is_employee: bool = False
    gender: str = "F"
    full_name: str = ""
    short_name: str = ""
    given_name: str = ""
    surname1: str = ""
    surname2: str = ""
    tipo_doc: str = "CC"
    cedula: str = ""
    email: str = ""
    phone: str = ""
    ciudad: str = ""
    barrio: str = ""
    address: str = ""
    extra_addresses: list[str] = field(default_factory=list)
    birth_iso: str = ""
    vinculacion_iso: str = ""
    ocupacion: str = ""
    cargo: str = ""
    reporta_a: str = ""
    plate: str = ""
    passport: str = ""
    account: str = ""
    handles: set[str] = field(default_factory=set)
    uids: set[str] = field(default_factory=set)
    pqrs: set[str] = field(default_factory=set)
    solicitudes: set[str] = field(default_factory=set)
    obligaciones: set[str] = field(default_factory=set)
    incidentes: set[str] = field(default_factory=set)
    tx_dates_amounts: set[tuple[str, str]] = field(default_factory=set)
    pseudonyms: dict[str, str] = field(default_factory=dict)
    originals_found: set[str] = field(default_factory=set)
    records_found: set[str] = field(default_factory=set)
    attrs_used: set[str] = field(default_factory=set)

    def record_hit(self, record_id: str, original_surface: str | None = None, attr_type: str = "") -> None:
        if record_id:
            self.records_found.add(record_id)
        if original_surface:
            clean_surf = original_surface.strip()
            if clean_surf and len(clean_surf) >= 2:
                self.originals_found.add(clean_surf)
        if attr_type:
            self.attrs_used.add(attr_type)

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "entity_id": self.entity_id,
            "is_employee": self.is_employee,
            "gender": self.gender,
            "full_name": self.full_name,
            "short_name": self.short_name,
            "given_name": self.given_name,
            "surname1": self.surname1,
            "surname2": self.surname2,
            "tipo_doc": self.tipo_doc,
            "cedula": self.cedula,
            "email": self.email,
            "phone": self.phone,
            "ciudad": self.ciudad,
            "barrio": self.barrio,
            "address": self.address,
            "extra_addresses": self.extra_addresses,
            "birth_iso": self.birth_iso,
            "vinculacion_iso": self.vinculacion_iso,
            "ocupacion": self.ocupacion,
            "cargo": self.cargo,
            "reporta_a": self.reporta_a,
            "plate": self.plate,
            "passport": self.passport,
            "account": self.account,
            "handles": sorted(self.handles),
            "uids": sorted(self.uids),
            "pqrs": sorted(self.pqrs),
            "solicitudes": sorted(self.solicitudes),
            "obligaciones": sorted(self.obligaciones),
            "incidentes": sorted(self.incidentes),
            "pseudonyms": self.pseudonyms,
        }

    @classmethod
    def from_snapshot_dict(cls, d: dict[str, Any]) -> PersonEntity:
        return cls(
            canonical_id=d["canonical_id"],
            entity_id=d["entity_id"],
            is_employee=bool(d.get("is_employee", False)),
            gender=d.get("gender", "F"),
            full_name=d.get("full_name", ""),
            short_name=d.get("short_name", ""),
            given_name=d.get("given_name", ""),
            surname1=d.get("surname1", ""),
            surname2=d.get("surname2", ""),
            tipo_doc=d.get("tipo_doc", "CC"),
            cedula=d.get("cedula", ""),
            email=d.get("email", ""),
            phone=d.get("phone", ""),
            ciudad=d.get("ciudad", ""),
            barrio=d.get("barrio", ""),
            address=d.get("address", ""),
            extra_addresses=list(d.get("extra_addresses", [])),
            birth_iso=d.get("birth_iso", ""),
            vinculacion_iso=d.get("vinculacion_iso", ""),
            ocupacion=d.get("ocupacion", ""),
            cargo=d.get("cargo", ""),
            reporta_a=d.get("reporta_a", ""),
            plate=d.get("plate", ""),
            passport=d.get("passport", ""),
            account=d.get("account", ""),
            handles=set(d.get("handles", [])),
            uids=set(d.get("uids", [])),
            pqrs=set(d.get("pqrs", [])),
            solicitudes=set(d.get("solicitudes", [])),
            obligaciones=set(d.get("obligaciones", [])),
            incidentes=set(d.get("incidentes", [])),
            pseudonyms=dict(d.get("pseudonyms", {})),
        )


@dataclass
class KeptEntity:
    entity_id: str
    name: str
    variants: list[str]
    reason: str = "organización, lugar, marca o figura pública; no es persona natural"
    originals_found: set[str] = field(default_factory=set)
    records_found: set[str] = field(default_factory=set)

    def record_hit(self, record_id: str, surface: str) -> None:
        if record_id:
            self.records_found.add(record_id)
        if surface:
            self.originals_found.add(surface)


class EntityRegistry:
    """Central registry of all person entities and kept entities."""

    def __init__(self, ledger: PseudonymLedger | None = None) -> None:
        self.ledger = ledger or PseudonymLedger()
        self.persons: dict[str, PersonEntity] = {}
        self.kept: dict[str, KeptEntity] = {}

        # Fast lookup indexes
        self.by_cedula: dict[str, PersonEntity] = {}
        self.by_cedula_last4: dict[str, list[PersonEntity]] = {}
        self.by_phone: dict[str, PersonEntity] = {}
        self.by_phone_last4: dict[str, list[PersonEntity]] = {}
        self.by_email: dict[str, PersonEntity] = {}
        self.by_handle: dict[str, list[PersonEntity]] = {}
        self.by_uid: dict[str, PersonEntity] = {}
        self.by_pqr: dict[str, PersonEntity] = {}
        self.by_solicitud: dict[str, PersonEntity] = {}
        self.by_obligacion: dict[str, PersonEntity] = {}
        self.by_plate: dict[str, PersonEntity] = {}
        self.by_passport: dict[str, PersonEntity] = {}
        self.by_account: dict[str, PersonEntity] = {}
        self.by_address_norm: dict[str, PersonEntity] = {}
        self.by_full_name_norm: dict[str, list[PersonEntity]] = {}
        self.by_short_name_norm: dict[str, list[PersonEntity]] = {}
        self.by_given_name_norm: dict[str, list[PersonEntity]] = {}
        self.by_surname1_norm: dict[str, list[PersonEntity]] = {}

        self._init_kept()

    def _init_kept(self) -> None:
        for idx, (canon_name, variants) in enumerate(KEPT_ENTITIES_CATALOG, start=1):
            slug = re.sub(r"[^a-z0-9]+", "_", norm(canon_name)).strip("_")
            eid = f"kept_{slug}"
            self.kept[eid] = KeptEntity(entity_id=eid, name=canon_name, variants=variants)

    def add_or_update_person(self, person: PersonEntity) -> PersonEntity:
        self.persons[person.canonical_id] = person
        self._index_person(person)
        return person

    def _index_person(self, p: PersonEntity) -> None:
        if p.cedula:
            cd = solo_digitos(p.cedula)
            if cd:
                self.by_cedula[cd] = p
                l4 = cd[-4:]
                lst = self.by_cedula_last4.setdefault(l4, [])
                if p not in lst:
                    lst.append(p)
        if p.phone:
            ph = solo_digitos(p.phone)[-10:]
            if ph:
                self.by_phone[ph] = p
                l4 = ph[-4:]
                lst = self.by_phone_last4.setdefault(l4, [])
                if p not in lst:
                    lst.append(p)
        if p.email:
            em = p.email.lower().strip()
            self.by_email[em] = p
            local = em.split("@", 1)[0]
            p.handles.add(local)
        for h in list(p.handles):
            hl = h.lower().strip().lstrip("@")
            if hl:
                lst = self.by_handle.setdefault(hl, [])
                if p not in lst:
                    lst.append(p)
        for u in p.uids:
            self.by_uid[u] = p
        for pqr in p.pqrs:
            self.by_pqr[pqr] = p
        for sol in p.solicitudes:
            self.by_solicitud[sol] = p
        for ob in p.obligaciones:
            self.by_obligacion[ob] = p
        if p.plate:
            pl_norm = re.sub(r"[^a-z0-9]", "", norm(p.plate))
            self.by_plate[pl_norm] = p
        if p.passport:
            pp_norm = re.sub(r"[^a-z0-9]", "", norm(p.passport))
            self.by_passport[pp_norm] = p
        if p.account:
            ac_d = solo_digitos(p.account)
            if ac_d:
                self.by_account[ac_d] = p
        if p.address:
            self.by_address_norm[norm(p.address)] = p
        for ea in p.extra_addresses:
            self.by_address_norm[norm(ea)] = p
        if p.full_name:
            fn = norm(p.full_name)
            lst = self.by_full_name_norm.setdefault(fn, [])
            if p not in lst:
                lst.append(p)
        if p.short_name:
            sn = norm(p.short_name)
            lst = self.by_short_name_norm.setdefault(sn, [])
            if p not in lst:
                lst.append(p)
        if p.given_name:
            gn = norm(p.given_name)
            lst = self.by_given_name_norm.setdefault(gn, [])
            if p not in lst:
                lst.append(p)
            first_tok = gn.split()[0]
            if first_tok != gn:
                lst2 = self.by_given_name_norm.setdefault(first_tok, [])
                if p not in lst2:
                    lst2.append(p)
        if p.surname1:
            s1 = norm(p.surname1)
            lst = self.by_surname1_norm.setdefault(s1, [])
            if p not in lst:
                lst.append(p)

    def rebuild_indexes(self) -> None:
        self.by_cedula.clear()
        self.by_cedula_last4.clear()
        self.by_phone.clear()
        self.by_phone_last4.clear()
        self.by_email.clear()
        self.by_handle.clear()
        self.by_uid.clear()
        self.by_pqr.clear()
        self.by_solicitud.clear()
        self.by_obligacion.clear()
        self.by_plate.clear()
        self.by_passport.clear()
        self.by_account.clear()
        self.by_address_norm.clear()
        self.by_full_name_norm.clear()
        self.by_short_name_norm.clear()
        self.by_given_name_norm.clear()
        self.by_surname1_norm.clear()
        for p in self.persons.values():
            self._index_person(p)

    def assign_all_pseudonyms(self) -> None:
        """Assign deterministic pseudonyms to all registered persons and save the ledger."""
        real_cedulas = {p.cedula for p in self.persons.values() if p.cedula}
        real_phones = {p.phone for p in self.persons.values() if p.phone}
        real_emails = {p.email for p in self.persons.values() if p.email}
        real_addrs = {p.address for p in self.persons.values() if p.address}
        real_births = {p.birth_iso for p in self.persons.values() if p.birth_iso}
        self.ledger.register_real_corpus_identifiers(
            real_cedulas, real_phones, real_emails, real_addrs, real_births
        )

        for cid in sorted(self.persons.keys()):
            p = self.persons[cid]
            orig_handle = next(iter(sorted(p.handles)), "")
            p.pseudonyms = dict(
                self.ledger.get_or_create(
                    canonical_id=p.canonical_id,
                    orig_name=p.full_name or p.short_name,
                    gender=p.gender,
                    orig_email=p.email,
                    orig_phone=p.phone,
                    orig_cedula=p.cedula,
                    orig_address=p.address,
                    orig_birth_iso=p.birth_iso,
                    orig_plate=p.plate,
                    orig_passport=p.passport,
                    orig_account=p.account,
                    orig_handle=orig_handle,
                    is_employee=p.is_employee,
                )
            )
        self.ledger.save()

    def save_snapshot(self, path: Path | None = None) -> None:
        snap_path = path or (STATE_DIR / "registry_snapshot.json")
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "persons": {cid: p.to_snapshot_dict() for cid, p in sorted(self.persons.items())}
        }
        snap_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_snapshot(self, path: Path | None = None) -> bool:
        snap_path = path or (STATE_DIR / "registry_snapshot.json")
        if not snap_path.exists():
            return False
        try:
            data = json.loads(snap_path.read_text(encoding="utf-8"))
            for cid, pdict in data.get("persons", {}).items():
                p = PersonEntity.from_snapshot_dict(pdict)
                self.persons[cid] = p
            self.rebuild_indexes()
            return True
        except Exception:
            return False
