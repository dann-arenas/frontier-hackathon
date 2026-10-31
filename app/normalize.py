"""Exact Python port of validar.mjs normalization, offset mapping, and spelled-email decoding."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

ESPACIO_CHARS = (
    "\t\n\v\f\r \x1c\x1d\x1e\x1f\x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
ESPACIO_SET = set(ESPACIO_CHARS)

CONFUSABLES: dict[str, str] = {
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0445": "x",
    "\u0443": "y",
    "\u0456": "i",
    "\u0458": "j",
    "\u0455": "s",
    "\u0410": "A",
    "\u0415": "E",
    "\u041e": "O",
    "\u0420": "P",
    "\u0421": "C",
    "\u0425": "X",
    "\u0423": "Y",
    "\u0406": "I",
    "\u0408": "J",
    "\u0405": "S",
    "\u0412": "B",
    "\u041d": "H",
    "\u041a": "K",
    "\u041c": "M",
    "\u0422": "T",
    "\u03bf": "o",
    "\u03b1": "a",
    "\u03c1": "p",
    "\u03bd": "v",
    "\u03b9": "i",
    "\u2113": "l",
    "\u0131": "i",
    "\u0251": "a",
}

DIGIT_SEPARATORS = set(ESPACIO_CHARS + ".,'\u2019`\xb4\xb7/\\-\u2013\u2014_")
RX_ESPACIOS = re.compile(r" {2,}")
RX_BLANCOS = re.compile(f"[{re.escape(ESPACIO_CHARS)}]+")


def _is_word_char(c: str) -> bool:
    if c == "_":
        return True
    cat = unicodedata.category(c)
    return cat.startswith("L") or cat.startswith("N")


def _is_nd(c: str) -> bool:
    return unicodedata.category(c) == "Nd"


def _is_rare_space(c: str) -> bool:
    return c in "\t\r\n\v\f" or unicodedata.category(c) == "Zs"


def plegar(s: str) -> str:
    return s.lower().replace("ß", "ss").replace("ς", "\u03c3").replace("ſ", "s")


def quitar_tildes(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=65536)
def clean(s: str) -> str:
    base = unicodedata.normalize("NFKC", s)
    out: list[str] = []
    for c in base:
        c = CONFUSABLES.get(c, c)
        if unicodedata.category(c) == "Cf":
            continue
        out.append(" " if _is_rare_space(c) else c)
    return RX_ESPACIOS.sub(" ", "".join(out))


@lru_cache(maxsize=65536)
def norm(s: str) -> str:
    return plegar(quitar_tildes(clean(s)))


def norm_with_offsets(s: str) -> tuple[str, list[int], list[int]]:
    """
    Compute (norm_str, starts, ends) where norm_str[k] came from s[starts[k]:ends[k]].
    Matches norm(s) character-for-character while preserving exact character spans in s.
    """
    out_chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    prev_was_space = False
    for idx, raw_ch in enumerate(s):
        if unicodedata.category(raw_ch) == "Mn":
            # Combining mark attached to previous character: extend previous end
            if ends:
                ends[-1] = idx + 1
            continue
        nfkc = unicodedata.normalize("NFKC", raw_ch)
        for c in nfkc:
            c = CONFUSABLES.get(c, c)
            if unicodedata.category(c) == "Cf":
                continue
            if _is_rare_space(c):
                c = " "
            nfd = unicodedata.normalize("NFD", c)
            base_c = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
            if not base_c:
                if ends:
                    ends[-1] = idx + 1
                continue
            folded = plegar(base_c)
            for fc in folded:
                if fc == " ":
                    if prev_was_space:
                        # Collapsed space
                        continue
                    prev_was_space = True
                else:
                    prev_was_space = False
                out_chars.append(fc)
                starts.append(idx)
                ends.append(idx + 1)

    return "".join(out_chars), starts, ends


@lru_cache(maxsize=65536)
def digitos_compactos(s: str) -> str:
    cl = clean(s)
    n = len(cl)
    out: list[str] = []
    for i, c in enumerate(cl):
        if c in DIGIT_SEPARATORS and 0 < i < n - 1 and _is_nd(cl[i - 1]) and _is_nd(cl[i + 1]):
            continue
        out.append(c)
    return "".join(out)


def solo_digitos(s: str) -> str:
    return "".join(c for c in s if _is_nd(c))


def partir(s: str) -> list[str]:
    return [x for x in RX_BLANCOS.split(s) if x]


def digitos_en(haystack: str, digitos: str) -> bool:
    if not digitos:
        return False
    compacto = digitos_compactos(haystack)
    candidatos = {digitos}
    if len(digitos) == 12 and digitos.startswith("57"):
        candidatos.add(digitos[2:])
    for d in candidatos:
        if d not in compacto:
            continue
        for m in re.finditer(rf"(?:57)?{re.escape(d)}", compacto):
            st, en = m.span()
            if st > 0 and _is_nd(compacto[st - 1]):
                continue
            if en < len(compacto) and _is_nd(compacto[en]):
                continue
            return True
    return False


def find_digit_spans_in_text(text: str, target_digits: str) -> list[tuple[int, int]]:
    """
    Find all character spans (start, end) in `text` whose compact digits match `target_digits`
    (with optional '57' prefix), respecting non-digit boundaries.
    """
    if not target_digits:
        return []
    # Build sequence of digits and their positions in original text
    # We allow single separators from DIGIT_SEPARATORS between consecutive digits
    spans: list[tuple[int, int]] = []
    candidatos = [target_digits]
    if len(target_digits) == 10 and target_digits.startswith("3"):
        candidatos.append("57" + target_digits)
    elif len(target_digits) == 12 and target_digits.startswith("57"):
        candidatos.append(target_digits[2:])

    # Collect digit runs where digits are separated by at most 1 separator char (or "+57 ")
    n = len(text)
    i = 0
    while i < n:
        if _is_nd(text[i]) or (text[i] == "+" and i + 1 < n and _is_nd(text[i + 1])):
            has_plus = text[i] == "+"
            run_digits: list[str] = []
            run_starts: list[int] = []
            run_ends: list[int] = []
            j = i + (1 if has_plus else 0)
            while j < n:
                if _is_nd(text[j]):
                    run_digits.append(text[j])
                    run_starts.append(i if (has_plus and not run_starts) else j)
                    run_ends.append(j + 1)
                    j += 1
                elif (
                    text[j] in DIGIT_SEPARATORS
                    and j + 1 < n
                    and _is_nd(text[j + 1])
                ):
                    j += 1
                else:
                    break
            d_str = "".join(run_digits)
            if d_str in candidatos:
                spans.append((run_starts[0], run_ends[-1]))
            i = j
        else:
            i += 1
    return spans


def _is_rx_texto(n: str) -> bool:
    for c in n:
        if not (_is_word_char(c) or c in " .'_-"):
            return False
    return bool(n)


def contiene(haystack: str, needle: str, exacto: bool = False) -> bool:
    """Exact port of contiene(haystack, needle, opciones) from validar.mjs."""
    if not needle:
        return False
    if exacto:
        return needle in haystack
    digitos = solo_digitos(needle)
    if (
        digitos
        and len(digitos) >= 6
        and len(digitos) >= 0.6 * len(needle)
        and not re.search(r"[a-zA-Z]", needle)
    ):
        return digitos_en(haystack, digitos)
    h = norm(haystack)
    n = norm(needle)
    if _is_rx_texto(n) and re.search(r"[a-z]", n):
        if n in h and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", h):
            return True
        palabras = partir(n)
        if len(palabras) >= 2 and all(len(w) >= 3 and w in h for w in palabras):
            sep = f"[{re.escape(ESPACIO_CHARS)}]*"
            pat = rf"(?<![a-z0-9]){sep.join(re.escape(w) for w in palabras)}(?![a-z0-9])"
            return bool(re.search(pat, h))
        return False
    return n in h


def es_nombre_suelto(s: str) -> bool:
    """Exact port of esNombreSuelto(s) from validar.mjs."""
    if not s:
        return False
    primero = s[0]
    return (
        " " not in s
        and "@" not in s
        and not any(_is_nd(c) for c in s)
        and primero.upper() == primero
        and primero.lower() != primero
    )


def plano(v: Any) -> list[str]:
    """Exact port of plano(v) from validar.mjs."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, bool):
        return ["True" if v else "False"]
    if isinstance(v, (int, float)):
        return [str(v)]
    if isinstance(v, list):
        out: list[str] = []
        for item in v:
            out.extend(plano(item))
        return out
    if isinstance(v, dict):
        out = []
        for k, x in v.items():
            out.append(str(k))
            out.extend(plano(x))
        return out
    return [str(v)]


def texto_de_registro(rec: dict[str, Any], campo: str | None = None) -> str:
    """Exact port of textoDeRegistro(rec, campo) from validar.mjs."""
    if campo is None or campo in ("", "any", "raw", "*"):
        sin_id = {k: v for k, v in rec.items() if k != "id"}
        return " | ".join(plano(sin_id))
    if campo.startswith("columns."):
        col = campo.split(".", 1)[1]
        cols = rec.get("columns") if isinstance(rec.get("columns"), dict) else {}
        if col == "*":
            return " | ".join(plano(cols))
        return " | ".join(plano(cols.get(col)))
    return " | ".join(plano(rec.get(campo)))


# Spelled email regex: e.g. "mcastano arroba gmail punto com" or "juan punto perez arroba kipupay punto co"
RX_SPELLED_EMAIL = re.compile(
    r"\b([a-zA-Z0-9_]+(?:\s+(?:punto|guion(?:\s+bajo)?)\s+[a-zA-Z0-9_]+)*)"
    r"\s+arroba\s+"
    r"([a-zA-Z0-9_]+(?:\s+guion\s+[a-zA-Z0-9_]+)*(?:\s+punto\s+[a-zA-Z]{2,10})+)\b",
    re.IGNORECASE,
)


def decode_spelled_email(surface: str) -> str:
    """Convert a spelled-out email ('juan punto perez arroba gmail punto com') to 'juan.perez@gmail.com'."""
    s = surface.strip()
    s = re.sub(r"\s+guion\s+bajo\s+", "_", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+guion\s+", "-", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+punto\s+", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+arroba\s+", "@", s, flags=re.IGNORECASE)
    return s.lower()


def encode_spelled_email(email: str, template_surface: str | None = None) -> str:
    """Convert 'doris.lozano@kipupay.co' into 'doris punto lozano arroba kipupay punto co'."""
    local, _, domain = email.partition("@")
    local_spelled = (
        local.replace("_", " guion bajo ")
        .replace("-", " guion ")
        .replace(".", " punto ")
    )
    domain_spelled = (
        domain.replace("-", " guion ")
        .replace(".", " punto ")
    )
    res = f"{local_spelled} arroba {domain_spelled}"
    if template_surface and template_surface.isupper():
        return res.upper()
    return res
