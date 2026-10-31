"""Aho-Corasick automaton over known names, handles, addresses, and kept entities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import ahocorasick

from app.normalize import norm, norm_with_offsets
from app.pseudonym import NICKNAME_BY_GIVEN
from app.registry import EntityRegistry, KeptEntity, PersonEntity

SPANISH_MONTHS: dict[int, str] = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


def birth_iso_to_spanish(birth_iso: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", birth_iso.strip())
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    m_name = SPANISH_MONTHS.get(mo, "")
    if not m_name:
        return ""
    return f"{d} de {m_name} de {y}"


def birth_iso_to_slashes(birth_iso: str) -> list[str]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", birth_iso.strip())
    if not m:
        return []
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return [f"{mo}/{d}/{y}", f"{d}/{mo}/{y}"]


@dataclass
class CandidateSpan:
    start: int
    end: int
    surface: str
    kind: str  # full_name, short_name, given_name, surname1, initial_surname, handle, filename_token, slug_hyphen, email, spelled_email, phone, cedula, cedula_last4, phone_last4, address, birth_iso, birth_spanish, birth_slash, plate, passport, account, uid_mention, kept
    candidates: list[PersonEntity]
    kept_entity: KeptEntity | None = None
    priority: int = 50


class KnownAutomaton:
    """
    Fast Aho-Corasick automaton built over normalized forms of all registered
    person names, handles, addresses, birth dates, and kept entities.
    """

    def __init__(self, registry: EntityRegistry) -> None:
        self.registry = registry
        self.automaton = ahocorasick.Automaton()
        self.patterns: dict[str, list[tuple[str, Any, int]]] = {}
        self._build()

    def _add_pattern(self, key_norm: str, kind: str, obj: Any, priority: int) -> None:
        k = key_norm.strip()
        if len(k) < 2:
            return
        lst = self.patterns.setdefault(k, [])
        for ex_kind, ex_obj, _ in lst:
            if ex_kind == kind and ex_obj is obj:
                return
        lst.append((kind, obj, priority))

    def _build(self) -> None:
        # 1. Kept entities
        for kept in self.registry.kept.values():
            for v in kept.variants:
                vn = norm(v)
                if len(vn) >= 2:
                    self._add_pattern(vn, "kept", kept, 45)

        # 2. Person entities
        for p in self.registry.persons.values():
            if p.full_name and len(p.full_name.split()) >= 3:
                self._add_pattern(norm(p.full_name), "full_name", p, 95)
            if p.short_name:
                self._add_pattern(norm(p.short_name), "short_name", p, 90)
            elif p.full_name:
                self._add_pattern(norm(p.full_name), "short_name", p, 90)
            if p.given_name:
                gn = norm(p.given_name)
                if len(gn) >= 3:
                    self._add_pattern(gn, "given_name", p, 60)
                first_w = gn.split()[0]
                if first_w != gn and len(first_w) >= 3:
                    self._add_pattern(first_w, "given_name", p, 58)
                for nick in NICKNAME_BY_GIVEN.get(first_w, []):
                    self._add_pattern(nick, "nickname", p, 56)
            if p.surname1 and len(norm(p.surname1)) >= 4:
                self._add_pattern(norm(p.surname1), "surname1", p, 55)
            if p.given_name and p.surname1:
                init_s = f"{norm(p.given_name)[0]}. {norm(p.surname1)}"
                self._add_pattern(init_s, "initial_surname", p, 88)
                fn_tok = f"{norm(p.given_name).replace(' ', '_')}_{norm(p.surname1)}"
                self._add_pattern(fn_tok, "filename_token", p, 92)
                slug_tok = f"{norm(p.given_name).replace(' ', '-')}-{norm(p.surname1)}"
                self._add_pattern(slug_tok, "slug_hyphen", p, 92)
                if p.full_name and len(p.full_name.split()) >= 3:
                    fn_full = norm(p.full_name).replace(" ", "_")
                    self._add_pattern(fn_full, "filename_token", p, 94)
                    fn_first_last = f"{norm(p.given_name).split()[0]}_{norm(p.surname1)}"
                    self._add_pattern(fn_first_last, "filename_token", p, 92)
            for h in p.handles:
                hn = norm(h.lstrip("@"))
                if len(hn) >= 4:
                    self._add_pattern(f"@{hn}", "handle_at", p, 94)
                    if "." in hn:
                        self._add_pattern(hn, "handle_dot", p, 91)
            if p.address:
                self._add_pattern(norm(p.address), "address", p, 96)
            for ea in p.extra_addresses:
                self._add_pattern(norm(ea), "address", p, 96)
            if p.birth_iso:
                self._add_pattern(norm(p.birth_iso), "birth_iso", p, 93)
                sp_b = birth_iso_to_spanish(p.birth_iso)
                if sp_b:
                    self._add_pattern(norm(sp_b), "birth_spanish", p, 93)
                for sl_b in birth_iso_to_slashes(p.birth_iso):
                    self._add_pattern(norm(sl_b), "birth_slash", p, 93)
            if p.plate:
                pl_n = norm(p.plate)
                self._add_pattern(pl_n, "plate", p, 94)
                if "-" not in pl_n and len(pl_n) == 6:
                    self._add_pattern(f"{pl_n[:3]}-{pl_n[3:]}", "plate", p, 94)
                elif "-" in pl_n:
                    self._add_pattern(pl_n.replace("-", ""), "plate", p, 94)
            if p.passport:
                self._add_pattern(norm(p.passport), "passport", p, 94)
            if p.account:
                self._add_pattern(norm(p.account), "account", p, 95)

        for k, val_list in self.patterns.items():
            self.automaton.add_word(k, (k, val_list))
        if len(self.patterns) > 0:
            self.automaton.make_automaton()

    def scan(self, text: str) -> list[CandidateSpan]:
        if not text or not self.patterns:
            return []
        norm_text, starts, ends = norm_with_offsets(text)
        if not norm_text:
            return []

        n_len = len(norm_text)
        raw_spans: list[CandidateSpan] = []

        for end_idx_incl, (key_norm, val_list) in self.automaton.iter(norm_text):
            end_norm = end_idx_incl + 1
            start_norm = end_norm - len(key_norm)
            if start_norm < 0:
                continue

            # Check word boundaries on norm_text
            before_ch = norm_text[start_norm - 1] if start_norm > 0 else " "
            after_ch = norm_text[end_norm] if end_norm < n_len else " "

            # Allow `_` boundary when matching inside filenames for filename_token, plate, or passport
            is_fn_only = all(k in ("filename_token", "plate", "passport") for k, _, _ in val_list)
            if is_fn_only:
                if before_ch.isalnum() or after_ch.isalnum():
                    continue
            else:
                if (before_ch.isalnum() or before_ch == "_") and key_norm[0] != "@":
                    continue
                if after_ch.isalnum() or after_ch == "_":
                    continue

            orig_start = starts[start_norm]
            orig_end = ends[end_norm - 1]
            surface = text[orig_start:orig_end]

            # Group by kind inside val_list
            by_kind: dict[str, list[ tuple[Any, int] ]] = {}
            for kind, obj, prio in val_list:
                if kind == "handle_dot" and (after_ch == "@" or before_ch == "@"):
                    continue
                by_kind.setdefault(kind, []).append((obj, prio))

            for kind, items in by_kind.items():
                if kind == "kept":
                    kept_obj = items[0][0]
                    raw_spans.append(
                        CandidateSpan(
                            start=orig_start,
                            end=orig_end,
                            surface=surface,
                            kind="kept",
                            candidates=[],
                            kept_entity=kept_obj,
                            priority=items[0][1],
                        )
                    )
                else:
                    cands = [it[0] for it in items]
                    prio = max(it[1] for it in items)
                    raw_spans.append(
                        CandidateSpan(
                            start=orig_start,
                            end=orig_end,
                            surface=surface,
                            kind=kind,
                            candidates=cands,
                            priority=prio,
                        )
                    )

        return raw_spans
