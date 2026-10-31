"""Surface-style rendering and non-overlapping span replacement for Kipu Pay anonymization."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING
from unidecode import unidecode

from app.normalize import encode_spelled_email, norm, solo_digitos
from app.pseudonym import format_cedula_dots

if TYPE_CHECKING:
    from app.registry import KeptEntity, PersonEntity

MES_NOMBRE = {
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


@dataclass
class ResolvedSpan:
    start: int
    end: int
    surface: str
    replacement: str
    person: PersonEntity | None = None
    kept_entity: KeptEntity | None = None
    attr_type: str = ""
    record_original: bool = True


def render_name_surface(orig_surface: str, target_name: str) -> str:
    """Render `target_name` mirroring the surface style of `orig_surface`."""
    s = orig_surface.strip()
    if "_" in s and " " not in s:
        return norm(target_name).replace(" ", "_")
    if "-" in s and " " not in s:
        return norm(target_name).replace(" ", "-")

    alpha_chars = [c for c in s if c.isalpha()]
    if alpha_chars and all(c.isupper() for c in alpha_chars):
        has_accent = any(c in "ÁÉÍÓÚÑÜ" for c in s)
        if not has_accent:
            return unidecode(target_name).upper()
        return target_name.upper()
    if alpha_chars and all(c.islower() for c in alpha_chars):
        has_accent = any(c in "áéíóúñü" for c in s)
        if not has_accent:
            return unidecode(target_name).lower()
        return target_name.lower()
    return target_name


def render_cedula_surface(orig_surface: str, fake_cedula: str) -> str:
    """Render `fake_cedula` preserving dots or spaces if present in `orig_surface`."""
    digits = solo_digitos(fake_cedula)
    if "." in orig_surface:
        return format_cedula_dots(digits)
    if " " in orig_surface.strip():
        return format_cedula_dots(digits).replace(".", " ")
    return digits


def render_phone_surface(orig_surface: str, fake_phone: str) -> str:
    """Render `fake_phone` preserving +57 prefix, spaces, or hyphens."""
    digits = solo_digitos(fake_phone)[-10:]
    s = orig_surface.strip()
    if s.startswith("+57"):
        if " " in s:
            return f"+57 {digits[:3]} {digits[3:6]} {digits[6:]}"
        if "-" in s:
            return f"+57-{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return f"+57{digits}"
    if " " in s:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    if "-" in s:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def render_email_surface(orig_surface: str, fake_email: str) -> str:
    """Render `fake_email` as standard email or spelled email ('arroba'/'punto')."""
    if "@" not in orig_surface:
        return encode_spelled_email(fake_email, orig_surface)
    if orig_surface.isupper():
        return fake_email.upper()
    return fake_email


def render_birth_surface(orig_surface: str, fake_birth_iso: str) -> str:
    """Render `fake_birth_iso` (`YYYY-MM-DD`) in ISO, slash (`MM/DD/YYYY`), or Spanish text."""
    parts = fake_birth_iso.split("-")
    if len(parts) != 3:
        return fake_birth_iso
    y, m_str, d_str = parts
    m = int(m_str)
    d = int(d_str)
    s = orig_surface.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return fake_birth_iso
    if "/" in s:
        return f"{m:02d}/{d:02d}/{y}"
    if "de" in s.lower():
        mes_txt = MES_NOMBRE.get(m, "enero")
        return f"{d} de {mes_txt} de {y}"
    return fake_birth_iso


def render_plate_surface(orig_surface: str, fake_plate: str) -> str:
    """Render `fake_plate` with or without hyphen matching `orig_surface`."""
    clean_pl = re.sub(r"[^A-Z0-9]", "", fake_plate.upper())
    if len(clean_pl) == 6:
        if "-" in orig_surface:
            return f"{clean_pl[:3]}-{clean_pl[3:]}"
        if " " in orig_surface.strip():
            return f"{clean_pl[:3]} {clean_pl[3:]}"
        return clean_pl
    return fake_plate


def render_account_surface(orig_surface: str, fake_account: str) -> str:
    """Render `fake_account` preserving separator style."""
    if "-" in orig_surface and " " in fake_account:
        return fake_account.replace(" ", "-")
    if " " in orig_surface and "-" in fake_account:
        return fake_account.replace("-", " ")
    return fake_account


def render_handle_surface(orig_surface: str, fake_handle: str, fake_short_name: str = "") -> str:
    """Render Slack `<@U...>` or `@handle` mention."""
    s = orig_surface.strip()
    if s.startswith("<@") and s.endswith(">"):
        if fake_short_name:
            return f"@{fake_short_name}"
        return f"@{fake_handle}"
    if s.startswith("@"):
        return f"@{fake_handle}"
    return fake_handle


def apply_resolved_spans(text: str, spans: list[ResolvedSpan], record_id: str) -> str:
    """
    Replace all non-overlapping `ResolvedSpan`s in `text` and record observed originals
    and record IDs on each touched entity.
    """
    if not text or not spans:
        return text

    # Sort by start ascending, then longest first to deduplicate any accidental overlap
    sorted_asc = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    non_overlapping: list[ResolvedSpan] = []
    cur_end = -1
    for sp in sorted_asc:
        if sp.start < 0 or sp.end > len(text) or sp.start >= sp.end:
            continue
        if sp.start >= cur_end:
            non_overlapping.append(sp)
            cur_end = sp.end

    out = text
    for sp in reversed(non_overlapping):
        if sp.person is not None:
            if sp.record_original:
                if sp.attr_type == "uid_mention":
                    sp.person.record_hit(record_id, sp.surface, attr_type="name")
                    uid_inner = sp.surface.strip("<@> ")
                    if uid_inner:
                        sp.person.record_hit(record_id, uid_inner, attr_type="name")
                else:
                    clean_surf = re.sub(r"\s+", " ", sp.surface).strip()
                    sp.person.record_hit(record_id, clean_surf, attr_type=sp.attr_type)
            else:
                sp.person.record_hit(record_id, None, attr_type=sp.attr_type)
            out = out[: sp.start] + sp.replacement + out[sp.end :]
        elif sp.kept_entity is not None:
            clean_surf = re.sub(r"\s+", " ", sp.surface).strip()
            sp.kept_entity.record_hit(record_id, clean_surf)

    return out
