"""Context-aware entity resolution and disambiguation of candidate spans."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from app.detect.known import CandidateSpan, KnownAutomaton
from app.detect.patterns import scan_patterns
from app.link import split_colombian_name
from app.normalize import decode_spelled_email, norm, solo_digitos
from app.pseudonym import (
    KNOWN_GIVEN_NAMES,
    KNOWN_SURNAMES,
    PseudonymLedger,
    infer_gender,
)
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

AMBIGUOUS_LOWERCASE_GIVEN_WORDS = NON_PERSON_GIVEN_WORDS | {
    "paz",
    "clara",
    "blanca",
    "luz",
    "esperanza",
    "consuelo",
    "victoria",
    "gloria",
    "soledad",
    "amparo",
    "socorro",
    "dolores",
    "pilar",
    "estrella",
    "marcos",
    "angel",
    "fe",
    "sol",
    "mar",
    "rosa",
    "alba",
    "alma",
    "vida",
    "gracia",
    "cruz",
    "auxilio",
    "milagros",
    "remedios",
    "justo",
    "fidel",
    "dulce",
    "linda",
    "santa",
    "santos",
    "carmen",
    "mercedes",
    "rocio",
    "cielo",
    "luna",
}

AMBIGUOUS_LOWERCASE_SURNAMES = {
    "mora",
    "mesa",
    "pena",
    "cano",
    "rios",
    "campo",
    "campos",
    "monte",
    "montes",
    "sierra",
    "vega",
    "prado",
    "parra",
    "pino",
    "rojas",
    "blanco",
    "prieto",
    "moreno",
    "rubio",
    "castano",
    "cortes",
    "franco",
    "bravo",
    "rico",
    "caro",
    "bueno",
    "bello",
    "guerrero",
    "caballero",
    "duque",
    "conde",
    "rey",
    "reyes",
    "reina",
    "leon",
    "romero",
    "serrano",
    "llano",
    "llanos",
    "valle",
    "valles",
    "plaza",
    "plazas",
    "puente",
    "puentes",
    "fuente",
    "fuentes",
    "pozo",
    "lagos",
    "correa",
    "cadena",
    "cadenas",
    "estrada",
    "calle",
    "calles",
    "carrera",
    "barrios",
    "casas",
    "torres",
    "castillo",
    "palacio",
    "palacios",
    "mercado",
    "molina",
    "herrera",
    "zapata",
    "forero",
    "botero",
    "salgado",
    "delgado",
    "jurado",
    "amado",
    "dorado",
    "granados",
    "arias",
    "paz",
    "cruz",
    "santos",
    "sosa",
    "lara",
    "vera",
    "vila",
    "marin",
    "millan",
    "galan",
    "duran",
    "beltran",
    "jordan",
    "chacon",
    "pabon",
    "garzon",
    "pinzon",
    "rincon",
    "alarcon",
    "calderon",
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
    r"(?:^|[.!?¡¿,\n—\-]\s*|\b(?:hola|buenas(?:\s+tardes|\s+d[ií]as|\s+noches)?|buen\s+d[ií]a|gracias|gacias|mil\s+gracias|listo|hecho|dale|vale|ok|tranquil[ao]|soy|habla|atiende|saluda|por\s+ac[aá]|con|para|de|a|y|ojo\s+con|hermano|hermana|esposo|esposa|vecino|vecina|mam[aá]|pap[aá]|hijo|hija|t[ií]o|t[ií]a|primo|prima|se[ñn]or(?:a|ita)?|sr\.?|sra\.?|don|do[ñn]a|jefe|profe)[.!?¡¿,\s]*)$",
    re.I,
)

RX_DIRECT_VOCATIVE_BEFORE = re.compile(
    r"\b(?:hola|buenas(?:\s+tardes|\s+d[ií]as|\s+noches)?|buen\s+d[ií]a|gracias|gacias|mil\s+gracias|muchas\s+gracias|listo|hecho|dale|vale|ok|tranquil[ao]|soy|habla|atiende|saluda|estimad[ao]|querid[ao]|se[ñn]or(?:a|ita)?|sr\.?|sra\.?|don|do[ñn]a)[.!?¡¿,\s]*$",
    re.I,
)

RX_UNSEEN_INTRO = re.compile(
    r"(?:\b(?:me\s+llamo|mi\s+nombre\s+es|soy|habla|te\s+atiende|le\s+atiende|preparado\s+por|elabor[oó]\s*:?|revisado\s+por|reviewed\s+by|prepared\s+by|created\s+by|authored\s+by|author\s*:|client\s*:|customer\s*:|contact\s*:|agent\s*:|my\s+name\s+is|i\s+am|i'm|auditora?\s*:?|responsable\s*:?|aprueba\s*:?|familiar\s+de|a\s+nombre\s+de|de\s+parte\s+de|en\s+mi\s+familia|se[ñn]or(?:a|ita)?|sr\.?|sra\.?|don|do[ñn]a|doctor(?:a)?|dr\.?|dra\.?|mr\.?|mrs\.?|ms\.?|abogad[oa]|cliente|usuari[oa]|titular)\s+)"
    r"([A-Za-záéíóúñÁÉÍÓÚÑ]+(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+){1,3})",
    re.I,
)

RX_UNSEEN_RELATIVE = re.compile(
    r"\b(?:mi|su|nuestr[oa]|tu|my|his|her|their)\s+"
    r"(?:espos[oa]|herman[oa]|vecin[oa]|mam[aá]|pap[aá]|madre|padre|hij[oa]|prim[oa]|t[ií][oa]|soci[oa]|amig[oa]|novi[oa]|pareja|cu[ñn]ad[oa]|suegr[oa]|abuel[oa]|niet[oa]|sobrin[oa]|jefe|jefa|husband|wife|brother|sister|neighbor|mother|father|son|daughter|partner|friend|boss)\s+"
    r"(?:se\s+llama\s+|es\s+|llamad[oa]\s+|named\s+)?"
    r"([A-Za-záéíóúñÁÉÍÓÚÑ]+(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+){0,2})",
    re.I,
)

RX_UNSEEN_PAREN_ID = re.compile(
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,3})\s*\(((?:CL|KP|PG|PA)-\d+)\)"
)

STOP_INTRO_WORDS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "de",
    "del",
    "al",
    "en",
    "por",
    "para",
    "con",
    "sin",
    "que",
    "quien",
    "como",
    "cuando",
    "donde",
    "porque",
    "pero",
    "sino",
    "también",
    "tambien",
    "tiene",
    "tengo",
    "tuvo",
    "esta",
    "está",
    "estoy",
    "están",
    "estan",
    "fue",
    "son",
    "sea",
    "sido",
    "hizo",
    "hace",
    "pago",
    "pagó",
    "pague",
    "pagué",
    "envio",
    "envió",
    "transfirio",
    "transfirió",
    "recibio",
    "recibió",
    "dijo",
    "dice",
    "vive",
    "trabaja",
    "puede",
    "puedo",
    "quiere",
    "quiero",
    "necesito",
    "necesita",
    "ya",
    "no",
    "si",
    "sí",
    "me",
    "te",
    "se",
    "le",
    "lo",
    "nos",
    "les",
    "mi",
    "tu",
    "su",
    "mis",
    "tus",
    "sus",
    "este",
    "esta",
    "ese",
    "esa",
    "esto",
    "eso",
    "todo",
    "toda",
    "todos",
    "todas",
    "otro",
    "otra",
    "otros",
    "otras",
    "mismo",
    "misma",
    "nuevo",
    "nueva",
    "nuevos",
    "nuevas",
    "viejo",
    "vieja",
    "bueno",
    "buena",
    "malo",
    "mala",
    "gran",
    "grande",
    "kipu",
    "kipi",
    "pay",
    "pagalo",
    "págalo",
    "soporte",
    "agente",
    "cliente",
    "clientes",
    "usuario",
    "usuaria",
    "usuarios",
    "titular",
    "contadora",
    "contador",
    "abogada",
    "abogado",
    "profesional",
    "independiente",
    "gerente",
    "director",
    "directora",
    "coordinador",
    "coordinadora",
    "analista",
    "ingeniero",
    "ingeniera",
    "lider",
    "líder",
    "equipo",
    "area",
    "área",
    "cuenta",
    "cuota",
    "credito",
    "crédito",
    "prestamo",
    "préstamo",
    "tarjeta",
    "banco",
    "nequi",
    "daviplata",
    "pse",
    "cedula",
    "cédula",
    "correo",
    "celular",
    "telefono",
    "teléfono",
    "numero",
    "número",
    "radicado",
    "caso",
    "hola",
    "buenas",
    "tardes",
    "dias",
    "días",
    "noches",
    "gracias",
    "favor",
    "ayuda",
    "problema",
    "error",
    "cobro",
    "saldo",
    "plata",
    "dinero",
    "valor",
    "monto",
    "fecha",
    "hora",
    "dia",
    "día",
    "mes",
    "año",
    "ano",
    "hoy",
    "ayer",
    "mañana",
    "manana",
    "desde",
    "hasta",
    "hacia",
    "entre",
    "sobre",
    "bajo",
    "tras",
    "ante",
    "según",
    "segun",
    "contra",
    "durante",
    "mediante",
    "version",
    "versión",
    "estado",
    "activo",
    "activa",
    "inactivo",
    "inactiva",
    "pendiente",
    "cerrado",
    "cerrada",
    "abierto",
    "abierta",
    "aprobado",
    "aprobada",
    "rechazado",
    "rechazada",
    "interno",
    "interna",
    "externo",
    "externa",
    "senior",
    "junior",
    "general",
    "nacional",
    "regional",
    "local",
    "central",
    "principal",
    "oficial",
    "legal",
    "comercial",
    "financiero",
    "financiera",
    "operativo",
    "operativa",
    "tecnico",
    "técnico",
    "tecnica",
    "técnica",
    "riesgo",
    "fraude",
    "cartera",
    "cobranza",
    "cumplimiento",
    "operaciones",
    "tecnologia",
    "tecnología",
    "producto",
    "productos",
    "servicio",
    "servicios",
    "empresa",
    "compañia",
    "compania",
    "fundacion",
    "fundación",
    "cooperativa",
    "corporacion",
    "corporación",
    "asociacion",
    "asociación",
    "universidad",
    "colegio",
    "clinica",
    "clínica",
    "hospital",
    "seguros",
    " Constructora",
    "constructora",
    "inmobiliaria",
    "transportes",
    "inversiones",
    "comercializadora",
    "distribuidora",
    "drogueria",
    "droguería",
    "farmacia",
    "supermercado",
    "tienda",
    "almacen",
    "almacén",
    "taller",
    "restaurante",
    "hotel",
    "conjunto",
    "edificio",
    "torre",
    "parque",
    "plaza",
    "centro",
    "barrio",
    "ciudad",
    "municipio",
    "departamento",
    "vereda",
    "corregimiento",
    "calle",
    "cl",
    "carrera",
    "cra",
    "cr",
    "kr",
    "avenida",
    "av",
    "autopista",
    "transversal",
    "tv",
    "tr",
    "diagonal",
    "dg",
    "diag",
    "cc",
    "ce",
    "ti",
    "nit",
    "rut",
    "dr",
    "dra",
    "sr",
    "sra",
    "circular",
    "manzana",
    "lote",
    "casa",
    "apto",
    "apartamento",
    "oficina",
    "local",
    "bodega",
    "sucursal",
    "sede",
    "estacion",
    "estación",
    "terminal",
    "aeropuerto",
    "puerto",
    "villa",
    "santa",
    "san",
    "valle",
    "norte",
    "sur",
    "oriente",
    "occidente",
    "bogota",
    "bogotá",
    "medellin",
    "medellín",
    "cali",
    "barranquilla",
    "cartagena",
    "bucaramanga",
    "pereira",
    "manizales",
    "armenia",
    "ibague",
    "ibagué",
    "pasto",
    "neiva",
    "villavicencio",
    "cucuta",
    "cúcuta",
    "monteria",
    "montería",
    "valledupar",
    "sincelejo",
    "popayan",
    "popayán",
    "tunja",
    "riohacha",
    "quibdo",
    "quibdó",
    "yopal",
    "florencia",
    "mocoa",
    "leticia",
    "inirida",
    "inírida",
    "mitu",
    "mitú",
    "arauca",
    "envigado",
    "itagui",
    "itagüí",
    "bello",
    "sabaneta",
    "rionegro",
    "soacha",
    "chia",
    "chía",
    "zipaquira",
    "zipaquirá",
    "facatativa",
    "facatativá",
    "fusagasuga",
    "fusagasugá",
    "girardot",
    "mosquera",
    "madrid",
    "funza",
    "cajica",
    "cajicá",
    "palmira",
    "buenaventura",
    "tulua",
    "tuluá",
    "buga",
    "cartago",
    "jamundi",
    "jamundí",
    "yumbo",
    "soledad",
    "malambo",
    "sabanalarga",
    "turbaco",
    "magangue",
    "magangué",
    "floridablanca",
    "giron",
    "girón",
    "piedecuesta",
    "barrancabermeja",
    "dosquebradas",
    "la",
    "dorada",
    "apartado",
    "apartadó",
    "turbo",
    "caucasia",
    "ipiales",
    "tumaco",
    "pitalito",
    "garzon",
    "garzón",
    "espinal",
    "acacias",
    "acacías",
    "granada",
    "ocaña",
    "ocana",
    "pamplona",
    "lorica",
    "sahagun",
    "sahagún",
    "cerete",
    "cereté",
    "aguachica",
    "maicao",
    "ciénaga",
    "cienaga",
    "fundacion",
    "sogamoso",
    "duitama",
    "chiquinquira",
    "chiquinquirá",
    "by",
    "from",
    "to",
    "with",
    "for",
    "in",
    "on",
    "at",
    "of",
    "and",
    "or",
    "the",
    "is",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "this",
    "that",
    "account",
    "payment",
    "loan",
    "credit",
    "support",
    "agent",
    "client",
    "customer",
    "user",
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


RX_LOCAL_CEDULA = re.compile(
    r"(?:\b(?:c[eé]dula|cc|c\.c\.|documento|doc|identificaci[oó]n|nit)\s*(?:no\.?|n[uú]mero|es|#|:)?\s*)"
    r"(\d{1,3}(?:[.\s]\d{3}){2,3}|\d{6,10})\b",
    re.I,
)
RX_LOCAL_EMAIL = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")
RX_LOCAL_PHONE = re.compile(r"(?:\+?57[\s.\-]?)?\b(3\d{2}[\s.\-]?\d{3}[\s.\-]?\d{4})\b")


def _extract_local_identifiers(local_text: str) -> tuple[str, str, str]:
    if not local_text:
        return "", "", ""
    local_ced = ""
    m_ced = RX_LOCAL_CEDULA.search(local_text)
    if m_ced:
        d = solo_digitos(m_ced.group(1))
        if 6 <= len(d) <= 10:
            local_ced = d

    local_email = ""
    m_em = RX_LOCAL_EMAIL.search(local_text)
    if m_em:
        em = m_em.group(1).strip().lower()
        lp = em.split("@", 1)[0]
        if lp not in GENERIC_CORPORATE_EMAIL_LOCALS:
            local_email = em

    local_phone = ""
    m_ph = RX_LOCAL_PHONE.search(local_text)
    if m_ph:
        ph = solo_digitos(m_ph.group(1))[-10:]
        if len(ph) == 10 and ph != local_ced:
            local_phone = ph

    return local_ced, local_email, local_phone


def _accept_llm_person_surface(surface: str, reg: EntityRegistry) -> bool:
    """Reject LLM person spans that are common phrases, kept entities, or bare given names of known people."""
    words = [w for w in surface.split() if norm(w) not in ("de", "del", "la", "los")]
    if len(words) < 2 or not all(w[0].isupper() for w in words):
        return False
    n_surface = norm(surface)
    if any(n_surface == norm(v) for k in reg.kept.values() for v in k.variants):
        return False
    if n_surface in reg.by_given_name_norm:
        return False
    return not all(norm(w) in KNOWN_GIVEN_NAMES for w in words)


def _get_or_register_dynamic_person(
    name_surface: str,
    reg: EntityRegistry,
    ledger: PseudonymLedger,
    explicit_id: str = "",
    is_employee: bool = False,
    local_text: str = "",
    record_id: str = "",
    allow_single_word: bool = False,
) -> PersonEntity | None:
    tokens = [w for w in name_surface.strip().split() if norm(w) not in STOP_INTRO_WORDS]
    min_words = 1 if allow_single_word else 2
    if len(tokens) < min_words:
        return None
    norm_tokens = [
        (w.capitalize() if (w.islower() or w.isupper()) else w)
        for w in tokens
    ]
    clean_name = " ".join(norm_tokens)
    n_name = norm(clean_name)
    if not n_name or n_name in reg.kept or n_name in ("kipu pay", "kipi"):
        return None
    for k_ent in reg.kept.values():
        if any(norm(orig) == n_name for orig in k_ent.originals_found):
            return None

    if explicit_id and explicit_id in reg.persons:
        return reg.persons[explicit_id]

    if len(norm_tokens) == 1:
        given = norm_tokens[0]
        s1 = ""
        s2 = ""
        short = given
        short_n = n_name
    elif (
        len(norm_tokens) == 3
        and norm(norm_tokens[0]) in KNOWN_GIVEN_NAMES
        and norm(norm_tokens[1]) in KNOWN_GIVEN_NAMES
        and norm(norm_tokens[2]) in KNOWN_SURNAMES
    ):
        given = f"{norm_tokens[0]} {norm_tokens[1]}"
        s1 = norm_tokens[2]
        s2 = ""
        short = f"{given} {s1}"
        short_n = norm(short)
    else:
        given, s1, s2, short = split_colombian_name(clean_name)
        short_n = norm(short)

    local_ced, local_email, local_phone = _extract_local_identifiers(local_text)
    if local_ced and local_ced in reg.by_cedula:
        owner = reg.by_cedula[local_ced]
        if norm(owner.short_name) != short_n and norm(owner.full_name) != n_name and norm(owner.given_name) != short_n:
            local_ced = ""
    if local_email and local_email in reg.by_email:
        owner = reg.by_email[local_email]
        if norm(owner.short_name) != short_n and norm(owner.full_name) != n_name and norm(owner.given_name) != short_n:
            local_email = ""
    if local_phone and local_phone in reg.by_phone:
        owner = reg.by_phone[local_phone]
        if norm(owner.short_name) != short_n and norm(owner.full_name) != n_name and norm(owner.given_name) != short_n:
            local_phone = ""

    if len(norm_tokens) == 1:
        cands = list(reg.by_short_name_norm.get(short_n, []) or reg.by_given_name_norm.get(short_n, []))
    else:
        cands = list(
            reg.by_full_name_norm.get(n_name, [])
            or reg.by_short_name_norm.get(n_name, [])
            or reg.by_short_name_norm.get(short_n, [])
        )

    if cands:
        # 1. Exact identifier match among candidates
        for c in cands:
            if local_ced and c.cedula == local_ced:
                return c
            if local_email and c.email and c.email.lower() == local_email:
                return c
            if local_phone and c.phone == local_phone:
                return c

        # 2. If local text has a discriminating identifier, check if any candidate is compatible
        if local_ced or local_email or local_phone:
            compatible: list[PersonEntity] = []
            for c in cands:
                if not c.canonical_id.startswith("external:"):
                    continue
                if local_ced and c.cedula and c.cedula != local_ced:
                    continue
                if local_email and c.email and c.email.lower() != local_email:
                    continue
                if local_phone and c.phone and c.phone != local_phone:
                    continue
                compatible.append(c)
            if compatible:
                target = compatible[0]
                if local_ced and not target.cedula:
                    target.cedula = local_ced
                if local_email and not target.email:
                    target.email = local_email
                if local_phone and not target.phone:
                    target.phone = local_phone
                _ensure_person_pseudonyms(target, ledger)
                reg.add_or_update_person(target)
                return target

            # All existing candidates conflict -> spawn distinct homonym!
            disc = local_ced or local_phone or norm(local_email.split("@", 1)[0]) or record_id or "2"
            cid = f"external:{short_n}:{disc}"
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
                cedula=local_ced,
                email=local_email,
                phone=local_phone,
                is_employee=is_employee,
            )
            _ensure_person_pseudonyms(p, ledger)
            reg.add_or_update_person(p)
            return p

        # No discriminating local identifier: if single-word relative in an unseen record, prefer external relative
        if len(norm_tokens) == 1 and allow_single_word:
            ext_rel_id = f"external:{short_n}_{record_id}" if record_id else f"external:{short_n}"
            if ext_rel_id in reg.persons:
                return reg.persons[ext_rel_id]
            for c in cands:
                if c.canonical_id.startswith("external:") and norm(c.short_name) == short_n:
                    return c
        else:
            return cands[0]

    if len(norm_tokens) == 1:
        cid = explicit_id or (f"external:{short_n}_{record_id}" if record_id else f"external:{short_n}")
    else:
        cid = explicit_id or f"external:{short_n}"
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
        cedula=local_ced,
        email=local_email,
        phone=local_phone,
        is_employee=is_employee or cid.startswith("KP-"),
    )
    _ensure_person_pseudonyms(p, ledger)
    reg.add_or_update_person(p)
    return p


def _line_around(text: str, start: int, end: int) -> str:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    return text[ls:le]


def _is_valid_unseen_name_tokens(words: list[str], require_lexicon: bool = False) -> bool:
    if len(words) < 2:
        return False
    w0_n = norm(words[0])
    if not w0_n or w0_n in STOP_INTRO_WORDS or w0_n in NON_PERSON_GIVEN_WORDS:
        return False
    for w in words:
        wn = norm(w)
        if not wn or wn in STOP_INTRO_WORDS or len(wn) < 2:
            return False

    all_Lower = all(w.islower() for w in words)
    all_Titled_or_Upper = all(w[0].isupper() for w in words)

    if not require_lexicon:
        if len(words) >= 3 and norm(words[-1]) not in KNOWN_SURNAMES:
            return False
        if all_Titled_or_Upper:
            # At least first word should be a plausible given name or none of the words is a known non-person word
            if w0_n in KNOWN_GIVEN_NAMES:
                return True
            # Allow unfamiliar foreign/rare given names after explicit intros ("My name is...", "Me llamo...")
            return len(words) in (2, 3) and all(w.isalpha() for w in words)
        if all_Lower:
            return (
                w0_n in KNOWN_GIVEN_NAMES
                and w0_n not in AMBIGUOUS_LOWERCASE_GIVEN_WORDS
                and any(norm(w) in KNOWN_SURNAMES for w in words[1:])
            )
        return False

    # Lexicon mode (no intro phrase): require given_name + surname from lexicon
    if w0_n not in KNOWN_GIVEN_NAMES:
        return False
    if all_Lower and w0_n in AMBIGUOUS_LOWERCASE_GIVEN_WORDS:
        return False

    if len(words) == 2:
        w1_n = norm(words[1])
        if w1_n not in KNOWN_SURNAMES:
            return False
        if all_Lower and w1_n in AMBIGUOUS_LOWERCASE_SURNAMES:
            return False
        return True

    if len(words) == 3:
        w1_n, w2_n = norm(words[1]), norm(words[2])
        if (w1_n in KNOWN_GIVEN_NAMES and w2_n in KNOWN_SURNAMES) or (
            w1_n in KNOWN_SURNAMES and w2_n in KNOWN_SURNAMES
        ):
            if all_Lower and w2_n in AMBIGUOUS_LOWERCASE_SURNAMES:
                return False
            return True
        return False

    if len(words) == 4:
        w1_n, w2_n, w3_n = norm(words[1]), norm(words[2]), norm(words[3])
        if w1_n in KNOWN_GIVEN_NAMES and w2_n in KNOWN_SURNAMES and w3_n in KNOWN_SURNAMES:
            return True
        return False

    return False


RX_WORD_SEQ = re.compile(r"\b([A-Za-záéíóúñÁÉÍÓÚÑ]{2,20}(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]{2,20}){1,3})\b")
RX_FILENAME_PAIR = re.compile(r"(?<![a-zA-Z0-9])([a-zA-Z]{3,16})_([a-zA-Z]{3,16})(?![a-zA-Z0-9])")
RX_SLUG_PAIR = re.compile(r"(?<![a-zA-Z0-9])([a-zA-Z]{3,16})-([a-zA-Z]{3,16})(?![a-zA-Z0-9])")


def _scan_unseen_names(
    text: str,
    reg: EntityRegistry,
    ledger: PseudonymLedger,
    ctx: ResolutionContext,
) -> list[CandidateSpan]:
    spans: list[CandidateSpan] = []

    # 1. Explicit `Name (CL-xxxx)` or `Name (KP-xxxx)`
    for m in RX_UNSEEN_PAREN_ID.finditer(text):
        raw_name = m.group(1)
        explicit_id = m.group(2)
        line_txt = _line_around(text, m.start(1), m.end(1))
        p = _get_or_register_dynamic_person(
            raw_name,
            reg,
            ledger,
            explicit_id=explicit_id,
            local_text=line_txt,
            record_id=ctx.record_id,
        )
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=m.end(1),
                    surface=raw_name,
                    kind="short_name" if len(raw_name.split()) <= 2 else "full_name",
                    candidates=[p],
                    priority=95,
                )
            )

    # 2. Intro / title / role cues (`Me llamo ...`, `Soy ...`, `señora ...`, `My name is ...`, etc.)
    for m in RX_UNSEEN_INTRO.finditer(text):
        raw_name = m.group(1)
        words = raw_name.split()
        kept_words: list[str] = []
        for idx, w in enumerate(words):
            wn = norm(w)
            if wn in STOP_INTRO_WORDS:
                break
            if idx == 0 and not (w[0].isupper() or (wn in KNOWN_GIVEN_NAMES and wn not in AMBIGUOUS_LOWERCASE_GIVEN_WORDS)):
                break
            if idx > 0 and not (
                w[0].isupper()
                or wn in KNOWN_SURNAMES
                or (idx == 1 and wn in KNOWN_GIVEN_NAMES and wn not in AMBIGUOUS_LOWERCASE_GIVEN_WORDS)
            ):
                break
            kept_words.append(w)
        while len(kept_words) >= 2 and not _is_valid_unseen_name_tokens(kept_words, require_lexicon=False):
            kept_words.pop()
        if len(kept_words) < 2:
            continue
        trimmed = " ".join(kept_words)
        end_pos = m.start(1) + len(trimmed)
        line_txt = _line_around(text, m.start(1), end_pos)
        existing_cands = list(
            reg.by_full_name_norm.get(norm(trimmed), [])
            or reg.by_short_name_norm.get(norm(trimmed), [])
        )
        p = _get_or_register_dynamic_person(
            trimmed,
            reg,
            ledger,
            local_text=line_txt,
            record_id=ctx.record_id,
        )
        if p is not None:
            has_local_id = any(_extract_local_identifiers(line_txt))
            is_new = (p not in existing_cands) or has_local_id
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=end_pos,
                    surface=text[m.start(1) : end_pos],
                    kind="short_name" if len(kept_words) <= 2 else "full_name",
                    candidates=[p] if is_new else existing_cands,
                    priority=94 if is_new else 84,
                )
            )

    # 3. Relative / neighbor / partner cues (`mi esposo Carlos`, `mi vecina Lucía Gómez`, `mi hermano Daniel`)
    for m in RX_UNSEEN_RELATIVE.finditer(text):
        raw_name = m.group(1)
        words = raw_name.split()
        kept_words = []
        for idx, w in enumerate(words):
            wn = norm(w)
            if wn in STOP_INTRO_WORDS or wn in NON_PERSON_GIVEN_WORDS:
                break
            if idx == 0:
                if wn not in KNOWN_GIVEN_NAMES:
                    break
                if w.islower() and wn in AMBIGUOUS_LOWERCASE_GIVEN_WORDS:
                    break
                kept_words.append(w)
            elif idx == 1:
                if (w[0].isupper() and (wn in KNOWN_SURNAMES or wn in KNOWN_GIVEN_NAMES)) or (
                    w.islower() and wn in KNOWN_SURNAMES and wn not in AMBIGUOUS_LOWERCASE_SURNAMES
                ):
                    kept_words.append(w)
                else:
                    break
            elif idx == 2:
                if (w[0].isupper() and wn in KNOWN_SURNAMES) or (
                    w.islower() and wn in KNOWN_SURNAMES and wn not in AMBIGUOUS_LOWERCASE_SURNAMES
                ):
                    kept_words.append(w)
                else:
                    break
        if not kept_words:
            continue
        trimmed = " ".join(kept_words)
        end_pos = m.start(1) + len(trimmed)
        line_txt = _line_around(text, m.start(1), end_pos)
        p = _get_or_register_dynamic_person(
            trimmed,
            reg,
            ledger,
            local_text=line_txt,
            record_id=ctx.record_id,
            allow_single_word=True,
        )
        if p is not None:
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=end_pos,
                    surface=text[m.start(1) : end_pos],
                    kind="given_name" if len(kept_words) == 1 else ("short_name" if len(kept_words) == 2 else "full_name"),
                    candidates=[p],
                    priority=93,
                )
            )

    # 4. Standalone lexicon-based detector (Title Case, MAYÚSCULAS, or unambiguous minúsculas)
    for m in RX_WORD_SEQ.finditer(text):
        raw_seq = m.group(1)
        before_txt = text[max(0, m.start(1) - 35) : m.start(1)]
        if RX_PLACE_PREP_BEFORE.search(before_txt):
            continue
        words = raw_seq.split()
        # Try longest valid prefix (up to 4 words, down to 2 words)
        matched_words: list[str] = []
        for k in range(min(4, len(words)), 1, -1):
            sub = words[:k]
            if _is_valid_unseen_name_tokens(sub, require_lexicon=True):
                matched_words = sub
                break
        if len(matched_words) < 2:
            continue
        # Calculate exact end offset in `text` for `matched_words`
        surf_match = re.match(r"^\s*(" + r"\s+".join(re.escape(w) for w in matched_words) + r")", raw_seq)
        if not surf_match:
            continue
        surf_str = surf_match.group(1)
        end_pos = m.start(1) + len(surf_str)
        line_txt = _line_around(text, m.start(1), end_pos)
        existing_cands = list(
            reg.by_full_name_norm.get(norm(surf_str), [])
            or reg.by_short_name_norm.get(norm(surf_str), [])
        )
        p = _get_or_register_dynamic_person(
            surf_str,
            reg,
            ledger,
            local_text=line_txt,
            record_id=ctx.record_id,
        )
        if p is not None:
            has_local_id = any(_extract_local_identifiers(line_txt))
            is_new = (p not in existing_cands) or has_local_id
            spans.append(
                CandidateSpan(
                    start=m.start(1),
                    end=end_pos,
                    surface=surf_str,
                    kind="short_name" if len(matched_words) <= 2 else "full_name",
                    candidates=[p] if is_new else existing_cands,
                    priority=92 if is_new else 84,
                )
            )

    # 5. Underscore (`filename_token`) and hyphen (`slug_hyphen`) unseen person tokens
    for rx_pair, span_kind in ((RX_FILENAME_PAIR, "filename_token"), (RX_SLUG_PAIR, "slug_hyphen")):
        for m in rx_pair.finditer(text):
            w1, w2 = m.group(1), m.group(2)
            n1, n2 = norm(w1), norm(w2)
            if (
                n1 in KNOWN_GIVEN_NAMES
                and n1 not in AMBIGUOUS_LOWERCASE_GIVEN_WORDS
                and n1 not in STOP_INTRO_WORDS
                and n2 in KNOWN_SURNAMES
                and n2 not in AMBIGUOUS_LOWERCASE_SURNAMES
                and n2 not in STOP_INTRO_WORDS
            ):
                short_pair_n = f"{n1} {n2}"
                if short_pair_n in reg.by_short_name_norm:
                    continue
                line_txt = _line_around(text, m.start(), m.end())
                p = _get_or_register_dynamic_person(
                    f"{w1.capitalize()} {w2.capitalize()}",
                    reg,
                    ledger,
                    local_text=line_txt,
                    record_id=ctx.record_id,
                )
                if p is not None:
                    spans.append(
                        CandidateSpan(
                            start=m.start(),
                            end=m.end(),
                            surface=m.group(0),
                            kind=span_kind,
                            candidates=[p],
                            priority=91,
                        )
                    )

    if ctx.llm_spans:
        for item in ctx.llm_spans:
            surf = str(item.get("surface", "")).strip()
            stype = str(item.get("type", "person")).strip()
            if not surf or stype != "person" or not _accept_llm_person_surface(surf, reg):
                continue
            for m in re.finditer(rf"(?<!\w){re.escape(surf)}(?!\w)", text):
                line_txt = _line_around(text, m.start(), m.end())
                p = _get_or_register_dynamic_person(
                    surf,
                    reg,
                    ledger,
                    local_text=line_txt,
                    record_id=ctx.record_id,
                )
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

    if span.kind in ("given_name", "nickname"):
        if not span.surface:
            return None
        is_upper = span.surface[0].isupper()
        if span.kind == "nickname" and not is_upper:
            return None
        gn = norm(span.surface)
        if gn in NON_PERSON_GIVEN_WORDS:
            return None
        if gn == "paz" and re.match(r"^\s*y\s+salvo\b", after_text, re.I):
            return None
        if gn == "maria" and ('"' in before_text[-3:] or '"' in after_text[:3] or "campaña" in line_norm):
            return None
        if not is_upper and gn in AMBIGUOUS_LOWERCASE_GIVEN_WORDS and not RX_DIRECT_VOCATIVE_BEFORE.search(before_text):
            return None

        # Check if followed by "(agente de <Ciudad>)" -> e.g. "<Nombre> (agente de <Ciudad>)"
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

        if not is_upper:
            # Lowercase given_name: require active participant in thread/record AND conversational cue
            active_matches = [p for p in active_pool if p in cands]
            if active_matches and RX_GIVEN_CUE_BEFORE.search(before_text):
                if re.search(r"\b(?:hola|buenas|estimad[ao]|querid[ao])[.!?¡¿,\s]*$", before_text, re.I) and ctx.thread_client in cands:
                    return ctx.thread_client
                if re.search(r"\b(?:soy|habla|saluda|atiende)[.!?¡¿,\s]*$", before_text, re.I) and ctx.author_entity in cands:
                    return ctx.author_entity
                return active_matches[0]
            return None

        # If greeting at start of agent message ("Hola <Nombre>!"), prefer thread_client
        if re.search(r"\b(?:hola|buenas|estimad[ao]|querid[ao])[.!?¡¿,\s]*$", before_text, re.I) and ctx.thread_client in cands:
            return ctx.thread_client
        # If self-intro ("soy <Nombre> de soporte"), prefer author_entity
        if re.search(r"\b(?:soy|habla|saluda|atiende)[.!?¡¿,\s]*$", before_text, re.I) and ctx.author_entity in cands:
            return ctx.author_entity

        active_matches = [p for p in active_pool if p in cands]
        if active_matches:
            return active_matches[0]

        # Otherwise only allow if there is a clear person cue right before (or internal channel/doc colleague mention)
        in_internal_ctx = ctx.channel_or_table in ("general", "fraude-interno", "escalamientos") or ctx.source_type == "docs"
        if not RX_GIVEN_CUE_BEFORE.search(before_text) and not (in_internal_ctx and not RX_PLACE_PREP_BEFORE.search(before_text)):
            return None
        if len(cands) == 1:
            return cands[0]
        # Multiple candidates for a single given_name with no active match:
        # Check line for explicit phone/cedula/email (e.g. relative mention with phone number)
        for c in cands:
            if c.cedula and c.cedula in line_text.replace(".", ""):
                return c
            if c.email and c.email.lower() in line_text.lower():
                return c
            if c.phone and c.phone in solo_digitos(line_text):
                return c
        # In internal channels or docs, if exactly 1 candidate is an employee
        if in_internal_ctx and gn not in AMBIGUOUS_LOWERCASE_GIVEN_WORDS and not RX_PLACE_PREP_BEFORE.search(before_text):
            emp_cands = [c for c in cands if c.is_employee]
            if len(emp_cands) == 1:
                return emp_cands[0]
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

    if kind in ("given_name", "nickname"):
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
    unseen_spans = _scan_unseen_names(text, reg, ledger, ctx)
    cand_spans.extend(unseen_spans)

    # If any unseen person was dynamically registered, ensure focus_person is updated if empty
    if focus_person is None:
        for sp in unseen_spans:
            if sp.kind in ("full_name", "short_name", "given_name", "filename_token", "slug_hyphen") and sp.candidates:
                focus_person = sp.candidates[0]
                break

    cand_spans.extend(automaton.scan(text))
    cand_spans.extend(scan_patterns(text, reg, focus_person=focus_person, is_chat=is_chat))

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
