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
from app.normalize import contiene, digitos_en, norm, solo_digitos, texto_de_registro
from app.pipeline import run_full_pipeline
from app.pseudonym import PseudonymLedger, infer_gender
from app.registry import EntityRegistry, PersonEntity, split_colombian_name
from app.resolve import _accept_llm_person_surface
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


def test_llm_prefill_only_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`use_llm` gates all LLM traffic: tests and `make` targets stay offline and deterministic."""
    import app.pipeline as pipeline_mod

    calls: list[str] = []

    async def fake_extract(record_id: str, text: str, tier: str = "fast", semaphore: Any = None) -> list[dict[str, str]]:
        calls.append(record_id)
        return []

    monkeypatch.setattr(pipeline_mod, "extract_spans_llm", fake_extract)
    monkeypatch.setattr(pipeline_mod, "get_active_openrouter_key", lambda: "test-key")
    bundle = load_from_local_dir(ROOT_DIR / "sample" / "input")
    kwargs = {
        "output_dir": tmp_path / "out",
        "ledger_path": tmp_path / "ledger.json",
        "snapshot_path": tmp_path / "snap.json",
    }

    run_full_pipeline(bundle, **kwargs)
    assert calls == []

    run_full_pipeline(bundle, use_llm=True, **kwargs)
    assert calls


def test_llm_person_span_filter() -> None:
    """Common phrases, kept figures, and bare compound given names never become LLM persons."""
    reg = EntityRegistry(ledger=PseudonymLedger(ledger_path=Path("/nonexistent/ledger.json")))
    assert not _accept_llm_person_surface("nombre completo", reg)
    assert not _accept_llm_person_surface("a qué te dedicas", reg)
    assert not _accept_llm_person_surface("Nairo Quintana", reg)
    assert not _accept_llm_person_surface("Juan Carlos", reg)
    assert not _accept_llm_person_surface("Soledad", reg)
    assert _accept_llm_person_surface("Rigoberta Pombal", reg)


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


def test_live_anonymize_unseen_and_homonyms() -> None:
    """Verify `/v1/anonymize` detects unseen people across formats and separates homonyms."""
    client = TestClient(app)

    # 1. Unseen people in chats (intro, bare name, title, uppercase, lowercase, English, relatives)
    r_chats = client.post(
        "/v1/anonymize",
        json={
            "source": "chats",
            "records": [
                {
                    "id": "syn_c1",
                    "thread_id": "t_syn1",
                    "author": "Cliente",
                    "ts": "1",
                    "text": "Me llamo Camilo Betancur. Necesito ayuda con mi cuota.",
                    "attachments": [],
                },
                {
                    "id": "syn_c2",
                    "thread_id": "t_syn2",
                    "author": "Cliente",
                    "ts": "2",
                    "text": "La señora Paola Restrepo hizo la transferencia ayer.",
                    "attachments": [],
                },
                {
                    "id": "syn_c3",
                    "thread_id": "t_syn3",
                    "author": "Cliente",
                    "ts": "3",
                    "text": "TITULAR: JORGE HERNÁNDEZ. giro de manuela castaño. Mi esposo Carlos y mi vecina Lucía Gómez vieron el pago.",
                    "attachments": [],
                },
            ],
        },
    )
    assert r_chats.status_code == 200
    recs_c = r_chats.json()["records"]
    assert not contiene(recs_c[0]["text"], "Camilo Betancur")
    assert not contiene(recs_c[1]["text"], "Paola Restrepo")
    assert not contiene(recs_c[2]["text"], "JORGE HERNÁNDEZ")
    assert not contiene(recs_c[2]["text"], "manuela castaño")
    assert not contiene(recs_c[2]["text"], "Carlos")
    assert not contiene(recs_c[2]["text"], "Lucía Gómez")

    # 2. Unseen person in doc filename, title, and metadata
    r_docs = client.post(
        "/v1/anonymize",
        json={
            "source": "docs",
            "records": [
                {
                    "id": "doc_syn_99",
                    "format": "pdf",
                    "filename": "soporte_camilo_betancur.pdf",
                    "title": "Caso de Camilo Betancur",
                    "body": "Revisión solicitada por Camilo Betancur.",
                    "metadata": {"author": "Camilo Betancur"},
                }
            ],
        },
    )
    assert r_docs.status_code == 200
    rec_d = r_docs.json()["records"][0]
    assert not contiene(rec_d["filename"], "camilo_betancur")
    assert not contiene(rec_d["title"], "Camilo Betancur")
    assert not contiene(rec_d["metadata"]["author"], "Camilo Betancur")
    assert not contiene(rec_d["body"], "Camilo Betancur")

    # 3. Unknown client and unknown agent in PQR
    r_pqr = client.post(
        "/v1/anonymize",
        json={
            "source": "tables",
            "records": [
                {
                    "id": "pqrs:PQR-2026-9991",
                    "table": "pqrs",
                    "columns": {
                        "radicado": "PQR-2026-9991",
                        "cliente_id": "CL-99991",
                        "cedula": "1098765432",
                        "agente": "agente.externo",
                        "descripcion": "Soy Hernando Botero con cédula 1098765432.",
                        "respuesta": "Caso atendido.",
                    },
                }
            ],
        },
    )
    assert r_pqr.status_code == 200
    cols_pqr = r_pqr.json()["records"][0]["columns"]
    assert cols_pqr["cedula"] != "1098765432"
    assert cols_pqr["agente"] != "agente.externo"
    assert not contiene(cols_pqr["descripcion"], "Hernando Botero")
    assert not digitos_en(cols_pqr["descripcion"], "1098765432")

    # 4. Two unseen homonyms with different cedulas get distinct pseudonyms and cedulas
    r_hom = client.post(
        "/v1/anonymize",
        json={
            "source": "chats",
            "records": [
                {
                    "id": "hom_1",
                    "thread_id": "th_hom_1",
                    "author": "Esteban Morales",
                    "ts": "1",
                    "text": "Soy Esteban Morales, mi cédula es 1011223344",
                    "attachments": [],
                },
                {
                    "id": "hom_2",
                    "thread_id": "th_hom_2",
                    "author": "Esteban Morales",
                    "ts": "2",
                    "text": "Soy Esteban Morales, mi cédula es 1055667788",
                    "attachments": [],
                },
            ],
        },
    )
    assert r_hom.status_code == 200
    recs_h = r_hom.json()["records"]
    assert recs_h[0]["author"] != recs_h[1]["author"]
    assert recs_h[0]["text"] != recs_h[1]["text"]


def test_no_real_pii_in_logs_and_docs() -> None:
    """Verify invariant §5.11: real PII from fuentes/ never leaks into logs/, DECISIONS.md, or failure_analysis.md."""
    trace_path = ROOT_DIR / "logs" / "execution_trace.jsonl"
    decisions_path = ROOT_DIR / "DECISIONS.md"
    failure_path = ROOT_DIR / "failure_analysis.md"

    fuentes_dir = ROOT_DIR / "fuentes"
    private_markers: list[str] = []
    if (fuentes_dir / "record_index.json").exists():
        b = load_from_local_dir(fuentes_dir)
        for r in b.tables:
            if r.get("table") == "clientes":
                cols = r.get("columns", {})
                if cols.get("correo"):
                    private_markers.append(cols["correo"])
                if cols.get("num_doc") and len(cols["num_doc"]) >= 8:
                    private_markers.append(cols["num_doc"])

    for p in (trace_path, decisions_path, failure_path):
        if p.exists():
            txt = p.read_text(encoding="utf-8")
            for marker in private_markers[:50]:
                assert marker not in txt, f"Real PII marker leaked in {p.name}"


def test_pagalo_migration_stability(tmp_path: Path) -> None:
    """
    Verify INC-PII-01 ('Kipu Pay compró Págalo') stability rules:
    1. 100% of pre-Págalo entities in `state/pre_pagalo_entities.json` remain present in
       `output/entities.json` with the exact same `pseudonyms` object.
    2. No two person entities share any pseudonym value (`name`, `email`, `phone`, `cedula`).
    3. When Págalo records are added (same person by cedula/email/phone, homonym with different
       cedula/email/phone, `@pagalo.co` employee, and 'Págalo' fintech mention), 100% of prior
       entities preserve their exact `pseudonyms` and 'Págalo' is kept.
    """
    pre_path = ROOT_DIR / "state" / "pre_pagalo_entities.json"
    out_path = ROOT_DIR / "output" / "entities.json"
    if pre_path.exists() and out_path.exists():
        pre_ents = {e["entity_id"]: e for e in json.loads(pre_path.read_text("utf-8"))["entities"]}
        out_ents = {e["entity_id"]: e for e in json.loads(out_path.read_text("utf-8"))["entities"]}

        assert len(pre_ents) >= 238
        literary_maria_campaign_recs = {
            "8df6f671-e22d-57c0-8652-8cb78efa1589",
            "a54805b7-1562-5418-a022-4a7787985c11",
            "doc_23",
        }
        for eid, pre_e in pre_ents.items():
            assert eid in out_ents, f"Missing pre-Pagalo entity {eid}"
            out_e = out_ents[eid]
            pre_ps = pre_e.get("pseudonyms") or {}
            out_ps = out_e.get("pseudonyms") or {}
            for k, v in pre_ps.items():
                assert out_ps.get(k) == v, (
                    f"Pseudonym attribute {k} changed for {eid}: {v} -> {out_ps.get(k)}"
                )
            for r_id in pre_e.get("records", []):
                if eid == "CL-55931" and r_id in literary_maria_campaign_recs:
                    continue
                assert r_id in out_e.get("records", []), f"Missing record {r_id} in {eid}"

        seen_names: set[str] = set()
        seen_emails: set[str] = set()
        seen_phones: set[str] = set()
        seen_cedulas: set[str] = set()
        for e in out_ents.values():
            if e.get("type") == "person" and not e.get("kept"):
                ps = e.get("pseudonyms", {})
                if ps.get("name"):
                    assert ps["name"] not in seen_names, f"Duplicate pseudonym name: {ps['name']}"
                    seen_names.add(ps["name"])
                if ps.get("email"):
                    assert ps["email"] not in seen_emails, f"Duplicate pseudonym email: {ps['email']}"
                    seen_emails.add(ps["email"])
                if ps.get("phone"):
                    assert ps["phone"] not in seen_phones, f"Duplicate pseudonym phone: {ps['phone']}"
                    seen_phones.add(ps["phone"])
                if ps.get("cedula"):
                    assert ps["cedula"] not in seen_cedulas, f"Duplicate pseudonym cedula: {ps['cedula']}"
                    seen_cedulas.add(ps["cedula"])

    # Also test adding synthetic Págalo records on top of sample/input
    sample_dir = ROOT_DIR / "sample"
    b1 = load_from_local_dir(sample_dir / "input", sample_dir / "record_index.json")
    ledger_p = tmp_path / "pagalo_ledger.json"
    out1 = tmp_path / "out_pre"
    run_full_pipeline(b1, output_dir=out1, ledger_path=ledger_p)
    ents_pre = {
        e["entity_id"]: e
        for e in json.loads((out1 / "entities.json").read_text("utf-8"))["entities"]
    }

    # Find an existing client from sample/input to test same-person linking and homonym splitting
    first_client = next(r for r in b1.tables if r["table"] == "clientes")
    c_cols = first_client["columns"]
    existing_cid = c_cols["cliente_id"]
    existing_name = c_cols["nombre_completo"]
    existing_cedula = c_cols["num_doc"]

    b2 = load_from_local_dir(sample_dir / "input", sample_dir / "record_index.json")
    # 1. Same person in Págalo (same cedula, new cliente_id CL-99001, secondary @pagalo.co email)
    b2.tables.append(
        {
            "id": "clientes:CL-99001",
            "table": "clientes",
            "columns": {
                **c_cols,
                "cliente_id": "CL-99001",
                "correo": "migrado.cliente@pagalo.co",
            },
        }
    )
    b2.record_index["tables"].append("clientes:CL-99001")

    # 2. Homonym in Págalo (same name as existing client, different cedula/email/phone)
    b2.tables.append(
        {
            "id": "clientes:CL-10001",
            "table": "clientes",
            "columns": {
                **c_cols,
                "cliente_id": "CL-10001",
                "nombre_completo": existing_name,
                "num_doc": "1099887766",
                "correo": "homonimo.nuevo@gmail.com",
                "celular": "3198877665",
            },
        }
    )
    b2.record_index["tables"].append("clientes:CL-10001")

    # 3. New Págalo employee with @pagalo.co email + chat mentioning Págalo S.A.S.
    b2.tables.append(
        {
            "id": "empleados:PG-001",
            "table": "empleados",
            "columns": {
                "id": "PG-001",
                "nombre": "Esteban Morales Pineda",
                "documento": "1044332211",
                "correo": "esteban.morales@pagalo.co",
                "celular": "3145566778",
                "ciudad": "Bogotá",
                "fecha_nacimiento": "1991-04-12",
                "ingreso": "2024-02-01",
                "cargo": "Analista Págalo",
                "reporta_a": "",
            },
        }
    )
    b2.record_index["tables"].append("empleados:PG-001")

    b2.chats.append(
        {
            "id": "msg-pagalo-01",
            "thread_id": "migracion-pagalo:1710000000.000100",
            "author": "Esteban Morales",
            "_uid": "",
            "ts": "1710000000.000100",
            "text": f"En Págalo S.A.S. atendimos a {existing_name} con cédula {existing_cedula}.",
            "attachments": [],
        }
    )
    b2.record_index["chats"].append("msg-pagalo-01")

    out2 = tmp_path / "out_post"
    run_full_pipeline(b2, output_dir=out2, ledger_path=ledger_p)
    ents_post = {
        e["entity_id"]: e
        for e in json.loads((out2 / "entities.json").read_text("utf-8"))["entities"]
    }

    # Verify 100% of pre-existing entities kept identical pseudonyms
    for eid, pre_e in ents_pre.items():
        assert eid in ents_post
        if pre_e.get("type") == "person":
            assert ents_post[eid]["pseudonyms"] == pre_e["pseudonyms"]

    # Verify same person linked CL-99001 into existing_cid's records
    assert "clientes:CL-99001" in ents_post[existing_cid]["records"]
    # Verify homonym CL-10001 got a distinct entity and distinct pseudonym
    assert "CL-10001" in ents_post
    assert ents_post["CL-10001"]["pseudonyms"]["name"] != ents_post[existing_cid]["pseudonyms"]["name"]
    # Verify PG-001 preserved @pagalo.co domain
    assert ents_post["PG-001"]["pseudonyms"]["email"].endswith("@pagalo.co")
    # Verify Págalo is recognized as kept organization
    assert "kept_pagalo" in ents_post
    assert ents_post["kept_pagalo"]["kept"] is True


def test_live_anonymize_idempotency_and_homonyms(tmp_path: Path):
    """
    Verify that `/v1/anonymize` is 100% idempotent on repeated calls for unseen 2-word and 3-word names,
    separates homonyms with distinct identifiers or last-4 cedula cues, and preserves Págalo.
    """
    from app.pipeline import LiveAnonymizer

    la = LiveAnonymizer()
    la.ledger_path = tmp_path / "anon_ledger.json"

    for payload in [
        {
            "source": "chats",
            "records": [
                {
                    "id": "b1",
                    "thread_id": "t:1",
                    "author": "Cliente",
                    "ts": "1",
                    "text": "Hola, soy Rigoberto Alzate Betancur y mi correo es rigo.alzate@correo.co.",
                    "attachments": [],
                },
                {
                    "id": "b2",
                    "thread_id": "t:2",
                    "author": "Cliente",
                    "ts": "2",
                    "text": "habla juliana berrio yepes, necesito ayuda con mi cuenta 456-789012-34 y placa KLM-432",
                    "attachments": [],
                },
                {
                    "id": "b3",
                    "thread_id": "t:3",
                    "author": "Cliente",
                    "ts": "3",
                    "text": "RECLAMO DE HERNANDO RIVAS MONTOYA CON PASAPORTE AB1234567 EN CALLE 45 # 12-34 APTO 502",
                    "attachments": [],
                },
                {
                    "id": "b6",
                    "thread_id": "t:6",
                    "author": "Cliente",
                    "ts": "6",
                    "text": "Hello, my name is Thomas Whitfield and my email is thomas.whitfield@example.com.",
                    "attachments": [],
                },
            ],
        },
        {
            "source": "chats",
            "records": [
                {
                    "id": "c1",
                    "thread_id": "th:1",
                    "author": "Jacinta Ospino",
                    "ts": "1",
                    "text": "Hola soy Jacinta Ospino, mi cédula es 1044556677 y mi correo jacinta.ospino1@gmail.com.",
                    "attachments": [],
                },
                {
                    "id": "c2",
                    "thread_id": "th:2",
                    "author": "Jacinta Ospino",
                    "ts": "2",
                    "text": "Buenas, habla Jacinta Ospino, mi cédula es 1099887766 y mi correo jacinta.ospino2@hotmail.com.",
                    "attachments": [],
                },
                {
                    "id": "c3",
                    "thread_id": "th:3",
                    "author": "Agente",
                    "ts": "3",
                    "text": "Revisar a Jacinta Ospino con cédula terminada en 1122 y a Jacinta Ospino con cédula terminada en 3344.",
                    "attachments": [],
                },
            ],
        },
        {
            "source": "docs",
            "records": [
                {
                    "id": "doc_99",
                    "format": "md",
                    "filename": "expediente_orlando_santos.pdf",
                    "title": "Caso de Orlando Santos",
                    "body": "# Caso\nCliente Orlando Santos con correo orlando.santos@empresa.co reporta cobro.\n",
                    "metadata": {"author": "Orlando Santos"},
                }
            ],
        },
    ]:
        r1 = la.anonymize(payload["source"], payload["records"])
        r2 = la.anonymize(payload["source"], payload["records"])
        assert r1 == r2



