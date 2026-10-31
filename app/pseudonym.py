"""Deterministic HMAC-based pseudonym generator and persistent entity ledger."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from app.config import PSEUDONYM_SECRET, STATE_DIR
from app.normalize import norm, solo_digitos

# Curated Colombian given names and surnames that have ZERO token overlap with
# any real name or surname in `fuentes/` or `sample/`.
FAKE_GIVEN_F: list[str] = [
    "Sofía", "Marcela", "Doris", "Norma", "Liliana", "Isabela", "Valeria", "Tatiana",
    "Lorena", "Mónica", "Claudia", "Patricia", "Sandra", "Adriana", "Milena", "Diana",
    "Ángela", "Rocío", "Pilar", "Ximena", "Fabiola", "Gladys", "Miriam", "Yolanda",
    "Consuelo", "Esperanza", "Amparo", "Cecilia", "Blanca", "Olga", "Leticia", "Raquel",
    "Rebeca", "Ester", "Sonia", "Nadia", "Tania", "Vanesa", "Melisa", "Yesica",
    "Karina", "Marisol", "Xiomara", "Zulma", "Berta", "Nidia", "Omaira", "Leonor",
    "Eugenia", "Berenice", "Clemencia", "Josefina", "Graciela", "Estela", "Aurora", "Violeta",
    "Iris", "Alba", "Nora", "Elsa", "Irma", "Lidia", "Rita", "unice",
    "Sabrina", "Tamara", "Samanta", "Débora", "Cinthia", "Brenda", "Priscila", "Gisela",
    "aira", "Danna", "Salma", "Miranda", "Regina", "Paulina", "Romina", "Ariadna",
]
FAKE_GIVEN_F = [
    "Sofía", "Marcela", "Doris", "Norma", "Liliana", "Isabela", "Valeria", "Tatiana",
    "Lorena", "Claudia", "Patricia", "Sandra", "Adriana", "Milena", "Diana",
    "Ángela", "Rocío", "Pilar", "Ximena", "Fabiola", "Gladys", "Miriam", "Yolanda",
    "Consuelo", "Esperanza", "Amparo", "Cecilia", "Blanca", "Olga", "Leticia", "Raquel",
    "Rebeca", "Ester", "Sonia", "Nadia", "Tania", "Vanesa", "Melisa", "Karina",
    "Marisol", "Xiomara", "Zulma", "Berta", "Nidia", "Omaira", "Leonor", "Eugenia",
    "Berenice", "Clemencia", "Josefina", "Graciela", "Estela", "Aurora", "Violeta", "Iris",
    "Alba", "Nora", "Elsa", "Irma", "Lidia", "Rita", "Eunice", "Sabrina",
    "Tamara", "Samanta", "Débora", "Cinthia", "Brenda", "Priscila", "Gisela", "Zaira",
    "Danna", "Salma", "Miranda", "Regina", "Paulina", "Romina", "Ariadna", "Estefanía",
]

FAKE_GIVEN_M = [
    "Hugo", "Gustavo", "Mauricio", "Hernán", "Germán", "Iván", "Ómar", "Édgar",
    "Wílmer", "Jairo", "Ramiro", "Álvaro", "Arturo", "Roberto", "Eduardo",
    "Rodrigo", "Marcelo", "Patricio", "Raúl", "Rubén", "Sergio", "Fabián", "Nelson",
    "Jaime", "Javier", "Orlando", "Humberto", "Alfonso", "Alfredo", "Armando",
    "Ernesto", "Efraín", "Gilberto", "Reinaldo", "Leonardo", "Leandro", "Lorenzo", "Matías",
    "Lucas", "Bruno", "Gael", "Thiago", "Dylan", "Braulio",
    "Claudio", "Flavio", "Aurelio", "Celso", "Damián", "Elías", "Ezequiel", "Gaspar",
    "Horacio", "Ismael", "Jacobo", "Lázaro", "Moisés", "Néstor", "Oswaldo", "Pascual",
    "René", "Román", "Tadeo", "Ulises", "Valerio", "Xavier", "Yahir", "Zacarías",
    "Amílcar", "Belisario", "Clemente", "Dionisio", "Eugenio", "Fidel", "Genaro", "Hipólito",
]

FAKE_SURNAMES: list[str] = [
    "Zambrano", "Naranjo", "Pardo", "Lozano", "Pineda", "Cano", "Salgado", "Cárdenas",
    "Rincón", "Mendoza", "Patiño", "Duque", "Fajardo", "Serrano", "Becerra", "Téllez",
    "Castañeda", "Vega", "Acosta", "Cabrera", "Campos", "Carrillo",
    "Cruz", "Delgado", "Escobar", "Espinosa", "Estrada", "Figueroa", "Fuentes",
    "Guerrero", "Guzmán", "Herrera", "Ibáñez", "Jaramillo", "León", "Luna", "Maldonado",
    "Marín", "Medina", "Meza", "Miranda", "Molina", "Monroy", "Morales", "Murillo",
    "Navarro", "Nieto", "Núñez", "Ocampo", "Ochoa", "Olaya", "Ortega", "Ortiz",
    "Oviedo", "Pacheco", "Padilla", "Palacios", "Palomino", "Parra", "Pedraza", "Peralta",
    "Pinzón", "Pizarro", "Poveda", "Prieto", "Puentes", "Pulido", "Quiroga", "Rangel",
    "Reyes", "Ríos", "Rivera", "Roa", "Roldán", "Romero", "Rosales", "Rubio",
    "Saavedra", "Salamanca", "Salcedo", "Sanabria", "Sandoval", "Sarmiento", "Sierra", "Silva",
    "Solano", "Soto", "Suárez", "Tamayo", "Tapia", "Tovar", "Trujillo", "Umaña",
    "Uribe", "Urrea", "Valderrama", "Valencia", "Valero", "Vallejo", "Varela", "Vásquez",
    "Villalba", "Villamizar", "Villanueva", "Villarreal", "Villegas", "Yáñez", "Zúñiga", "Acevedo",
    "Alarcón", "Aldana", "Almanza", "Alvarado", "Álvarez", "Amador", "Amaya", "Ángel",
    "Angarita", "Angulo", "Aponte", "Arbeláez", "Arcila", "Arellano", "Arenas", "Arévalo",
    "Arias", "Ariza", "Arjona", "Ávila", "Avendaño", "Ayala", "Báez", "Ballesteros",
    "Baquero", "Barajas", "Barbosa", "Barragán", "Barrera", "Barreto", "Barrios", "Bastidas",
    "Bautista", "Bayona", "Bedoya", "Bejarano", "Beltrán", "Benavides", "Benítez", "Bermúdez",
    "Blanco", "Bohórquez", "Bonilla", "Borbón", "Bravo", "Briceño", "Buitrago",
    "Burgos", "Bustamante", "Bustos", "Caicedo", "Calderón", "Callejas", "Camacho", "Camargo",
    "Cifuentes", "Chaparro", "Chavarro", "Chinchilla", "Cobos", "Collazos",
    "Contreras", "Corredor", "Cortázar", "Cubillos", "Cuéllar", "Cuervo",
    "Cuevas", "Daza", "Delgadillo", "Domínguez", "Donoso",
    "Duarte", "Durán", "Echeverri", "Echeverría", "Enciso", "Erazo", "Escalante", "Escamilla",
    "Espitia", "Estévez", "Fandiño", "Farfán", "Fonseca",
    "Forero", "Gaona", "Garavito", "Garay", "Garcés", "Garzón", "Gaviria",
    "Godoy", "Góngora", "González", "Granados", "Grijalba", "Guarín",
    "Guevara", "Heredia", "Hincapié", "Holguín",
    "Huertas", "Hurtado", "Ibarra", "Iglesias", "Infante", "Izquierdo", "Jaimes",
    "Latorre", "Laverde", "Ledesma", "Leiva", "Lemus",
]

FEMALE_NAMES_KNOWN: set[str] = {
    "adela", "alejandra", "alicia", "amalia", "ana", "andrea", "antonia", "beatriz",
    "bibiana", "camila", "carmen", "carmenza", "carolina", "catalina", "celeste", "clara",
    "claudia", "consuelo", "daniela", "diana", "dolores", "dora", "elena", "eliana", "elisa",
    "elvira", "emilia", "esperanza", "estefania", "eugenia", "fabiola", "fernanda", "flor",
    "gabriela", "gloria", "graciela", "helena", "ines", "irene", "isabel", "isabella",
    "ivonne", "jessica", "jimena", "johana", "juana", "juanita", "julia", "juliana", "julieth",
    "karen", "karina", "katherine", "kelly", "laura", "leidy", "leticia", "liliana", "lina",
    "lizeth", "lorena", "lucia", "luisa", "luz", "magnolia", "manuela", "marcela", "margarita",
    "maria", "mariana", "marisol", "marta", "martha", "martina", "melissa", "mercedes",
    "milena", "monica", "natalia", "nathalia", "nohora", "norma", "ofelia", "olga", "pamela",
    "paola", "patricia", "paula", "pilar", "raquel", "rebeca", "renata", "rocio", "rosa",
    "rosario", "ruth", "salome", "sandra", "sara", "silvia", "socorro", "sofia", "soledad",
    "sonia", "stellla", "susana", "tatiana", "teresa", "valentina", "valeria", "vanessa",
    "veronica", "victoria", "viviana", "wendy", "ximena", "xiomara", "yenny", "yesenia",
    "yolanda", "yuliana", "zulma",
    *(norm(x) for x in FAKE_GIVEN_F),
}

MALE_NAMES_KNOWN: set[str] = {
    "abel", "adrian", "alberto", "alejandro", "alex", "alexander", "alfonso", "alfredo",
    "alvaro", "andres", "angel", "anibal", "antonio", "armando", "arturo", "augusto",
    "benjamin", "bernardo", "brayan", "bryan", "camilo", "carlos", "cesar", "christian",
    "cristian", "cristobal", "dagoberto", "daniel", "dario", "david", "diego", "edgar",
    "eduardo", "edwin", "efrain", "emilio", "enrique", "ernesto", "esteban", "fabian",
    "fabio", "farid", "federico", "felipe", "fernando", "francisco", "freddy", "gabriel",
    "german", "gilberto", "gonzalo", "guillermo", "gustavo", "harold", "hector", "henry",
    "hernan", "hernando", "horacio", "hugo", "humberto", "ignacio", "ivan", "jaime", "jairo",
    "javier", "jefferson", "jeronimo", "jesus", "jhon", "johan", "john", "jonathan", "jorge",
    "jose", "juan", "julian", "julio", "kevin", "leonardo", "lisandro", "lorenzo", "luis",
    "manuel", "marcelo", "marco", "marcos", "mario", "martin", "mateo", "mauricio", "miguel",
    "milton", "moises", "nelson", "nestor", "nicolas", "norberto", "octavio", "omar",
    "orlando", "oscar", "oswaldo", "pablo", "patricio", "pedro", "rafael", "ramiro", "raul",
    "reinaldo", "ricardo", "roberto", "rodolfo", "rodrigo", "roman", "ruben", "samuel",
    "santiago", "saul", "sebastian", "sergio", "simon", "teodoro", "tomas", "vicente",
    "victor", "william", "wilmer", "wilson", "yamid", "yesid",
    *(norm(x) for x in FAKE_GIVEN_M),
}

KNOWN_GIVEN_NAMES: set[str] = FEMALE_NAMES_KNOWN | MALE_NAMES_KNOWN

KNOWN_SURNAMES: set[str] = {
    "agudelo", "aguirre", "alarcon", "alvarez", "amaya", "arango", "arbelaez", "arcila",
    "arenas", "arias", "aristizabal", "avila", "ayala", "baquero", "barbosa", "barrera",
    "barrios", "bedoya", "beltran", "benitez", "bermudez", "bernal", "betancur", "blanco",
    "bohorquez", "bolivar", "bonilla", "botero", "bravo", "buitrago", "burgos", "bustamante",
    "cabrera", "caicedo", "calderon", "calle", "camacho", "camargo", "campos", "cano",
    "cardenas", "cardona", "carrillo", "carvajal", "castaneda", "castano", "castellanos",
    "castilla", "castillo", "castro", "ceballos", "celis", "chavarriaga", "cifuentes",
    "clavijo", "colorado", "CONTRERAS".lower(), "cordoba", "correa", "cortes", "cruz",
    "cuellar", "cuervo", "daza", "delgado", "diaz", "dominguez", "duarte", "duque", "duran",
    "echeverri", "echeverria", "escobar", "espinosa", "estrada", "fajardo", "fernandez",
    "figueroa", "florez", "fonseca", "forero", "franco", "fuentes", "galan", "galeano",
    "galindo", "gallo", "galvis", "garces", "garcia", "garzon", "gaviria", "gil", "giraldo",
    "gomez", "gonzalez", "granados", "guarin", "guerrero", "guevara", "gutierrez", "guzman",
    "henao", "hernandez", "herrera", "hincapie", "holguin", "hoyos", "huertas", "hurtado",
    "ibanez", "ibarra", "isaza", "jaimes", "jaramillo", "jimenez", "lara", "laverde",
    "ledesma", "leiva", "leon", "loaiza", "londono", "lopez", "lozano", "luna", "machado",
    "maldonado", "mantilla", "marin", "marquez", "martinez", "marulanda", "maya", "medina",
    "mejia", "mendez", "mendoza", "mesa", "meza", "miranda", "molina", "moncada", "monroy",
    "monsalve", "montoya", "mora", "morales", "moreno", "mosquera", "munoz", "murillo",
    "naranjo", "navarro", "nieto", "nino", "norena", "nunez", "ocampo", "ochoa", "olaya",
    "ordonez", "orozco", "ortega", "ortiz", "osorio", "ospina", "ovalle", "oviedo", "pacheco",
    "padilla", "paez", "palacio", "palacios", "parra", "patino", "pedraza", "pena", "peralta",
    "perez", "piedrahita", "pineda", "pinilla", "pinzon", "pizarro", "posada", "poveda",
    "prieto", "puerta", "pulido", "quintero", "quiroga", "ramirez", "ramos", "rangel",
    "restrepo", "rey", "reyes", "rincon", "rios", "rivera", "roa", "robledo", "rocha",
    "rodriguez", "rojas", "roldan", "romero", "rosales", "rubio", "ueda", "rueda", "ruiz",
    "saavedra", "salamanca", "salazar", "salcedo", "saldarriaga", "salgado", "sanabria",
    "sanchez", "sandoval", "santamaria", "santander", "sarmiento", "sepulveda", "serna",
    "serrano", "sierra", "silva", "solano", "soto", "suarez", "tamayo", "tapia", "tellez",
    "tobon", "toro", "torres", "tovar", "trujillo", "umana", "uribe", "urrea", "valderrama",
    "valencia", "vallejo", "vanegas", "varela", "vargas", "vasquez", "vega", "vela",
    "velasco", "velasquez", "velez", "vera", "vergara", "villa", "villalba", "villamizar",
    "villanueva", "villegas", "yepes", "zambrano", "zapata", "zuluaga", "zuniga",
    *(norm(x) for x in FAKE_SURNAMES),
}

NICKNAME_BY_GIVEN: dict[str, list[str]] = {
    "juliana": ["juli"],
    "julian": ["juli"],
    "julieth": ["juli"],
    "carolina": ["caro"],
    "camila": ["cami"],
    "camilo": ["cami"],
    "catalina": ["cata"],
    "daniela": ["dani"],
    "daniel": ["dani"],
    "alejandra": ["aleja", "ale"],
    "alejandro": ["alejo", "ale"],
    "valentina": ["vale", "valen"],
    "gabriela": ["gabi", "gaby"],
    "gabriel": ["gabi", "gaby"],
    "sebastian": ["sebas"],
    "santiago": ["santi"],
    "nicolas": ["nico"],
    "fernanda": ["fer", "nanda"],
    "fernando": ["fer", "nando"],
    "mauricio": ["mauri", "mao"],
    "francisco": ["pacho", "pancho"],
    "ignacio": ["nacho"],
    "guillermo": ["guille", "memo"],
    "eduardo": ["edu", "lalo"],
    "roberto": ["beto"],
    "natalia": ["nata", "nati"],
    "nathalia": ["nata", "nati"],
    "tatiana": ["tati"],
    "marcela": ["marce"],
    "veronica": ["vero"],
    "patricia": ["pati", "paty"],
    "adriana": ["adri"],
    "liliana": ["lili"],
    "viviana": ["vivi"],
    "bibiana": ["bibi"],
    "esteban": ["teban"],
    "cristian": ["cris"],
    "hernando": ["nando"],
    "hernan": ["nando"],
}

MOBILE_PREFIXES: list[str] = [
    "300", "301", "302", "304", "305",
    "310", "311", "312", "313", "314", "315", "316", "317", "318",
    "320", "321", "322", "323", "350",
]

STREET_TYPES: list[str] = ["Calle", "Cl", "Carrera", "Cra", "Diagonal", "Transversal", "Av"]
STREET_SUFFIXES: list[str] = ["", "A", "B", " Bis", " Sur", "A Sur", "B Bis"]
COMPLEMENTS: list[str] = ["", " apto 302", " apto 504", " apto 801", " of 204", " of 405", " casa 14", " casa 28"]
PUBLIC_DOMAINS: list[str] = ["gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"]


def infer_gender(full_or_first_name: str) -> str:
    """Infer 'F' or 'M' from a Colombian person's name."""
    tokens = norm(full_or_first_name).split()
    if not tokens:
        return "F"
    first = tokens[0]
    if first in FEMALE_NAMES_KNOWN:
        return "F"
    if first in MALE_NAMES_KNOWN:
        return "M"
    if first.endswith("a") and first not in ("luca", "joshua", "nikita"):
        return "F"
    if first.endswith(("is", "eth", "ud", "uz")):
        return "F"
    return "M"


def _hmac_int(secret: str, message: str) -> int:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def format_cedula_dots(digits: str) -> str:
    """Format a numeric string with dots every 3 digits from the right (e.g. '1.107.360.039')."""
    d = solo_digitos(digits)
    if not d:
        return digits
    parts: list[str] = []
    while len(d) > 3:
        parts.append(d[-3:])
        d = d[:-3]
    if d:
        parts.append(d)
    return ".".join(reversed(parts))


def _shares_digit_window(cand: str, forbidden_numbers: set[str], window: int = 4) -> bool:
    if not cand:
        return False
    for f in forbidden_numbers:
        if not f:
            continue
        if cand == f or cand in f or f in cand:
            return True
        if len(cand) >= window and len(f) >= window:
            if cand[-4:] == f[-4:] or cand[:4] == f[:4]:
                return True
            for i in range(len(cand) - window + 1):
                sub = cand[i : i + window]
                if sub in f:
                    return True
    return False


class PseudonymLedger:
    """
    Manages deterministic, collision-free pseudonyms persisted in `STATE_DIR/entity_ledger.json`.
    Guarantees that existing entities in the ledger never change their pseudonyms across runs.
    """

    def __init__(self, ledger_path: Path | None = None, secret: str | None = None) -> None:
        self.ledger_path = ledger_path or (STATE_DIR / "entity_ledger.json")
        self.secret = secret or PSEUDONYM_SECRET
        self.entries: dict[str, dict[str, Any]] = {}
        self.used_names: set[str] = set()
        self.used_short_names: set[str] = set()
        self.used_full_names: set[str] = set()
        self.used_surname1s: set[str] = set()
        self.used_emails: set[str] = set()
        self.used_handles: set[str] = set()
        self.used_cedulas: set[str] = set()
        self.used_phones: set[str] = set()
        self.used_addresses: set[str] = set()
        self.used_births: set[str] = set()
        self.used_plates: set[str] = set()
        self.used_passports: set[str] = set()
        self.used_accounts: set[str] = set()
        self.forbidden_digits: set[str] = set()
        self.load()

    def load(self) -> None:
        paths_to_load: list[Path] = []
        pre_pagalo_path = STATE_DIR / "pre_pagalo_ledger.json"
        if self.ledger_path == (STATE_DIR / "entity_ledger.json") and pre_pagalo_path.exists():
            paths_to_load.append(pre_pagalo_path)
        if self.ledger_path.exists():
            paths_to_load.append(self.ledger_path)

        for pth in paths_to_load:
            try:
                data = json.loads(pth.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    loaded = data.get("entries", data)
                    if isinstance(loaded, dict):
                        for cid, entry in loaded.items():
                            if not isinstance(entry, dict):
                                continue
                            # Pre-pagalo ledger entries take precedence if already loaded
                            if cid not in self.entries:
                                self.entries[cid] = entry
                            self._register_used(self.entries[cid])
            except Exception:
                pass

    def save(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": self.entries}
        self.ledger_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _register_used(self, entry: dict[str, Any]) -> None:
        if entry.get("name"):
            self.used_names.add(norm(entry["name"]))
        if entry.get("short_name"):
            self.used_names.add(norm(entry["short_name"]))
            self.used_short_names.add(norm(entry["short_name"]))
        if entry.get("full_name"):
            self.used_names.add(norm(entry["full_name"]))
            self.used_full_names.add(norm(entry["full_name"]))
        if entry.get("surname1"):
            self.used_surname1s.add(norm(entry["surname1"]))
        if entry.get("email"):
            self.used_emails.add(entry["email"].lower())
        if entry.get("handle"):
            self.used_handles.add(entry["handle"].lower())
        if entry.get("cedula"):
            self.used_cedulas.add(solo_digitos(entry["cedula"]))
        if entry.get("phone"):
            self.used_phones.add(solo_digitos(entry["phone"]))
        if entry.get("address"):
            self.used_addresses.add(norm(entry["address"]))
        if entry.get("birth_iso"):
            self.used_births.add(entry["birth_iso"])
        if entry.get("plate"):
            self.used_plates.add(norm(entry["plate"]))
        if entry.get("passport"):
            self.used_passports.add(norm(entry["passport"]))
        if entry.get("account"):
            self.used_accounts.add(solo_digitos(entry["account"]))

    def register_real_corpus_identifiers(
        self,
        cedulas: set[str],
        phones: set[str],
        emails: set[str],
        addresses: set[str],
        births: set[str] | None = None,
    ) -> None:
        """Register all real identifiers from the corpus so no fake value ever matches a real one."""
        for c in cedulas:
            d = solo_digitos(c)
            if d:
                self.used_cedulas.add(d)
                self.forbidden_digits.add(d)
        for p in phones:
            d = solo_digitos(p)
            if d:
                self.used_phones.add(d[-10:] if len(d) >= 10 else d)
                self.forbidden_digits.add(d[-10:] if len(d) >= 10 else d)
        for e in emails:
            if e:
                self.used_emails.add(e.lower())
        for a in addresses:
            if a:
                self.used_addresses.add(norm(a))
        for b in births or set():
            if b:
                self.used_births.add(b.strip())

    def get_or_create(
        self,
        canonical_id: str,
        orig_name: str,
        gender: str | None = None,
        orig_email: str = "",
        orig_phone: str = "",
        orig_cedula: str = "",
        orig_address: str = "",
        orig_birth_iso: str = "",
        orig_plate: str = "",
        orig_passport: str = "",
        orig_account: str = "",
        orig_handle: str = "",
        is_employee: bool = False,
    ) -> dict[str, str]:
        """
        Return existing pseudonym bundle for `canonical_id` or deterministically create a new one.
        If an existing entry is missing a newly discovered attribute (e.g. plate, passport, account),
        only that missing attribute is generated and added.
        """
        entry = self.entries.get(canonical_id, {})
        resolved_gender = gender or entry.get("gender") or infer_gender(orig_name)
        orig_tokens = set(norm(orig_name).split())

        local_forbidden_digits = set()
        if orig_cedula:
            local_forbidden_digits.add(solo_digitos(orig_cedula))
        if orig_phone:
            local_forbidden_digits.add(solo_digitos(orig_phone)[-10:])
        if orig_account:
            local_forbidden_digits.add(solo_digitos(orig_account))

        # 1. Name (given_name, surname1, surname2, short_name, full_name)
        if not entry.get("short_name"):
            pool_given = FAKE_GIVEN_F if resolved_gender == "F" else FAKE_GIVEN_M
            counter = 0
            while True:
                h1 = _hmac_int(self.secret, f"{canonical_id}:given:{counter}")
                h2 = _hmac_int(self.secret, f"{canonical_id}:surname1:{counter}")
                h3 = _hmac_int(self.secret, f"{canonical_id}:surname2:{counter}")
                g = pool_given[h1 % len(pool_given)]
                s1 = FAKE_SURNAMES[h2 % len(FAKE_SURNAMES)]
                s2 = FAKE_SURNAMES[h3 % len(FAKE_SURNAMES)]
                if s1 == s2:
                    s2 = FAKE_SURNAMES[(h3 + 1) % len(FAKE_SURNAMES)]

                cand_tokens = {norm(g), norm(s1), norm(s2)}
                if cand_tokens & orig_tokens:
                    counter += 1
                    continue

                short_name = f"{g} {s1}"
                full_name = f"{g} {s1} {s2}"
                need_unique_s1 = len(self.used_surname1s) < (len(FAKE_SURNAMES) - 5) and counter < 300
                if (
                    norm(short_name) in self.used_short_names
                    or norm(full_name) in self.used_full_names
                    or (need_unique_s1 and norm(s1) in self.used_surname1s)
                ):
                    counter += 1
                    continue

                entry["gender"] = resolved_gender
                entry["given_name"] = g
                entry["surname1"] = s1
                entry["surname2"] = s2
                entry["short_name"] = short_name
                entry["full_name"] = full_name
                entry["name"] = short_name
                entry["initial_surname"] = f"{g[0]}. {s1}"
                entry["filename_token"] = f"{norm(g).replace(' ', '_')}_{norm(s1)}"
                entry["slug_hyphen"] = f"{norm(g).replace(' ', '-')}-{norm(s1)}"
                self.used_names.add(norm(short_name))
                self.used_names.add(norm(full_name))
                self.used_short_names.add(norm(short_name))
                self.used_full_names.add(norm(full_name))
                self.used_surname1s.add(norm(s1))
                break

        g_norm = norm(entry["given_name"]).split()[0]
        s1_norm = norm(entry["surname1"])
        s2_norm = norm(entry["surname2"])

        # 2. Email & Handle
        if not entry.get("email") or not entry.get("handle"):
            orig_dom = orig_email.split("@", 1)[1].lower().strip() if "@" in orig_email else ""
            if orig_dom and orig_dom not in PUBLIC_DOMAINS:
                domain = orig_dom
            elif is_employee:
                domain = "pagalo.co" if canonical_id.upper().startswith(("PG-", "PA-")) else "kipupay.co"
            else:
                h_dom = _hmac_int(self.secret, f"{canonical_id}:domain")
                domain = PUBLIC_DOMAINS[h_dom % len(PUBLIC_DOMAINS)]

            use_dot = is_employee or domain in ("kipupay.co", "pagalo.co", "pagalo.com") or ("." in orig_email.split("@")[0] if "@" in orig_email else False) or ("." in orig_handle)
            counter = 0
            while True:
                if counter == 0:
                    local = f"{g_norm}.{s1_norm}" if use_dot else f"{g_norm[0]}{s1_norm}"
                elif counter == 1:
                    local = f"{g_norm}.{s1_norm}"
                elif counter == 2:
                    local = f"{g_norm[0]}{s1_norm}.{s2_norm}" if use_dot else f"{g_norm}{s1_norm}"
                else:
                    local = f"{g_norm}.{s1_norm}{counter}"
                cand_email = f"{local}@{domain}".lower()
                if cand_email not in self.used_emails and local not in self.used_handles:
                    entry["email"] = cand_email
                    entry["handle"] = local
                    self.used_emails.add(cand_email)
                    self.used_handles.add(local)
                    break
                counter += 1

        # 3. Cedula
        if not entry.get("cedula"):
            orig_c_digits = solo_digitos(orig_cedula)
            target_len = len(orig_c_digits) if len(orig_c_digits) in (7, 8, 9, 10) else 10
            if target_len == 10 and orig_c_digits and orig_c_digits[:2] in ("10", "11"):
                prefix = orig_c_digits[:2]
            elif target_len == 10:
                prefix = "10"
            else:
                first_d = orig_c_digits[0] if orig_c_digits and orig_c_digits[0] in "3456789" else "7"
                prefix = first_d

            rem_len = target_len - len(prefix)
            counter = 0
            while True:
                h_ced = _hmac_int(self.secret, f"{canonical_id}:cedula:{counter}")
                body = str(h_ced % (10**rem_len)).zfill(rem_len)
                cand_ced = prefix + body
                if (
                    cand_ced not in self.used_cedulas
                    and cand_ced not in self.used_phones
                    and not _shares_digit_window(cand_ced, local_forbidden_digits, window=4)
                    and cand_ced not in self.forbidden_digits
                ):
                    entry["cedula"] = cand_ced
                    entry["cedula_dots"] = format_cedula_dots(cand_ced)
                    entry["cedula_last4"] = cand_ced[-4:]
                    self.used_cedulas.add(cand_ced)
                    break
                counter += 1

        # 4. Phone
        if not entry.get("phone"):
            counter = 0
            while True:
                h_ph = _hmac_int(self.secret, f"{canonical_id}:phone:{counter}")
                pref = MOBILE_PREFIXES[h_ph % len(MOBILE_PREFIXES)]
                seven = str((h_ph // len(MOBILE_PREFIXES)) % 10_000_000).zfill(7)
                cand_ph = pref + seven
                if (
                    cand_ph not in self.used_phones
                    and cand_ph not in self.used_cedulas
                    and not _shares_digit_window(cand_ph, local_forbidden_digits, window=4)
                    and cand_ph not in self.forbidden_digits
                ):
                    entry["phone"] = cand_ph
                    entry["phone_spaced"] = f"+57 {cand_ph[:3]} {cand_ph[3:6]} {cand_ph[6:]}"
                    entry["phone_last4"] = cand_ph[-4:]
                    self.used_phones.add(cand_ph)
                    break
                counter += 1

        # 5. Birth ISO
        if not entry.get("birth_iso"):
            orig_y, orig_m, orig_d = 1985, 6, 15
            m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", orig_birth_iso.strip())
            if m_iso:
                orig_y, orig_m, orig_d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
            y_offsets = [-3, -2, -1, 1, 2, 3]
            counter = 0
            while True:
                h_b = _hmac_int(self.secret, f"{canonical_id}:birth" if counter == 0 else f"{canonical_id}:birth:{counter}")
                new_y = orig_y + y_offsets[h_b % len(y_offsets)]
                new_m = ((orig_m + (h_b // 7) % 10) % 12) + 1
                if new_m == orig_m:
                    new_m = (new_m % 12) + 1
                new_d = ((orig_d + (h_b // 97) % 25) % 28) + 1
                if new_d == orig_d:
                    new_d = (new_d % 28) + 1
                cand_birth = f"{new_y:04d}-{new_m:02d}-{new_d:02d}"
                if cand_birth != orig_birth_iso.strip() and cand_birth not in self.used_births:
                    entry["birth_iso"] = cand_birth
                    self.used_births.add(cand_birth)
                    break
                counter += 1

        # 6. Address
        if not entry.get("address"):
            orig_addr_nums = set(re.findall(r"\d+", orig_address))
            counter = 0
            while True:
                h_ad = _hmac_int(self.secret, f"{canonical_id}:address:{counter}")
                st = STREET_TYPES[h_ad % len(STREET_TYPES)]
                suf = STREET_SUFFIXES[(h_ad // 7) % len(STREET_SUFFIXES)]
                comp = COMPLEMENTS[(h_ad // 49) % len(COMPLEMENTS)]
                n1 = ((h_ad // 343) % 150) + 10
                n2 = ((h_ad // 51450) % 95) + 4
                n3 = ((h_ad // 4887750) % 90) + 2
                cand_nums = {str(n1), str(n2), str(n3)}
                if cand_nums & orig_addr_nums:
                    counter += 1
                    continue
                cand_addr = f"{st} {n1}{suf} # {n2}-{n3}{comp}"
                if norm(cand_addr) not in self.used_addresses:
                    entry["address"] = cand_addr
                    self.used_addresses.add(norm(cand_addr))
                    break
                counter += 1

        # 7. Plate (if requested or always available)
        if orig_plate and not entry.get("plate"):
            orig_pl_clean = re.sub(r"[^A-Z0-9]", "", orig_plate.upper())
            counter = 0
            letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
            while True:
                h_pl = _hmac_int(self.secret, f"{canonical_id}:plate:{counter}")
                l1 = letters[h_pl % len(letters)]
                l2 = letters[(h_pl // 23) % len(letters)]
                l3 = letters[(h_pl // 529) % len(letters)]
                d1 = (h_pl // 12167) % 10
                d2 = (h_pl // 121670) % 10
                d3 = (h_pl // 1216700) % 10
                cand_pl = f"{l1}{l2}{l3}{d1}{d2}{d3}"
                if (
                    cand_pl != orig_pl_clean
                    and cand_pl[:3] != orig_pl_clean[:3]
                    and cand_pl[3:] != orig_pl_clean[3:]
                    and norm(cand_pl) not in self.used_plates
                ):
                    if "-" in orig_plate:
                        entry["plate"] = f"{l1}{l2}{l3}-{d1}{d2}{d3}"
                    else:
                        entry["plate"] = cand_pl
                    entry["plate_compact"] = cand_pl
                    entry["plate_hyphen"] = f"{l1}{l2}{l3}-{d1}{d2}{d3}"
                    self.used_plates.add(norm(cand_pl))
                    break
                counter += 1

        # 8. Passport
        if orig_passport and not entry.get("passport"):
            orig_pp = orig_passport.upper().strip()
            counter = 0
            letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
            while True:
                h_pp = _hmac_int(self.secret, f"{canonical_id}:passport:{counter}")
                l1 = letters[h_pp % len(letters)]
                l2 = letters[(h_pp // 23) % len(letters)]
                num = str((h_pp // 529) % 900000 + 100000)
                cand_pp = f"{l1}{l2}{num}"
                if (
                    cand_pp != orig_pp
                    and not _shares_digit_window(num, {solo_digitos(orig_pp)}, window=3)
                    and norm(cand_pp) not in self.used_passports
                ):
                    entry["passport"] = cand_pp
                    self.used_passports.add(norm(cand_pp))
                    break
                counter += 1

        # 9. Bank Account
        if orig_account and not entry.get("account"):
            orig_acc_d = solo_digitos(orig_account)
            is_10_hyphen = len(orig_acc_d) == 10 and "-" in orig_account
            counter = 0
            while True:
                h_ac = _hmac_int(self.secret, f"{canonical_id}:account:{counter}")
                p1 = str((h_ac % 8000) + 1000)
                p2 = str(((h_ac // 8000) % 9000) + 1000)
                if is_10_hyphen:
                    p3 = str(((h_ac // 72000000) % 90) + 10)
                else:
                    p3 = str(((h_ac // 72000000) % 900) + 100)
                cand_d = p1 + p2 + p3
                if (
                    cand_d not in self.used_accounts
                    and not _shares_digit_window(cand_d, {orig_acc_d} | local_forbidden_digits, window=3)
                ):
                    entry["account"] = f"{p1}-{p2}-{p3}" if "-" in orig_account else f"{p1} {p2} {p3}"
                    self.used_accounts.add(cand_d)
                    break
                counter += 1

        self.entries[canonical_id] = entry
        return entry
