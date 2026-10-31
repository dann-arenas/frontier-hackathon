"""Automated test suite covering public tests, sample tests, canary obfuscation, stability, category, structure, and HTTP API."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import OUTPUT_DIR, ROOT_DIR, STATE_DIR
from app.guard import run_final_guard
from app.main import app
from app.normalize import contiene, norm, solo_digitos, texto_de_registro
from app.pipeline import run_full_pipeline
from app.pseudonym import PseudonymLedger, infer_gender
from app.registry import EntityRegistry, PersonEntity, split_colombian_name
from app.sources import CorpusBundle, load_from_local_dir

FORBIDDEN_PACKAGES = (
    "presidio",
    "presidio-analyzer",
    "presidio-anonymizer",
    "scrubadub",
    "comprehend",
    "privateai",
    "private-ai",
    "gliner",
    "Amazon-Comprehend",
)


def _eval_test_list(tests: list[dict[str, Any]], out_dir: Path) -> list[str]:
    records: dict[str, dict[str, Any]] = {}
    for fn in ("chats.jsonl", "docs.jsonl", "tables.jsonl"):
        for line in (out_dir / fn).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                records[r["id"]] = r
    entero = "\n".join(texto_de_registro(r) for r in records.values())
    entities = json.loads((out_dir / "entities.json").read_text(encoding="utf-8"))["entities"]

    failures: list[str] = []
    for t in tests:
        tid = t["id"]
        fam = t["family"]
        loc = t.get("locator") or {}
        rec_id = loc.get("record")
        field = loc.get("field")
        payload = t.get("payload") or []

        if fam in ("directa", "oculta"):
            rec = records[rec_id]
            txt = texto_de_registro(rec, field)
            for p in payload:
                if contiene(txt, p) or (fam == "directa" and contiene(entero, p)):
                    failures.append(f"{tid}: payload {p!r} leaked in {rec_id}")
        elif fam == "indirecta":
            rec = records[rec_id]
            txt = texto_de_registro(rec, field)
            if all(contiene(txt, p) for p in payload):
                failures.append(f"{tid}: quasi-identifiers {payload!r} co-occur in {rec_id}")
        elif fam == "falso_positivo":
            rec = records[rec_id]
            txt = texto_de_registro(rec, field)
            for p in payload:
                if not contiene(txt, p, exacto=True) and not contiene(txt, p):
                    failures.append(f"{tid}: preserved entity {p!r} missing in {rec_id}")
        elif fam == "homonimos":
            orig = payload[0]
            short_orig = " ".join(orig.split()[:2])
            matching = [
                e
                for e in entities
                if e.get("type") == "person"
                and any(norm(o) in (norm(orig), norm(short_orig)) for o in e.get("originals", []))
            ]
            if len(matching) < 2:
                failures.append(f"{tid}: expected >=2 homonym entities for {orig!r}, got {len(matching)}")
            elif len({e["pseudonyms"]["name"] for e in matching}) < len(matching):
                failures.append(f"{tid}: homonyms share pseudonym")
        elif fam == "consistencia":
            forms = set(t.get("lookup_forms") or [])
            matching = [
                e
                for e in entities
                if e.get("type") == "person" and (set(e.get("originals", [])) & forms)
            ]
            if len(matching) != 1:
                failures.append(f"{tid}: expected 1 entity for {forms}, got {len(matching)}")
            else:
                ent = matching[0]
                ps_name = ent["pseudonyms"]["name"]
                ps_tokens = list(ent["pseudonyms"].values()) + [
                    ps_name.split()[0],
                    ps_name.split()[-1],
                    norm(ps_name).replace(" ", "_"),
                    norm(ps_name).replace(" ", "-"),
                ]
                req_recs = t.get("records") or ent.get("records") or []
                for rid in req_recs:
                    if rid not in ent.get("records", []):
                        failures.append(f"{tid}: record {rid} missing from entity {ent['entity_id']}")
                        break
                    r_txt = texto_de_registro(records[rid])
                    if not any(contiene(r_txt, v) for v in ps_tokens if v):
                        failures.append(f"{tid}: no pseudonym of {ent['entity_id']} in {rid}")
                        break
    return failures


def test_no_forbidden_packages() -> None:
    req_txt = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    for pkg in FORBIDDEN_PACKAGES:
        assert pkg.lower() not in req_txt, f"Forbidden package {pkg} found in requirements.txt"


def test_validator_sample_expected() -> None:
    proc = subprocess.run(
        ["node", str(ROOT_DIR / "validar.mjs"), "sample/expected_output", "--public", "sample"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK: la salida pasa el validador" in proc.stdout


def test_sample_pipeline_and_13_cases(tmp_path: Path) -> None:
    sample_bundle = load_from_local_dir(ROOT_DIR / "sample" / "input")
    out_dir = tmp_path / "sample_out"
    ledger_path = tmp_path / "sample_ledger.json"
    snap_path = tmp_path / "sample_snap.json"

    res = run_full_pipeline(
        sample_bundle,
        output_dir=out_dir,
        ledger_path=ledger_path,
        snapshot_path=snap_path,
    )
    assert res["records"] == {"chats": 10, "docs": 1, "tables": 4}
    assert res["guard_intercepts"] == 0

    proc = subprocess.run(
        ["node", str(ROOT_DIR / "validar.mjs"), str(out_dir), "--public", "sample"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK: la salida pasa el validador" in proc.stdout

    tests = json.loads((ROOT_DIR / "sample" / "tests_sample.json").read_text(encoding="utf-8"))
    failures = _eval_test_list(tests, out_dir)
    assert failures == []


def test_fuentes_output_and_5_public_cases() -> None:
    if not (OUTPUT_DIR / "entities.json").exists():
        b = load_from_local_dir(ROOT_DIR / "fuentes")
        run_full_pipeline(b)

    proc = subprocess.run(
        ["node", str(ROOT_DIR / "validar.mjs"), str(OUTPUT_DIR), "--public", "fuentes"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "OK: la salida pasa el validador" in proc.stdout

    tests = json.loads((ROOT_DIR / "tests_public.json").read_text(encoding="utf-8"))
    failures = _eval_test_list(tests, OUTPUT_DIR)
    assert failures == []


def test_canary_obfuscation(tmp_path: Path) -> None:
    """Test T5: synthetic PII with confusables, invisible chars, separators, and concatenated words."""
    ledger = PseudonymLedger(ledger_path=tmp_path / "canary_ledger.json")
    reg = EntityRegistry(ledger=ledger)
    g, s1, s2, short = split_colombian_name("Valentina Restrepo")
    p = PersonEntity(
        canonical_id="CL-99999",
        entity_id="CL-99999",
        full_name="Valentina Restrepo",
        short_name=short,
        given_name=g,
        surname1=s1,
        surname2=s2,
        gender="F",
        cedula="1098765432",
        phone="3159876543",
        email="vrestrepo@gmail.com",
    )
    reg.add_or_update_person(p)
    reg.assign_all_pseudonyms()

    # Cyrillic 'а' (\u0430), 'е' (\u0435), zero-width space (\u200b), rare space (\u00a0), separated digits
    obfuscated_text = (
        "Soy V\u0430l\u0435ntin\u0430\u00a0R\u0435str\u0435p\u200bo, "
        "cédula 1.098-765_432, cel +57 315.987-6543, "
        "concatenado valentinarestrepo."
    )
    records = [
        {
            "id": "canary-01",
            "thread_id": "soporte:1.0",
            "author": "Cliente",
            "ts": "1.0",
            "text": obfuscated_text,
            "attachments": [],
        }
    ]
    run_final_guard(records, reg)
    scrubbed = texto_de_registro(records[0])
    assert not contiene(scrubbed, "Valentina Restrepo")
    assert not contiene(scrubbed, "1098765432")
    assert not contiene(scrubbed, "3159876543")


def test_pseudonym_category(tmp_path: Path) -> None:
    """Test T7: same gender, valid formats, and zero shared name tokens or digit sequences."""
    ledger = PseudonymLedger(ledger_path=tmp_path / "cat_ledger.json")
    samples = [
        ("CL-001", "María Fernanda Gómez López", "F", "1020304050", "3102223344", "mfgomez@gmail.com"),
        ("CL-002", "Juan Carlos Rodríguez Pérez", "M", "79888999", "3001112233", "jcrodriguez@hotmail.com"),
        ("KP-001", "Laura Sofía Martínez Díaz", "F", "52333444", "3154445566", "laura.martinez@kipupay.co"),
    ]
    seen_names: set[str] = set()
    for cid, name, gender, ced, ph, em in samples:
        ps = ledger.get_or_create(
            canonical_id=cid,
            orig_name=name,
            gender=gender,
            orig_cedula=ced,
            orig_phone=ph,
            orig_email=em,
            orig_address="Calle 45 # 12-34 apto 201",
            orig_birth_iso="1990-05-15",
            orig_plate="ABC123",
            orig_passport="AP123456",
            orig_account="4129-8834-00",
            is_employee=cid.startswith("KP-"),
        )
        assert ps["gender"] == gender
        assert infer_gender(ps["given_name"]) == gender
        orig_tokens = {norm(t) for t in name.split()}
        fake_tokens = {norm(t) for t in ps["full_name"].split()}
        assert orig_tokens.isdisjoint(fake_tokens)
        assert ps["short_name"] not in seen_names
        seen_names.add(ps["short_name"])
        assert len(ps["cedula"]) == len(ced) and ps["cedula"].isdigit()
        assert ced not in ps["cedula"] and ps["cedula"][-4:] != ced[-4:]
        assert len(ps["phone"]) == 10 and ps["phone"].startswith("3")
        assert ph not in ps["phone"] and ps["phone"][-4:] != ph[-4:]
        if cid.startswith("KP-"):
            assert ps["email"].endswith("@kipupay.co")


def test_stability_and_growth(tmp_path: Path) -> None:
    """Test T6: re-running and simulating source growth preserves existing pseudonyms 100% intact."""
    b1 = load_from_local_dir(ROOT_DIR / "sample" / "input")
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    ledger_p = tmp_path / "ledger.json"
    snap_p = tmp_path / "snap.json"

    run_full_pipeline(b1, output_dir=out1, ledger_path=ledger_p, snapshot_path=snap_p)
    ents1 = {
        e["entity_id"]: e["pseudonyms"]
        for e in json.loads((out1 / "entities.json").read_text(encoding="utf-8"))["entities"]
        if e.get("type") == "person"
    }

    # Simulate growth: add a new client with a homonym name + new chat message
    b2 = copy.deepcopy(b1)
    new_client_id = "clientes:CL-99887"
    b2.tables.append(
        {
            "id": new_client_id,
            "table": "clientes",
            "columns": {
                "cliente_id": "CL-99887",
                "nombre_completo": "Manuela Castaño Gómez",
                "tipo_doc": "CC",
                "num_doc": "1044556677",
                "correo": "manuela.gomez@yahoo.com",
                "celular": "3187654321",
                "ciudad": "Medellín",
                "barrio": "El Poblado",
                "direccion": "Carrera 43A # 10-20",
                "fecha_nacimiento": "1992-11-03",
                "fecha_vinculacion": "2024-01-15",
                "ocupacion": "arquitecta",
                "notas": "Cliente nueva.",
            },
        }
    )
    b2.record_index["tables"].append(new_client_id)

    run_full_pipeline(b2, output_dir=out2, ledger_path=ledger_p, snapshot_path=snap_p)
    ents2 = {
        e["entity_id"]: e["pseudonyms"]
        for e in json.loads((out2 / "entities.json").read_text(encoding="utf-8"))["entities"]
        if e.get("type") == "person"
    }

    for eid, ps1 in ents1.items():
        assert eid in ents2
        assert ents2[eid] == ps1, f"Pseudonym changed for existing entity {eid}"
    assert "CL-99887" in ents2
    assert ents2["CL-99887"]["name"] != ents1["CL-88627"]["name"]


def test_structure_integrity() -> None:
    """Verify relational keys (CL-*, TX-*, PQR-*, KP-*) and chat thread graphs are 100% preserved."""
    orig_bundle = load_from_local_dir(ROOT_DIR / "fuentes")
    out_tables = [
        json.loads(line)
        for line in (OUTPUT_DIR / "tables.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(out_tables) == len(orig_bundle.tables)
    orig_by_id = {r["id"]: r for r in orig_bundle.tables}
    for r in out_tables:
        orig_r = orig_by_id[r["id"]]
        assert set(r["columns"].keys()) == set(orig_r["columns"].keys())
        for key_col in ("cliente_id", "tx_id", "radicado", "id"):
            if key_col in orig_r["columns"]:
                assert r["columns"][key_col] == orig_r["columns"][key_col]


def test_api_endpoints() -> None:
    client = TestClient(app)

    # 1. GET /v1/health
    r_health = client.get("/v1/health")
    assert r_health.status_code == 200
    body_h = r_health.json()
    assert body_h["status"] == "ok"
    assert isinstance(body_h["team"], str) and body_h["team"]

    # 2. POST /v1/anonymize (valid chat record)
    r_anon = client.post(
        "/v1/anonymize",
        json={
            "source": "chats",
            "records": [
                {
                    "id": "fea922de-00f1-5c98-a036-4c7540983788",
                    "thread_id": "soporte-cobros:1785769807.000100",
                    "author": "Manuela Castaño",
                    "ts": "1785769807.000100",
                    "text": "Hola, me llamo Manuela Castaño. Me están cobrando una cuota que ya pagué.",
                    "attachments": [],
                }
            ],
        },
    )
    assert r_anon.status_code == 200
    data_anon = r_anon.json()
    assert len(data_anon["records"]) == 1
    assert not contiene(data_anon["records"][0]["text"], "Manuela Castaño")
    assert not contiene(data_anon["records"][0]["author"], "Manuela Castaño")
    assert len(data_anon["entities"]) >= 1

    # 3. POST /v1/anonymize (422 out of schema)
    r_bad = client.post(
        "/v1/anonymize",
        json={"source": "chats", "records": [{"id": "missing_required_fields"}]},
    )
    assert r_bad.status_code == 422

    # 4. GET /v1/output/* and GET /v1/entities
    for fname in ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json"):
        r_out = client.get(f"/v1/output/{fname}")
        assert r_out.status_code == 200
        assert len(r_out.text) > 100

    r_ents = client.get("/v1/entities")
    assert r_ents.status_code == 200
    assert "entities" in r_ents.json()


def test_no_real_pii_in_logs_and_docs() -> None:
    """Verify invariant §5.11: real PII from fuentes/ never leaks into logs/, DECISIONS.md, or failure_analysis.md."""
    trace_path = ROOT_DIR / "logs" / "execution_trace.jsonl"
    decisions_path = ROOT_DIR / "DECISIONS.md"
    failure_path = ROOT_DIR / "failure_analysis.md"

    # Sample of private client emails and cedulas from fuentes/ (excluding public test examples)
    private_markers = (
        "jmejia@gmail.com",
        "31066233",
        "carolina.arango@yahoo.com",
        "52550162",
        "ocorrea@hotmail.com",
        "1088582908",
    )
    for p in (trace_path, decisions_path, failure_path):
        if p.exists():
            txt = p.read_text(encoding="utf-8")
            for marker in private_markers:
                assert marker not in txt, f"Real PII marker {marker!r} leaked in {p.name}"

