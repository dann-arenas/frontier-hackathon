PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
PYTEST := $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)

.PHONY: regen test validate score-sample

regen:
	$(PYTHON) -u -c 'import asyncio; from pathlib import Path; from app.config import SOURCES_URL, SOURCES_TOKEN; from app.sources import fetch_from_api, load_from_local_dir; from app.pipeline import run_full_pipeline; b = asyncio.run(fetch_from_api(SOURCES_URL, SOURCES_TOKEN, save_public_dir=Path("fuentes"))) if SOURCES_TOKEN else load_from_local_dir(Path("fuentes")); print(run_full_pipeline(b))'
	node validar.mjs output --public fuentes

validate:
	node validar.mjs output --public fuentes

test:
	$(PYTHON) -m pytest -v tests/

score-sample:
	$(PYTHON) -u -c 'from pathlib import Path; from app.sources import load_from_local_dir; from app.pipeline import run_full_pipeline; res = run_full_pipeline(load_from_local_dir(Path("sample/input")), output_dir=Path("state/sample_out"), ledger_path=Path("state/sample_ledger.json"), snapshot_path=Path("state/sample_snapshot.json")); print("Sample pipeline:", res)'
	node validar.mjs state/sample_out --public sample
