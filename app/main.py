"""FastAPI participant service exposing `/v1` (and root aliases) for Frontier PII evaluation."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import JOBS_DIR, OUTPUT_DIR, ROOT_DIR, SOURCES_TOKEN, SOURCES_URL, STATE_DIR, TEAM_NAME, VERSION, get_active_models
from app.jobs import create_and_launch_job, read_job
from app.pipeline import LiveAnonymizer


def _trigger_warmup_if_needed(auth_header: str | None = None) -> str | None:
    if "pytest" in sys.modules:
        return None
    if (OUTPUT_DIR / "entities.json").exists() or (STATE_DIR / "latest_output" / "entities.json").exists():
        return None
    if (ROOT_DIR / "fuentes" / "record_index.json").exists():
        return create_and_launch_job(f"local:{ROOT_DIR / 'fuentes'}", "")
    tok = SOURCES_TOKEN
    if not tok and auth_header:
        tok = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
    if SOURCES_URL and tok:
        return create_and_launch_job(SOURCES_URL.rstrip("/"), tok)
    return None


async def _wait_for_output_file(fname: str, auth_header: str | None = None, max_wait: float = 25.0) -> Path | None:
    for p in (OUTPUT_DIR / fname, STATE_DIR / "latest_output" / fname):
        if p.exists():
            return p
    if "pytest" in sys.modules:
        return None
    _trigger_warmup_if_needed(auth_header)
    # If any job is currently queued or running, wait up to max_wait seconds for it to publish
    has_active = any(
        json.loads(jf.read_text("utf-8")).get("status") in ("queued", "running")
        for jf in JOBS_DIR.glob("job_*.json")
        if jf.exists()
    )
    if not has_active:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    while loop.time() < deadline:
        for p in (OUTPUT_DIR / fname, STATE_DIR / "latest_output" / fname):
            if p.exists():
                return p
        await asyncio.sleep(0.3)
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _trigger_warmup_if_needed()
    yield


app = FastAPI(
    title="Reto PII - Kipu Pay Anonymization Service",
    version=VERSION,
    lifespan=lifespan,
)

router = APIRouter()
live_anonymizer = LiveAnonymizer()

VALID_OUTPUT_FILES = ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json")


class RunRequest(BaseModel):
    sources_url: str = ""
    token: str = ""


class AnonymizeRequest(BaseModel):
    source: Literal["chats", "docs", "tables"]
    records: list[dict[str, Any]] = Field(..., max_length=50)


def _validate_records_schema(source: str, records: list[dict[str, Any]]) -> None:
    if not isinstance(records, list) or len(records) > 50:
        raise HTTPException(status_code=422, detail="records must be a list of at most 50 items")

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise HTTPException(status_code=422, detail=f"records[{idx}] must be an object")
        if source == "chats":
            req = ("id", "thread_id", "author", "ts", "text", "attachments")
            for k in req:
                if k not in rec:
                    raise HTTPException(status_code=422, detail=f"chats record[{idx}] missing '{k}'")
            if not isinstance(rec["id"], str) or not isinstance(rec["text"], str):
                raise HTTPException(status_code=422, detail=f"chats record[{idx}] invalid field types")
            if not isinstance(rec["attachments"], list):
                raise HTTPException(status_code=422, detail=f"chats record[{idx}] attachments must be list")
        elif source == "docs":
            req = ("id", "format", "filename", "title", "body", "metadata")
            for k in req:
                if k not in rec:
                    raise HTTPException(status_code=422, detail=f"docs record[{idx}] missing '{k}'")
            if rec["format"] not in ("pdf", "html", "md"):
                raise HTTPException(status_code=422, detail=f"docs record[{idx}] invalid format")
            if not isinstance(rec["metadata"], dict):
                raise HTTPException(status_code=422, detail=f"docs record[{idx}] metadata must be object")
        elif source == "tables":
            req = ("id", "table", "columns")
            for k in req:
                if k not in rec:
                    raise HTTPException(status_code=422, detail=f"tables record[{idx}] missing '{k}'")
            if rec["table"] not in ("clientes", "transacciones", "pqrs", "empleados"):
                raise HTTPException(status_code=422, detail=f"tables record[{idx}] invalid table")
            if not isinstance(rec["columns"], dict):
                raise HTTPException(status_code=422, detail=f"tables record[{idx}] columns must be object")


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "team": TEAM_NAME,
        "version": VERSION,
        "models": get_active_models(),
    }


@router.post("/run", status_code=202)
async def start_run(
    body: RunRequest | None = Body(default=None),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    s_url = (body.sources_url if body and body.sources_url else SOURCES_URL).rstrip("/")
    tok = body.token if body and body.token else ""
    if not tok and authorization:
        tok = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    if not tok:
        tok = SOURCES_TOKEN
    job_id = create_and_launch_job(s_url, tok)
    return JSONResponse(status_code=202, content={"job_id": job_id})


@router.get("/run/{job_id}")
async def get_run_status(job_id: str) -> dict[str, Any]:
    job = read_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/output/{archivo}")
async def get_output_file(
    archivo: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    if archivo not in VALID_OUTPUT_FILES:
        raise HTTPException(status_code=404, detail="Invalid output filename")
    fpath = await _wait_for_output_file(archivo, authorization)
    if fpath is None or not fpath.exists():
        raise HTTPException(status_code=404, detail="No completed run output available")
    media_type = (
        "application/json; charset=utf-8"
        if archivo.endswith(".json")
        else "application/x-ndjson; charset=utf-8"
    )
    return FileResponse(path=fpath, media_type=media_type, filename=archivo)


@router.post("/anonymize")
async def anonymize_records(
    body: AnonymizeRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _validate_records_schema(body.source, body.records)
    _trigger_warmup_if_needed(authorization)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(live_anonymizer.anonymize, body.source, body.records),
            timeout=45.0,
        )
        return result
    except asyncio.TimeoutError:
        # Fallback synchronous deterministic execution if thread timed out
        return live_anonymizer.anonymize(body.source, body.records)


@router.get("/entities")
async def get_entities(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    fpath = await _wait_for_output_file("entities.json", authorization)
    if fpath is None or not fpath.exists():
        raise HTTPException(status_code=404, detail="No completed run entities available")
    return json.loads(fpath.read_text(encoding="utf-8"))


# Mount under `/v1` (official OpenAPI server base) and root `/` (in case entrypoint is registered with or without `/v1`)
app.include_router(router, prefix="/v1")
app.include_router(router, prefix="")
app.include_router(router, prefix="/v1/v1", include_in_schema=False)


@app.get("/", include_in_schema=False)
@app.get("/v1", include_in_schema=False)
async def root_health() -> dict[str, Any]:
    return await health()
