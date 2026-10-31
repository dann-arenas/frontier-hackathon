"""Configuration, environment variables, paths, and OpenRouter budget control."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

TEAM_NAME: str = os.getenv("TEAM_NAME", "abh_cdmx")
VERSION: str = os.getenv("APP_VERSION", "1.0.0")

SOURCES_URL: str = (
    os.getenv("SOURCES_URL")
    or os.getenv("SOURCES_BASE_URL")
    or ""
).rstrip("/")
SOURCES_TOKEN: str = os.getenv("SOURCES_TOKEN", "")

PSEUDONYM_SECRET: str = os.getenv(
    "PSEUDONYM_SECRET", "dev-local-fallback-seed"
)
RUN_MODE: str = os.getenv("RUN_MODE", "dev")

STATE_DIR: Path = Path(os.getenv("STATE_DIR", str(ROOT_DIR / "state"))).resolve()
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(ROOT_DIR / "output"))).resolve()
LOGS_DIR: Path = Path(os.getenv("LOGS_DIR", str(ROOT_DIR / "logs"))).resolve()
FUENTES_DIR: Path = Path(os.getenv("FUENTES_DIR", str(ROOT_DIR / "fuentes"))).resolve()
SAMPLE_DIR: Path = Path(os.getenv("SAMPLE_DIR", str(ROOT_DIR / "sample"))).resolve()

LEDGER_PATH: Path = STATE_DIR / "entity_ledger.json"
REGISTRY_SNAPSHOT_PATH: Path = STATE_DIR / "registry_snapshot.json"
HTTP_CACHE_DIR: Path = STATE_DIR / "http_cache"
LLM_CACHE_DIR: Path = STATE_DIR / "llm_cache"
JOBS_DIR: Path = STATE_DIR / "jobs"
LAST_INDEX_PATH: Path = STATE_DIR / "last_record_index.json"

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_EVAL_KEY: str = os.getenv("OPENROUTER_EVAL_KEY", "")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

FAST_MODELS: list[str] = [
    os.getenv("LLM_FAST_MODEL", "openai/gpt-4o-mini"),
    "google/gemini-2.5-flash-lite",
]
HEAVY_MODELS: list[str] = [
    os.getenv("LLM_HEAVY_MODEL", "openai/gpt-4o-mini"),
    "google/gemini-2.5-flash",
]
MODELS_FAST: list[str] = FAST_MODELS
MODELS_HEAVY: list[str] = HEAVY_MODELS
LLM_CONCURRENCY_INITIAL: int = int(os.getenv("LLM_CONCURRENCY", "16"))
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "4.0"))
LLM_MAX_CALLS_PER_RUN: int = int(os.getenv("LLM_MAX_CALLS_PER_RUN", "2"))

# Ensure directories exist
for _d in (STATE_DIR, OUTPUT_DIR, LOGS_DIR, HTTP_CACHE_DIR, LLM_CACHE_DIR, JOBS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def get_active_models() -> list[str]:
    """Return list of configured LLM models if an OpenRouter key is configured."""
    key = get_openrouter_key()
    if not key:
        return []
    seen: list[str] = []
    for m in FAST_MODELS + HEAVY_MODELS:
        if m and m not in seen:
            seen.append(m)
    return seen


def get_openrouter_key() -> str:
    """Return appropriate OpenRouter API key according to RUN_MODE."""
    if RUN_MODE == "eval" and OPENROUTER_EVAL_KEY:
        return OPENROUTER_EVAL_KEY
    return OPENROUTER_API_KEY


get_active_openrouter_key = get_openrouter_key

_key_usage_cache: dict[str, Any] = {"ts": 0.0, "usage": 0.0}


async def check_budget_allows_llm() -> tuple[bool, str]:
    """Check OpenRouter credit usage (cached 60s) to protect the $5 eval reserve."""
    key = get_openrouter_key()
    if not key:
        return False, "none"
    now = time.time()
    if now - _key_usage_cache["ts"] > 60.0:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{OPENROUTER_BASE_URL}/key",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    _key_usage_cache["usage"] = float(data.get("usage") or 0.0)
                    _key_usage_cache["ts"] = now
        except Exception:
            _key_usage_cache["ts"] = now

    usage = _key_usage_cache["usage"]
    if usage > 44.0 and not (RUN_MODE == "eval" and OPENROUTER_EVAL_KEY):
        if RUN_MODE == "dev":
            return False, "none"
        return True, "fast"
    return True, "fast"


async def get_budget_status() -> dict[str, Any]:
    allow, tier = await check_budget_allows_llm()
    return {
        "allow_llm": allow,
        "force_fast": tier == "fast",
        "usage_usd": _key_usage_cache["usage"],
    }
