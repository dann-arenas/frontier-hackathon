"""Background job execution for `POST /v1/run` in a separate process with `node validar.mjs` gate."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import JOBS_DIR, OUTPUT_DIR, ROOT_DIR, STATE_DIR
from app.normalize import contiene, digitos_en
from app.pipeline import run_full_pipeline
from app.sources import fetch_from_api, load_from_local_dir
from app.telemetry import log_step, record_failure_analysis


def _validate_staging_python_fallback(staging_dir: Path, public_idx_dir: Path) -> tuple[bool, str]:
    """In-process Python port of `validar.mjs` used only if the `node` binary is absent."""
    try:
        chats = [json.loads(l) for l in (staging_dir / "chats.jsonl").read_text("utf-8").splitlines() if l.strip()]
        docs = [json.loads(l) for l in (staging_dir / "docs.jsonl").read_text("utf-8").splitlines() if l.strip()]
        tables = [json.loads(l) for l in (staging_dir / "tables.jsonl").read_text("utf-8").splitlines() if l.strip()]
        ents_obj = json.loads((staging_dir / "entities.json").read_text("utf-8"))
        entities = ents_obj.get("entities", [])
    except Exception as e:
        return False, f"Missing or invalid output files: {e}"

    by_id: dict[str, str] = {}
    for r in chats + docs + tables:
        rid = r.get("id")
        if not rid or rid in by_id:
            return False, f"Duplicate or missing id: {rid}"
        by_id[rid] = json.dumps(r, ensure_ascii=False)

    idx_path = public_idx_dir / "record_index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text("utf-8"))
        for k, sec in (("chats", chats), ("docs", docs), ("tables", tables)):
            expected = set(idx.get(k, []))
            got = {r["id"] for r in sec}
            if expected != got:
                return False, f"{k} record_index mismatch"

    full_text = "\n".join(by_id.values())
    for ent in entities:
        recs = ent.get("records", [])
        if not recs:
            return False, f"Entity {ent.get('entity_id')} has empty records"
        if ent.get("type") == "person" and not ent.get("kept"):
            for orig in ent.get("originals", []):
                if contiene(full_text, orig) or digitos_en(full_text, orig):
                    return False, f"Entity {ent.get('entity_id')}: original leaked ({orig})"
            for k, val in (ent.get("pseudonyms") or {}).items():
                if not any(contiene(by_id.get(rid, ""), str(val)) or digitos_en(by_id.get(rid, ""), str(val)) for rid in recs):
                    return False, f"Entity {ent.get('entity_id')}: pseudonym {k}={val} not found in records"
    return True, "OK: la salida pasa el validador"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(job_id: str) -> Path:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR / f"{job_id}.json"


def read_job(job_id: str) -> dict[str, Any] | None:
    p = _job_path(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_job(job_id: str, payload: dict[str, Any]) -> None:
    p = _job_path(job_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def create_and_launch_job(sources_url: str, token: str) -> str:
    """Create a job state file and spawn a detached subprocess to run the pipeline."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for jf in sorted(JOBS_DIR.glob("job_*.json"), reverse=True):
        try:
            if now - jf.stat().st_mtime < 120.0:
                jdata = json.loads(jf.read_text(encoding="utf-8"))
                if jdata.get("status") in ("queued", "running"):
                    return jf.stem
        except Exception:
            continue

    job_id = f"job_{int(now)}_{uuid.uuid4().hex[:8]}"
    initial_state: dict[str, Any] = {
        "status": "queued",
        "started_at": _utc_now_iso(),
        "records": {"chats": 0, "docs": 0, "tables": 0},
        "cost": {"tokens_in": 0, "tokens_out": 0, "seconds": 0.0},
    }
    write_job(job_id, initial_state)

    subprocess.Popen(
        [sys.executable, "-m", "app.jobs", job_id, sources_url, token],
        cwd=str(ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return job_id


def execute_job_sync(job_id: str, sources_url: str, token: str) -> dict[str, Any]:
    """Execute the full regeneration pipeline, run `node validar.mjs` gate, and publish to `output/`."""
    t0 = time.perf_counter()
    started_at = _utc_now_iso()
    existing = read_job(job_id)
    if existing and "started_at" in existing:
        started_at = existing["started_at"]

    running_state: dict[str, Any] = {
        "status": "running",
        "started_at": started_at,
        "records": {"chats": 0, "docs": 0, "tables": 0},
        "cost": {"tokens_in": 0, "tokens_out": 0, "seconds": 0.0},
    }
    write_job(job_id, running_state)

    staging_dir = STATE_DIR / "staging" / job_id
    public_idx_dir = STATE_DIR / "public_idx" / job_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    public_idx_dir.mkdir(parents=True, exist_ok=True)

    try:
        if sources_url.startswith("local:"):
            local_path = Path(sources_url.split("local:", 1)[1])
            bundle = load_from_local_dir(local_path)
            (public_idx_dir / "record_index.json").write_text(
                json.dumps(bundle.record_index, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            try:
                bundle = asyncio.run(fetch_from_api(sources_url, token, save_public_dir=public_idx_dir))
            except Exception as fetch_exc:
                # Fallback to local `fuentes/` only if API is unreachable and local copy exists
                fallback_dir = ROOT_DIR / "fuentes"
                if fallback_dir.exists() and (fallback_dir / "record_index.json").exists():
                    bundle = load_from_local_dir(fallback_dir)
                    (public_idx_dir / "record_index.json").write_text(
                        json.dumps(bundle.record_index, ensure_ascii=False),
                        encoding="utf-8",
                    )
                else:
                    raise fetch_exc

        res = run_full_pipeline(bundle, output_dir=staging_dir, use_llm=True)

        # Mandatory `node validar.mjs` gate before publishing to `output/`
        if shutil.which("node"):
            val_proc = subprocess.run(
                ["node", str(ROOT_DIR / "validar.mjs"), str(staging_dir), "--public", str(public_idx_dir)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=60,
            )
            val_ok = val_proc.returncode == 0 and "OK: la salida pasa el validador" in val_proc.stdout
            err_msg = (val_proc.stdout + "\n" + val_proc.stderr).strip()
        else:
            val_ok, err_msg = _validate_staging_python_fallback(staging_dir, public_idx_dir)

        if not val_ok:
            log_step(
                stage="validate",
                record=job_id,
                mode="DET",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                status="FAIL",
            )
            record_failure_analysis(
                case_id=job_id,
                component="validar.mjs",
                root_cause="Validator gate rejected staging output",
                mitigation="Aborted promotion to output/; preserving last valid run",
            )
            raise RuntimeError(f"Validator gate failed: {err_msg[:400]}")

        log_step(
            stage="validate",
            record=job_id,
            mode="DET",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            status="PASS",
        )

        # Promote staging files to `output/` and `state/latest_output/`
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        latest_out_dir = STATE_DIR / "latest_output"
        latest_out_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json"):
            src_f = staging_dir / fname
            dst_f = OUTPUT_DIR / fname
            tmp_f = OUTPUT_DIR / f"{fname}.tmp"
            shutil.copy2(src_f, tmp_f)
            tmp_f.replace(dst_f)
            shutil.copy2(dst_f, latest_out_dir / fname)

        elapsed = round(time.perf_counter() - t0, 3)
        done_state: dict[str, Any] = {
            "status": "done",
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "records": res["records"],
            "cost": {
                "tokens_in": int(res.get("tokens_in", 0)),
                "tokens_out": int(res.get("tokens_out", 0)),
                "usd": round(float(res.get("cost_usd", 0.0)), 6),
                "seconds": elapsed,
            },
        }
        write_job(job_id, done_state)
        shutil.rmtree(staging_dir, ignore_errors=True)
        return done_state

    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 3)
        failed_state: dict[str, Any] = {
            "status": "failed",
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "records": {"chats": 0, "docs": 0, "tables": 0},
            "cost": {
                "tokens_in": 0,
                "tokens_out": 0,
                "seconds": elapsed,
            },
            "error": str(exc)[:500],
        }
        write_job(job_id, failed_state)
        record_failure_analysis(
            case_id=job_id,
            component="jobs.execute_job_sync",
            root_cause=type(exc).__name__,
            mitigation="Marked job status as failed",
        )
        return failed_state


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        execute_job_sync(sys.argv[1], sys.argv[2], sys.argv[3])
