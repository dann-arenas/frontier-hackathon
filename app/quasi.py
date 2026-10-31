"""Dual quasi-identifier generalization (tables + free text in chats, docs, and PQRs)."""
from __future__ import annotations

import hashlib
import hmac
import re

from app.config import PSEUDONYM_SECRET
from app.normalize import norm
from app.registry import PersonEntity

# Macro-sector mapping for occupations (zero word overlap with original occupation strings).
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
    "medico": "profesional de la salud",
    "medica": "profesional de la salud",
    "ingeniera de sistemas": "profesional de tecnología e infraestructura",
    "ingeniero de sistemas": "profesional de tecnología e infraestructura",
    "ingeniero civil": "profesional de tecnología e infraestructura",
    "ingeniera civil": "profesional de tecnología e infraestructura",
    "electricista": "profesional de tecnología e infraestructura",
    "electricista independiente": "profesional de tecnología e infraestructura",
    "docente": "profesional de educación",
    "profesor": "profesional de educación",
    "profesora": "profesional de educación",
    "chef": "profesional de gastronomía",
    "panadero": "profesional de gastronomía",
    "panadera": "profesional de gastronomía",
    "estudiante de derecho": "joven en formación académica",
    "estudiante de medicina": "joven en formación académica",
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
    "el poblado": "Medellín",
    "la america": "Medellín",
    "buenos aires": "Medellín",
    "robledo": "Medellín",
    "castilla": "Medellín",
    "teusaquillo": "Bogotá",
    "chapinero": "Bogotá",
    "cedritos": "Bogotá",
    "suba": "Bogotá",
    "kennedy": "Bogotá",
    "chico": "Bogotá",
    "la candelaria": "Bogotá",
    "granada": "Cali",
    "el penon": "Cali",
    "santa monica": "Cali",
    "ciudad jardin": "Cali",
    "san fernando": "Cali",
    "el prado": "Barranquilla",
    "riomar": "Barranquilla",
    "villa country": "Barranquilla",
    "cabecera": "Bucaramanga",
    "sotomayor": "Bucaramanga",
    "real de minas": "Bucaramanga",
    "canaveral": "Bucaramanga",
    "lagos del cacique": "Bucaramanga",
    "bocagrande": "Cartagena",
    "manga": "Cartagena",
    "crespo": "Cartagena",
    "getsemani": "Cartagena",
    "pie de la popa": "Cartagena",
    "castillogrande": "Cartagena",
    "pinares": "Pereira",
    "dosquebradas": "Pereira",
    "villa olimpica": "Pereira",
    "cuba": "Pereira",
    "circunvalar": "Pereira",
    "alamos": "Pereira",
    "galan": "Bogotá",
    "santa fe": "Bogotá",
    "boston": "Barranquilla",
    "pance": "Cali",
}

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
    in chats, docs, pqrs, and table notes (both Spanish and English) so (barrio, ocupacion, anio)
    never co-occur.
    """
    if not text:
        return text

    out = text

    # 1. English quasi-identifier generalization (applies to any English document or chat)
    english_ocup_patterns: list[tuple[str, str]] = [
        (r"\ba\s+ride-share\s+driver\b", "a transportation professional"),
        (r"\b(?:an?\s+)?(?:uber|taxi|platform|delivery)\s+driver\b", "a transportation professional"),
        (r"\ba\s+commercial\s+pilot\b", "a transportation professional"),
        (r"\ba\s+mechanic\b", "a transportation professional"),
        (r"\ba\s+(?:chef|baker)\b", "a hospitality professional"),
        (r"\b(?:a\s+)?(?:freelance\s+)?(?:graphic\s+designer|photographer|architect|designer)\b", "a creative industry professional"),
        (r"\b(?:a\s+)?(?:systems\s+engineer|civil\s+engineer|software\s+engineer|electrician)\b", "a technology professional"),
        (r"\b(?:a\s+(?:freelance\s+)?|freelance\s+)(?:journalist|community\s+manager)\b", "a media professional"),
        (r"\b(?:a\s+)?(?:dentist|psychologist|physical\s+therapist|veterinarian|nurse)\b", "a healthcare professional"),
        (r"\b(?:an?\s+)?(?:accountant|accounting\s+assistant|bookkeeper)\b", "a finance professional"),
        (r"\b(?:a\s+)?(?:lawyer|attorney)\b(?!\s+(?:at|for)\s+(?:Kipu|Págalo|Pagalo))", "a legal professional"),
        (r"\b(?:a\s+)?(?:law|medical)\s+student\b", "a university student"),
    ]
    for pat_en, rep_en in english_ocup_patterns:
        out = re.sub(pat_en, rep_en, out, flags=re.I)

    out = re.sub(
        r"(\b(?:joined(?:\s+us)?\s+in|client\s+since|with\s+us\s+since|customer\s+since)\s+)(20[12]\d)\b",
        r"\1a few years ago",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bborn\s+in\s+(19[4-9]\d|200\d)\b",
        "of legal age",
        out,
        flags=re.I,
    )

    # 2. Spanish vinculación year, birth year, age, and exact birthday day+month
    out = re.sub(
        r"(\bentr[eé]\s+con\s+ustedes\s+en\s+)(20[12]\d)\b",
        r"\1hace unos años",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(\b(?:llevo\s+con\s+uste\w*\s+desde|con\s+nosotros\s+desde|cliente(?:\s+nuestro)?\s+desde|est[aá]\s+con\s+nosotros\s+desde)\s+)(20[12]\d)\b",
        r"\1hace unos años",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"\bllevo\s+desde\s+(?:el\s+)?(20[12]\d)\s+(con\s+(?:Kipu(?:\s+Pay)?|P[áa]galo|ustedes))\b",
        r"llevo varios años \2",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(\bvinculad[ao]s?\s+a\s+(?:Kipu(?:\s+Pay)?|P[áa]galo)\s+(?:en|desde)\s+(?:el\s+)?)(20[12]\d)\b",
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
        r"(\bcon\s+producto\s+propio\s+desde\s+)(20[12]\d)\b",
        r"\1hace unos años",
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
    out = re.sub(
        r"\bcumple\s+a[ñn]os\s+el\s+\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b",
        "cumple años en este trimestre",
        out,
        flags=re.I,
    )

    # 3. Generalize barrios in personal residence/work/ATM/store context to city
    target_city = person.ciudad if (person and person.ciudad) else ""
    if person and person.barrio:
        b_esc = re.escape(person.barrio)
        city_rep = target_city or BARRIO_DEFAULT_CITY.get(norm(person.barrio), "la ciudad")
        out = re.sub(rf",\s*{b_esc}\s*,\s*{re.escape(city_rep)}\b", f", {city_rep}", out, flags=re.I)
        out = re.sub(
            rf"\b(en|a|de|Éxito(?:\s+de)?)\s+{b_esc}\b",
            lambda m: f"{m.group(1)} {city_rep}",
            out,
            flags=re.I,
        )

    all_barrios_alternation = (
        r"Buenos\s+Aires|Real\s+de\s+Minas|Santa\s+M[oó]nica|San\s+Fernando|El\s+Pe[ñn][oó]n|"
        r"El\s+Prado|El\s+Poblado|Ciudad\s+Jard[ií]n|Villa\s+Country|Lagos\s+del\s+Cacique|"
        r"Pie\s+de\s+la\s+Popa|La\s+Am[eé]rica|La\s+Candelaria|Villa\s+Ol[ií]mpica|Sotomayor|"
        r"Bocagrande|Teusaquillo|Chapinero|Cedritos|Manrique|Envigado|Itag[üu][ií]|Sabaneta|"
        r"Granada|Cabecera|Castilla|Robledo|Laureles|Bel[eé]n|Riomar|Manga|Suba|Kennedy|"
        r"Dosquebradas|Pinares|Circunvalar|[ÁA]lamos|Cuba|Chic[oó]|Gal[aá]n|Castillogrande|"
        r"Ca[ñn]averal|Crespo|Getseman[ií]|Boston|Pance"
    )

    def _replace_ctx_barrio(m: re.Match[str]) -> str:
        prefix = m.group(1)
        b_surf = m.group(2)
        b_n = norm(b_surf)
        c_rep = target_city or BARRIO_DEFAULT_CITY.get(b_n, "Medellín")
        return f"{prefix}{c_rep}"

    out = re.sub(
        rf"(\b(?:viv[oie]n?\s+en\s+|vives\s+en\s+|trabajo\s+cerca,\s+en\s+|oficina\s+(?:en|de)\s+|local\s+(?:de|en)\s+|consultorio\s+(?:queda\s+)?en\s+|pas[eé]\s+a\s+|casa\s+en\s+|pusimos\s+en\s+|est[aá]\s+en\s+|cajero\s+de\s+Bancolombia\s+en\s+|cajero\s+de\s+|apto\s+\d+\s*,\s*|Éxito\s+(?:de\s+)?|lives?\s+in\s+|living\s+in\s+|based\s+in\s+|office\s+in\s+))({all_barrios_alternation})\b",
        _replace_ctx_barrio,
        out,
        flags=re.I,
    )
    # Also collapse ", <Barrio>, <City>" when both appear together in any record
    out = re.sub(
        rf",\s*(?:{all_barrios_alternation})\s*,\s*(Bogot[aá]|Medell[ií]n|Cali|Barranquilla|Bucaramanga|Cartagena|Pereira)\b",
        r", \1",
        out,
        flags=re.I,
    )

    # 4. Generalize occupation mentions in text without touching corporate roles (`Cargo: Contadora`, `Abogado externo`, `Abogada Senior`)
    ocup_patterns: list[tuple[str, str]] = [
        (r"\bdise[ñn]ador(?:a)?\s+gr[aá]fic[oa]\s+freelance\b", "profesional de las industrias creativas"),
        (r"\bdisenador(?:a)?\s+gr[aá]fic[oa]\s+freelance\b", "profesional de las industrias creativas"),
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
        (r"\bcomerciantes?\b", "profesional de servicios"),
        (r"\bestilistas?\b", "profesional de servicios"),
        (r"\bdocentes?\b", "profesional de educación"),
        (r"\bprofesor(?:a|es|as)?\b", "profesional de educación"),
        (r"\badministrador(?:a)?\b(?!\s+de\s+(?:la\s+cuenta|sistemas|red|base))", "profesional de servicios"),
        # `contador/a` and `abogado/a`: skip corporate roles (`Contadora` in table/organigrama, `abogada senior`, `abogado externo`, `la SIC o un abogado`)
        (
            r"(?<!\|\s)(?<!Cargo:\s)\bcontador(?:a)?\b(?!\s+(?:senior|junior|general|de\s+(?:Kipu|Págalo|Pagalo)|\|))",
            "profesional del sector financiero",
        ),
        (
            r"(?<!\|\s)(?<!Cargo:\s)(?<!SIC\so\sun\s)\babogad[oa]\b(?!\s+(?:senior|junior|extern[oa]|intern[oa]|del\s+banco|de\s+(?:Kipu|Págalo|Pagalo)|\|))",
            "profesional jurídico",
        ),
    ]

    for pat, rep in ocup_patterns:
        def _replace_ocup(m: re.Match[str], r_str: str = rep) -> str:
            s = m.group(0)
            st = m.start()
            before = out[max(0, st - 25) : st]
            after = out[m.end() : min(len(out), m.end() + 25)]
            # Preserve corporate role cells in markdown tables (`| Contadora |`) or HTML role tags
            if re.search(r"(?:\|\s*|role[\"']>|\bCargo:\s*)$", before, re.I) and re.match(r"^\s*(?:\||<|\n|$)", after):
                return s
            if s and s[0].isupper():
                return r_str[0].upper() + r_str[1:]
            return r_str

        out = re.sub(pat, _replace_ocup, out, flags=re.I)

    return out
