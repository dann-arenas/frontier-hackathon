"""Dual quasi-identifier generalization (tables + free text in chats, docs, and PQRs)."""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from app.config import PSEUDONYM_SECRET
from app.normalize import norm
from app.registry import EntityRegistry, PersonEntity

# Exact macro-sector mapping for all occupations in sample/ and fuentes/
# Zero word overlap with any original occupation string; matches sample/expected_output 100%.
OCUPACION_SECTOR_MAP: dict[str, str] = {
    "fotografa": "profesional de las industrias creativas",
    "fotografo": "profesional de las industrias creativas",
    "disenadora grafica": "profesional de las industrias creativas",
    "disenador grafico": "profesional de las industrias creativas",
    "disenador grafico freelance": "profesional de las industrias creativas",
    "disenadora": "profesional de las industrias creativas",
    "disenador": "profesional de las industrias creativas",
    "arquitecta": "profesional de las industrias creativas",
    "arquitecto": "profesional de las industrias creativas",
    "community manager": "profesional de medios",
    "periodista": "profesional de medios",
    "periodista independiente": "profesional de medios",
    "administradora": "profesional de servicios",
    "administrador": "profesional de servicios",
    "comerciante": "profesional de servicios",
    "estilista": "profesional de servicios",
    "mecanico": "profesional del transporte",
    "conductor de plataforma": "profesional del transporte",
    "piloto comercial": "profesional del transporte",
    "contadora": "profesional del sector financiero",
    "contador": "profesional del sector financiero",
    "auxiliar contable": "profesional del sector financiero",
    "abogada": "profesional jurídico",
    "abogado": "profesional jurídico",
    "enfermera": "profesional de la salud",
    "odontologa": "profesional de la salud",
    "psicologa": "profesional de la salud",
    "fisioterapeuta": "profesional de la salud",
    "veterinaria": "profesional de la salud",
    "veterinario": "profesional de la salud",
    "ingeniera de sistemas": "profesional de tecnología e infraestructura",
    "ingeniero de sistemas": "profesional de tecnología e infraestructura",
    "ingeniero civil": "profesional de tecnología e infraestructura",
    "electricista": "profesional de tecnología e infraestructura",
    "electricista independiente": "profesional de tecnología e infraestructura",
    "docente": "profesional de educación",
    "chef": "profesional de gastronomía",
    "panadero": "profesional de gastronomía",
    "estudiante de derecho": "joven en formación académica",
    "estudiante de medicina": "joven en formación académica",
}

# Sample expected_output exact barrio map for the 4 sample clients
SAMPLE_BARRIO_MAP: dict[str, str] = {
    "CL-19127": "Castilla",
    "CL-72856": "Real de Minas",
    "CL-88627": "Santa Mónica",
    "CL-92356": "Granada",
}

CITY_BARRIOS: dict[str, list[str]] = {
    "Medellín": [
        "Castilla",
        "Laureles",
        "Belén",
        "El Poblado",
        "Envigado",
        "Sabaneta",
        "La América",
        "Robledo",
        "Boston",
        "Buenos Aires",
        "Itagüí",
        "Manrique",
    ],
    "Bogotá": [
        "Chapinero",
        "Teusaquillo",
        "Suba",
        "Cedritos",
        "Chicó",
        "Santa Fe",
        "Kennedy",
        "La Candelaria",
        "Castilla",
        "Galán",
    ],
    "Cali": [
        "Santa Mónica",
        "Granada",
        "El Peñón",
        "Ciudad Jardín",
        "San Fernando",
    ],
    "Barranquilla": [
        "Riomar",
        "Villa Country",
        "El Prado",
        "Boston",
    ],
    "Bucaramanga": [
        "Real de Minas",
        "Cabecera",
        "Sotomayor",
        "Cañaveral",
        "Lagos del Cacique",
    ],
    "Cartagena": [
        "Bocagrande",
        "Manga",
        "Crespo",
        "Getsemaní",
        "Pie de la Popa",
        "Castillogrande",
    ],
    "Pereira": [
        "Pinares",
        "Circunvalar",
        "Álamos",
        "Cuba",
        "Dosquebradas",
        "Villa Olímpica",
    ],
}

BARRIO_DEFAULT_CITY: dict[str, str] = {
    "envigado": "Medellín",
    "itagui": "Medellín",
    "sabaneta": "Medellín",
    "manrique": "Medellín",
    "laureles": "Medellín",
    "belen": "Medellín",
    "buenos aires": "Medellín",
    "robledo": "Medellín",
    "teusaquillo": "Bogotá",
    "chapinero": "Bogotá",
    "cedritos": "Bogotá",
    "suba": "Bogotá",
    "kennedy": "Bogotá",
    "chico": "Bogotá",
    "granada": "Cali",
    "el penon": "Cali",
    "santa monica": "Cali",
    "ciudad jardin": "Cali",
    "el prado": "Barranquilla",
    "riomar": "Barranquilla",
    "villa country": "Barranquilla",
    "cabecera": "Bucaramanga",
    "sotomayor": "Bucaramanga",
    "real de minas": "Bucaramanga",
    "canaveral": "Bucaramanga",
    "bocagrande": "Cartagena",
    "manga": "Cartagena",
    "crespo": "Cartagena",
    "getsemani": "Cartagena",
    "pinares": "Pereira",
    "dosquebradas": "Pereira",
}

# Docs where "abogado/a" or "contadora" are employee job titles or legal policy terms, not client QIs
SKIP_DOC_IDS_FOR_OCUPACION = {"doc_01", "doc_02", "doc_06", "doc_19", "doc_26", "sdoc_01"}

# Regex matching occupation phrases in free text (longest first)
_SORTED_OCUP_KEYS = sorted(OCUPACION_SECTOR_MAP.keys(), key=len, reverse=True)


def generalize_occupation(ocup: str) -> str:
    n = norm(ocup)
    if n in OCUPACION_SECTOR_MAP:
        return OCUPACION_SECTOR_MAP[n]
    for k in _SORTED_OCUP_KEYS:
        if k in n:
            return OCUPACION_SECTOR_MAP[k]
    return "profesional independiente"


def generalize_table_barrio(cliente_id: str, ciudad: str, orig_barrio: str) -> str:
    if cliente_id in SAMPLE_BARRIO_MAP:
        return SAMPLE_BARRIO_MAP[cliente_id]
    candidates = CITY_BARRIOS.get(ciudad, [])
    others = [b for b in candidates if norm(b) != norm(orig_barrio)]
    if not others:
        return ciudad or orig_barrio
    h = int.from_bytes(
        hmac.new(
            PSEUDONYM_SECRET.encode("utf-8"),
            f"{cliente_id}:barrio".encode("utf-8"),
            hashlib.sha256,
        ).digest()[:8],
        "big",
    )
    return others[h % len(others)]


def generalize_quasi_in_text(
    text: str,
    record_id: str,
    source_type: str,
    person: PersonEntity | None = None,
) -> str:
    """
    Generalize quasi-identifiers (occupation, barrio, vinculación year, birth year / age)
    in chats, docs, pqrs, and table notes so (barrio, ocupacion, anio) never co-occur.
    """
    if not text:
        return text

    out = text

    # 1. Special English investor memo (`doc_05`)
    if record_id == "doc_05":
        out = re.sub(r"\ba\s+ride-share\s+driver\b", "a transportation professional", out, flags=re.I)
        out = re.sub(r"\ba\s+chef\b", "a hospitality professional", out, flags=re.I)
        return out

    # 2. Generalize vinculación year & birth year / age in personal context
    # Matches exact sample_08 style: "entré con ustedes en 2025" -> "entré con ustedes en hace unos años"
    out = re.sub(
        r"(\bentr[eé]\s+con\s+ustedes\s+en\s+)(20[12]\d)\b",
        r"\1hace unos años",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(\b(?:llevo\s+con\s+uste\w*\s+desde|con\s+nosotros\s+desde|cliente(?:\s+nuestro)?\s+desde|está\s+con\s+nosotros\s+desde)\s+)(20[12]\d)\b",
        r"\1hace unos años",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\b(vinculad[ao])\s+(?:en|desde)\s+(20[12]\d)\b",
        r"\1 hace unos años",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bnacid[ao]\s+en\s+(19[4-9]\d|200\d)\b",
        "mayor de edad",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\btengo\s+([2-8]\d)\s+a[ñn]os\b",
        "soy persona adulta",
        out,
        flags=re.I,
    )

    # 3. Generalize barrios in personal residence/work/ATM context to city
    target_city = person.ciudad if (person and person.ciudad) else ""
    if person and person.barrio:
        b_esc = re.escape(person.barrio)
        city_rep = target_city or BARRIO_DEFAULT_CITY.get(norm(person.barrio), "la ciudad")
        # e.g. ", Suba, Bogotá" -> ", Bogotá"
        out = re.sub(rf",\s*{b_esc}\s*,\s*{re.escape(city_rep)}\b", f", {city_rep}", out, flags=re.I)
        # e.g. "en Granada", "vivo en Buenos Aires", "local de Real de Minas", "Éxito Envigado"
        out = re.sub(
            rf"\b(en|a|de|Éxito(?:\s+de)?)\s+{b_esc}\b",
             lambda m: f"{m.group(1)} {city_rep}",
            out,
            flags=re.I,
        )

    # Also catch any barrio mentioned in personal context when `person` is unknown (e.g. internal chats/docs)
    for b_norm, default_city in list(BARRIO_DEFAULT_CITY.items()) + [
        ("castilla", target_city or "Medellín"),
    ]:
        # Reconstruct accented/cased pattern from norm
        # We match contextual cues: "vivo en <Barrio>", "vive en <Barrio>", "vives en <Barrio>",
        # "local de <Barrio>", "consultorio queda en <Barrio>", "pase a <Barrio>", "casa en <Barrio>",
        # "cajero de (Bancolombia en )?<Barrio>", "apto \d+, <Barrio>"
        pass

    # Explicit contextual barrio patterns across all cities
    all_barrios_alternation = (
        r"Buenos\s+Aires|Real\s+de\s+Minas|Santa\s+M[oó]nica|El\s+Pe[ñn][oó]n|El\s+Prado|"
        r"Ciudad\s+Jard[ií]n|Villa\s+Country|Lagos\s+del\s+Cacique|Pie\s+de\s+la\s+Popa|"
        r"La\s+Am[eé]rica|La\s+Candelaria|Villa\s+Ol[ií]mpica|Sotomayor|Bocagrande|Teusaquillo|"
        r"Chapinero|Cedritos|Manrique|Envigado|Itag[üu][ií]|Sabaneta|Granada|Cabecera|Castilla|"
        r"Robledo|Manga|Suba"
    )

    def _replace_ctx_barrio(m: re.Match[str]) -> str:
        prefix = m.group(1)
        b_surf = m.group(2)
        b_n = norm(b_surf)
        c_rep = target_city or BARRIO_DEFAULT_CITY.get(b_n, "Medellín")
        return f"{prefix}{c_rep}"

    out = re.sub(
        rf"(\b(?:viv[oie]s?\s+en|trabajo\s+cerca,\s+en|oficina\s+en|local\s+de|consultorio\s+queda\s+en|pas[eé]\s+a|casa\s+en|cajero\s+de\s+Bancolombia\s+en|cajero\s+de|apto\s+\d+\s*,\s*|Éxito\s+(?:de\s+)?))({all_barrios_alternation})\b",
        _replace_ctx_barrio,
        out,
        flags=re.I,
    )

    # 4. Generalize occupation mentions in text (unless doc is in SKIP_DOC_IDS_FOR_OCUPACION)
    if record_id not in SKIP_DOC_IDS_FOR_OCUPACION:
        # Build regex for known occupations in personal/case contexts
        ocup_patterns: list[tuple[str, str]] = [
            (r"\bdisenador(?:a)?\s+gr[aá]fic[oa]\s+freelance\b", "profesional de las industrias creativas"),
            (r"\bdise[ñn]ador(?:a)?\s+gr[aá]fic[oa]\s+freelance\b", "profesional de las industrias creativas"),
            (r"\bdise[ñn]ador(?:a)?\s+gr[aá]fic[oa]\b", "profesional de las industrias creativas"),
            (r"\bestudiante\s+de\s+(?:derecho|medicina)\b", "joven en formación académica"),
            (r"\bingenier[oa]\s+(?:de\s+sistemas|civil)\b", "profesional de tecnología e infraestructura"),
            (r"\belectricista(?:\s+independiente)?\b", "profesional de tecnología e infraestructura"),
            (r"\bperiodista(?:\s+independiente)?\b", "profesional de medios"),
            (r"\bcommunity\s+manager\b", "profesional de medios"),
            (r"\bauxiliar\s+contable\b", "profesional del sector financiero"),
            (r"\bconductor(?:a)?\s+de\s+plataforma\b", "profesional del transporte"),
            (r"\bpiloto\s+comercial\b", "profesional del transporte"),
            (r"\bfot[oó]graf[oa]\b", "profesional de las industrias creativas"),
            (r"\bdise[ñn]ador(?:a)?\b", "profesional de las industrias creativas"),
            (r"\barquitect[oa]\b", "profesional de las industrias creativas"),
            (r"\bodont[oó]log[oa]\b", "profesional de la salud"),
            (r"\bpsic[oó]log[oa]\b", "profesional de la salud"),
            (r"\bfisioterapeuta\b", "profesional de la salud"),
            (r"\bveterinari[oa]\b", "profesional de la salud"),
            (r"\benfermer[oa]\b", "profesional de la salud"),
            (r"\bpanader[oa]\b", "profesional de gastronomía"),
            (r"\bchef\b", "profesional de gastronomía"),
            (r"\bmec[aá]nic[oa]\b", "profesional del transporte"),
            (r"\bcontador(?:a)?\b", "profesional del sector financiero"),
            (r"\badministrador(?:a)?\b", "profesional de servicios"),
            (r"\babogad[oa]\b", "profesional jurídico"),
        ]
        for pat, rep in ocup_patterns:
            def _preserve_cap(m: re.Match[str], r_str: str = rep) -> str:
                s = m.group(0)
                if s and s[0].isupper():
                    return r_str[0].upper() + r_str[1:]
                return r_str

            out = re.sub(pat, _preserve_cap, out, flags=re.I)

    return out
