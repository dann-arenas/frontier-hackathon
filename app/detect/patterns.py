"""Regex and structural pattern detectors for Colombian PII."""
from __future__ import annotations

import re
from typing import Any

from app.detect.known import CandidateSpan
from app.normalize import (
    RX_SPELLED_EMAIL,
    decode_spelled_email,
    find_digit_spans_in_text,
    norm,
    solo_digitos,
)
from app.registry import EntityRegistry, PersonEntity

RX_UID_MENTION = re.compile(r"<@(U[A-Z0-9]+)(?:\|[^>]+)?>")
RX_EMAIL = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]*[a-zA-Z0-9]\b")
RX_PHONE = re.compile(r"(?:\+?57[\s.\-\u00a0\u200b]*)?(?<![0-9])(3\d{2}[\s.\-\u00a0\u200b]*\d{3}[\s.\-\u00a0\u200b]*\d{4})(?![0-9])")
RX_CEDULA_DOTS = re.compile(r"(?<![0-9.])(\d{1,2}\.\d{3}\.\d{3}(?:\.\d{3})?)(?![0-9]|\.\d)")
RX_CEDULA_PLAIN = re.compile(r"(?:(?<![0-9A-Za-z\-])|(?<=\bCC)|(?<=\bcc)|(?<=_))(\d{7,10})(?![0-9])")
RX_CEDULA_OBFUSCATED = re.compile(
    r"(?:c[eé]dula|cc|documento|id(?:\s+no\.?)?)\s*(?:es|:|n[oú]mero)?\s*([0-9][0-9\s.,'’`´·/\-–—_\u200b\u200c\u200d\ufeff]{6,18}[0-9])",
    re.I,
)
RX_ADDR = re.compile(
    r"\b(?:Calle|Cl|Carrera|Cra|Cr|Diagonal|Diag|Dg|Transversal|Tv|Tr|Avenida|Av)\.?\s+\d+[A-Za-z]?(?:\s+(?:Bis|Sur|Norte|Este|Oeste))*\s*(?:#|No\.?)\s*\d+[A-Za-z]?-\d+(?:\s+(?:apto|of|oficina|casa|interior|int|torre|piso)\s+\w+)*",
    re.I,
)
RX_BIRTH_ISO = re.compile(r"\b(19[4-9]\d|200\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
RX_BIRTH_SPANISH = re.compile(
    r"\b([12]?\d|3[01])\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+(19[4-9]\d|200\d)\b",
    re.I,
)
RX_BIRTH_SLASH = re.compile(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|[12]\d|3[01])/(19[4-9]\d|200\d)\b")
RX_PLATE_CTX = re.compile(r"\b(?:placa|car\s+is)\s+([A-Z]{3}[-\s]?\d{2}[0-9A-Z])\b", re.I)
RX_PASSPORT_CTX = re.compile(r"\b(?:pasaporte|passport)\s+([A-Z]{1,3}\d{5,8})\b", re.I)
RX_ACCOUNT_CTX = re.compile(
    r"\b(?:cuenta|account)\s+(?:de\s+ahorros\s+|corriente\s+|bancaria\s+|no\.?\s+|n[uú]mero\s+|number\s+)?(\d{3,4}[\s\-]\d{4,6}[\s\-]\d{2,4})\b",
    re.I,
)
RX_LAST4_TOKEN = re.compile(r"(?<![0-9.])(\d{4})(?![0-9]|\.\d)")

# Corporate office address of Kipu Pay (must be preserved)
KIPU_OFFICE_ADDR_NORM = "cra 43a # 1-50"
KIPU_NIT_DIGITS = "9014567893"


def scan_patterns(
    text: str,
    registry: EntityRegistry,
    focus_person: PersonEntity | None = None,
    is_chat: bool = False,
) -> list[CandidateSpan]:
    """Scan `text` with regex patterns for structured PII tokens."""
    if not text:
        return []
    spans: list[CandidateSpan] = []

    # 1. Slack <@U...> mentions
    for m in RX_UID_MENTION.finditer(text):
        uid = m.group(1)
        p = registry.by_uid.get(uid)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=m.group(0),
                kind="uid_mention",
                candidates=cands,
                priority=99,
            )
        )

    # 2. Spelled-out emails
    for m in RX_SPELLED_EMAIL.finditer(text):
        surf = m.group(0)
        decoded = decode_spelled_email(surf).lower()
        p = registry.by_email.get(decoded)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="spelled_email",
                candidates=cands,
                priority=98,
            )
        )

    # 3. Standard emails
    for m in RX_EMAIL.finditer(text):
        surf = m.group(0)
        em = surf.lower()
        p = registry.by_email.get(em)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="email",
                candidates=cands,
                priority=98,
            )
        )

    # 3b. URL query handles (`?ref=agomez`, `?u=khenao`, `&user=jrojas`)
    for m in re.finditer(r"[?&](?:ref|u|user|usr|agente|author)=([a-zA-Z0-9_.]{4,25})\b", text):
        h_surf = m.group(1)
        h_norm = norm(h_surf)
        if h_norm in ("chat", "web", "app", "email", "soporte", "kipi", "true", "false"):
            continue
        cands = list(registry.by_handle.get(h_norm, []))
        if not cands and focus_person is not None:
            cands = [focus_person]
        spans.append(
            CandidateSpan(
                start=m.start(1),
                end=m.end(1),
                surface=h_surf,
                kind="handle_dot",
                candidates=cands,
                priority=94,
            )
        )

    # 4. Mobile phones
    for m in RX_PHONE.finditer(text):
        surf = m.group(0)
        digits = solo_digitos(m.group(1))[-10:]
        p = registry.by_phone.get(digits)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="phone",
                candidates=cands,
                priority=97,
            )
        )

    # 5. Dotted cedulas (and check against money amounts)
    for m in RX_CEDULA_DOTS.finditer(text):
        surf = m.group(1)
        digits = solo_digitos(surf)
        if digits == KIPU_NIT_DIGITS or digits.startswith("901456789"):
            continue
        p = registry.by_cedula.get(digits)
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=m.end(1),
                    surface=surf,
                    kind="cedula",
                    candidates=[p],
                    priority=97,
                )
            )
        else:
            # Check context for unknown dotted cedula (not ending in .000 unless explicitly preceded by cedula/cc)
            before_ctx = text[max(0, m.start(1) - 35) : m.start(1)].lower()
            has_ced_cue = bool(re.search(r"\b(?:c[eé]dula|cc|c\.c\.|documento|identificaci[oó]n|id)\b", before_ctx))
            is_standalone_chat = is_chat and len(text.strip()) <= 28 and not surf.endswith(".000")
            if (has_ced_cue or is_standalone_chat or not surf.endswith("00")) and not surf.endswith(".000"):
                cands = [focus_person] if focus_person else []
                spans.append(
                    CandidateSpan(
                        start=m.start(1),
                        end=m.end(1),
                        surface=surf,
                        kind="cedula",
                        candidates=cands,
                        priority=95,
                    )
                )

    # 6. Plain 7-10 digit cedulas
    for m in RX_CEDULA_PLAIN.finditer(text):
        surf = m.group(1)
        digits = solo_digitos(surf)
        if digits in registry.by_phone:
            continue
        # Skip PSE references (`PSE-76224471`) or Slack timestamps (`1785769807.000100`)
        before_immediate = text[max(0, m.start(1) - 6) : m.start(1)].lower()
        after_immediate = text[m.end(1) : min(len(text), m.end(1) + 4)]
        if "pse-" in before_immediate or after_immediate.startswith("."):
            continue
        p = registry.by_cedula.get(digits)
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=m.end(1),
                    surface=surf,
                    kind="cedula",
                    candidates=[p],
                    priority=96,
                )
            )
        else:
            before_ctx = text[max(0, m.start(1) - 35) : m.start(1)].lower()
            if re.search(r"\b(?:c[eé]dula|cc|c\.c\.|documento|identificaci[oó]n|id)\b", before_ctx):
                cands = [focus_person] if focus_person else []
                spans.append(
                    CandidateSpan(
                        start=m.start(1),
                        end=m.end(1),
                        surface=surf,
                        kind="cedula",
                        candidates=cands,
                        priority=94,
                    )
                )

    # 6b. Obfuscated cedulas/phones with strange separators (for canary/adversarial tests)
    for m in RX_CEDULA_OBFUSCATED.finditer(text):
        surf = m.group(1)
        digits = solo_digitos(surf)
        if 7 <= len(digits) <= 10 and digits != KIPU_NIT_DIGITS:
            p = registry.by_cedula.get(digits) or registry.by_phone.get(digits)
            cands = [p] if p else ([focus_person] if focus_person else [])
            kind = "phone" if (len(digits) == 10 and digits.startswith("3")) else "cedula"
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=m.end(1),
                    surface=surf,
                    kind=kind,
                    candidates=cands,
                    priority=96,
                )
            )

    # 7. Addresses
    for m in RX_ADDR.finditer(text):
        surf = m.group(0)
        nad = norm(surf)
        if nad.startswith(KIPU_OFFICE_ADDR_NORM):
            continue
        p = registry.by_address_norm.get(nad)
        if p is None:
            for k_ad, cand_p in registry.by_address_norm.items():
                if nad in k_ad or k_ad in nad:
                    p = cand_p
                    break
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="address",
                candidates=cands,
                priority=96,
            )
        )

    # 8. Birth dates (ISO, Spanish, Slash)
    for m in RX_BIRTH_ISO.finditer(text):
        surf = m.group(0)
        cands = [p for p in registry.persons.values() if p.birth_iso == surf]
        if not cands and focus_person:
            cands = [focus_person]
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="birth_iso",
                candidates=cands,
                priority=93,
            )
        )
    for m in RX_BIRTH_SPANISH.finditer(text):
        surf = m.group(0)
        cands = [focus_person] if focus_person else []
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="birth_spanish",
                candidates=cands,
                priority=92,
            )
        )
    for m in RX_BIRTH_SLASH.finditer(text):
        surf = m.group(0)
        cands = [focus_person] if focus_person else []
        spans.append(
            CandidateSpan(
                start=m.start(),
                end=m.end(),
                surface=surf,
                kind="birth_slash",
                candidates=cands,
                priority=92,
            )
        )

    # 9. Plates, Passports, Accounts in context
    for m in RX_PLATE_CTX.finditer(text):
        surf = m.group(1)
        pl_n = re.sub(r"[^a-z0-9]", "", norm(surf))
        p = registry.by_plate.get(pl_n)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(1),
                end=m.end(1),
                surface=surf,
                kind="plate",
                candidates=cands,
                priority=94,
            )
        )
    for m in RX_PASSPORT_CTX.finditer(text):
        surf = m.group(1)
        pp_n = re.sub(r"[^a-z0-9]", "", norm(surf))
        p = registry.by_passport.get(pp_n)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(1),
                end=m.end(1),
                surface=surf,
                kind="passport",
                candidates=cands,
                priority=94,
            )
        )
    for m in RX_ACCOUNT_CTX.finditer(text):
        surf = m.group(1)
        ac_d = solo_digitos(surf)
        p = registry.by_account.get(ac_d)
        cands = [p] if p else ([focus_person] if focus_person else [])
        spans.append(
            CandidateSpan(
                start=m.start(1),
                end=m.end(1),
                surface=surf,
                kind="account",
                candidates=cands,
                priority=95,
            )
        )

    # 10. Last-4 digits of cedula or phone for focus_person (and its homonyms) in chats/filenames
    if focus_person is not None:
        check_persons = [focus_person]
        if focus_person.short_name:
            for hom in registry.by_short_name_norm.get(norm(focus_person.short_name), []):
                if hom is not focus_person:
                    check_persons.append(hom)
        for m in RX_LAST4_TOKEN.finditer(text):
            d4 = m.group(1)
            before_ctx = text[max(0, m.start(1) - 35) : m.start(1)].lower()
            has_explicit_last4_cue = bool(
                re.search(r"(?:img_|termina(?:da|do)?\s+en|ending\s+in|[uú]ltimos\s+(?:4|cuatro))", before_ctx)
            )
            if not has_explicit_last4_cue and d4 in {"2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "2027"}:
                continue
            for cand_p in check_persons:
                if not has_explicit_last4_cue and cand_p.birth_iso and d4 == cand_p.birth_iso[:4]:
                    continue
                if cand_p.cedula and len(cand_p.cedula) >= 6 and cand_p.cedula.endswith(d4):
                    spans.append(
                        CandidateSpan(
                            start=m.start(1),
                            end=m.end(1),
                            surface=d4,
                            kind="cedula_last4",
                            candidates=[cand_p],
                            priority=85,
                        )
                    )
                    break
                if cand_p.phone and len(cand_p.phone) >= 10 and cand_p.phone.endswith(d4):
                    spans.append(
                        CandidateSpan(
                            start=m.start(1),
                            end=m.end(1),
                            surface=d4,
                            kind="phone_last4",
                            candidates=[cand_p],
                            priority=85,
                        )
                    )
                    break

    return spans
