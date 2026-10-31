"""Optional OpenRouter LLM span extractor with content-hash caching and budget guard."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import (
    LLM_CACHE_DIR,
    LLM_CONCURRENCY_INITIAL,
    LLM_MAX_CALLS_PER_RUN,
    LLM_TIMEOUT_SECONDS,
    MODELS_FAST,
    MODELS_HEAVY,
    OPENROUTER_BASE_URL,
    RUN_MODE,
    get_active_openrouter_key,
    get_budget_status,
)
from app.telemetry import log_step

SYSTEM_PROMPT = """Eres un detector quirúrgico de PII para un corpus financiero colombiano (Kipu Pay).
Extrae ÚNICAMENTE menciones de personas naturales (clientes, empleados, terceros, familiares, abogados, auditores) o sus datos personales (nombre, apodo, correo, teléfono, cédula, dirección, placa, pasaporte) que aparezcan textualmente en el texto.
NO extraigas empresas, bancos (Bancolombia, Nequi, Daviplata, Santander, SURA), lugares, barrios, ciudades ni figuras públicas (Shakira, Gabriel García Márquez, Jorge Isaacs, Egan Bernal, Nairo Quintana) ni el bot Kipi.
Devuelve SIEMPRE un objeto JSON con la llave "spans", donde cada elemento tiene:
{"surface": "<subcadena exacta del texto>", "type": "person|email|phone|cedula|address|plate|passport", "person_hint": "<rol o pista>", "gender": "F|M"}"""


_TOKENS_IN_ACC = 0
_TOKENS_OUT_ACC = 0
_COST_USD_ACC = 0.0
_NETWORK_CALLS_IN_RUN = 0


def get_and_reset_llm_tokens() -> tuple[int, int]:
    global _TOKENS_IN_ACC, _TOKENS_OUT_ACC, _NETWORK_CALLS_IN_RUN
    t_in, t_out = _TOKENS_IN_ACC, _TOKENS_OUT_ACC
    _TOKENS_IN_ACC = 0
    _TOKENS_OUT_ACC = 0
    _NETWORK_CALLS_IN_RUN = 0
    return t_in, t_out


def get_and_reset_llm_cost() -> float:
    global _COST_USD_ACC
    cost_usd = _COST_USD_ACC
    _COST_USD_ACC = 0.0
    return cost_usd


def _cache_path(text: str, tier: str) -> Path:
    h = hashlib.sha256(f"{tier}:{text}".encode("utf-8")).hexdigest()
    return LLM_CACHE_DIR / f"{h}.json"


def get_cached_llm_spans(text: str, tier: str = "fast") -> list[dict[str, str]] | None:
    p = _cache_path(text, tier)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("spans"), list):
                return data["spans"]
        except Exception:
            return None
    return None


def save_cached_llm_spans(text: str, tier: str, spans: list[dict[str, str]]) -> None:
    p = _cache_path(text, tier)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"spans": spans}, ensure_ascii=False), encoding="utf-8")


async def extract_spans_llm(
    record_id: str,
    text: str,
    tier: str = "fast",
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict[str, str]]:
    """
    Call OpenRouter to extract typed spans `{surface, type, person_hint, gender}`
    with caching in `STATE_DIR/llm_cache/` and budget protection.
    """
    if not text or not text.strip():
        return []

    global _NETWORK_CALLS_IN_RUN
    cached = get_cached_llm_spans(text, tier)
    if _NETWORK_CALLS_IN_RUN >= LLM_MAX_CALLS_PER_RUN:
        return cached if cached is not None else []

    api_key = get_active_openrouter_key()
    if not api_key:
        return cached if cached is not None else []

    budget = await get_budget_status()
    if not budget.get("allow_llm", True):
        return cached if cached is not None else []
    if budget.get("force_fast", False):
        tier = "fast"

    _NETWORK_CALLS_IN_RUN += 1
    models = list(dict.fromkeys(MODELS_HEAVY if tier == "heavy" else MODELS_FAST))
    sem = semaphore or asyncio.Semaphore(LLM_CONCURRENCY_INITIAL)
    t0 = time.time()

    payload = {
        "models": models,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with sem:
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
                    resp = await client.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1.0 * (2**attempt))
                    continue
                resp.raise_for_status()
                body = resp.json()
                model_used = body.get("model", models[0])
                usage = body.get("usage", {}) or {}
                tokens_in = int(usage.get("prompt_tokens", 0))
                tokens_out = int(usage.get("completion_tokens", 0))
                cost_usd = float(usage.get("cost", 0.0) or 0.0)
                global _TOKENS_IN_ACC, _TOKENS_OUT_ACC, _COST_USD_ACC
                _TOKENS_IN_ACC += tokens_in
                _TOKENS_OUT_ACC += tokens_out
                _COST_USD_ACC += cost_usd
                content = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "{}")
                )
                parsed = json.loads(content)
                spans = parsed.get("spans", []) if isinstance(parsed, dict) else []
                valid_spans = [
                    s
                    for s in spans
                    if isinstance(s, dict)
                    and isinstance(s.get("surface"), str)
                    and s["surface"] in text
                ]
                save_cached_llm_spans(text, tier, valid_spans)
                log_step(
                    stage="detect",
                    record=record_id,
                    mode="LLM",
                    model=model_used,
                    latency_ms=(time.time() - t0) * 1000.0,
                    tokens=tokens_in + tokens_out,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                    status="PASS",
                )
                return valid_spans
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                log_step(
                    stage="detect",
                    record=record_id,
                    mode="LLM",
                    model=models[0],
                    latency_ms=(time.time() - t0) * 1000.0,
                    status="DEGRADED",
                )
                return []
    return []
