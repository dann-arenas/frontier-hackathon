"""Structured telemetry, execution trace logging (zero real PII), and incident tracking."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR, ROOT_DIR

TRACE_FILE: Path = LOGS_DIR / "execution_trace.jsonl"
INCIDENTS_FILE: Path = LOGS_DIR / "incidents_log.json"
FAILURE_ANALYSIS_FILE: Path = ROOT_DIR / "failure_analysis.md"


def log_step(
    stage: str,
    record: str = "ALL",
    mode: str = "DET",
    model: str = "NONE",
    latency_ms: float = 0.0,
    tokens: int = 0,
    cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    guard: str = "PASS",
    status: str = "PASS",
) -> None:
    """Append a structured [STEP] line to logs/execution_trace.jsonl without any PII."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    line_str = (
        f"[STEP] stage={stage} record={record} mode={mode} model={model} "
        f"latency_ms={int(round(latency_ms))} tokens={tokens} "
        f"cost_usd={cost_usd:.6f} guard={guard} status={status}"
    )
    payload = {
        "ts": ts,
        "line": line_str,
        "stage": stage,
        "record": record,
        "mode": mode,
        "model": model,
        "latency_ms": round(latency_ms, 2),
        "tokens": tokens,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 6),
        "guard": guard,
        "status": status,
    }
    try:
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def sum_llm_usage(since_iso: str) -> dict[str, int]:
    """Sum LLM tokens logged in the trace since `since_iso`, for `cost` in `GET /v1/run/{job_id}`."""
    totals = {"tokens_in": 0, "tokens_out": 0}
    if not TRACE_FILE.exists():
        return totals
    with open(TRACE_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("mode") == "LLM" and event.get("ts", "") >= since_iso:
                totals["tokens_in"] += int(event.get("tokens_in", 0))
                totals["tokens_out"] += int(event.get("tokens_out", 0))
    return totals


def log_sources_grown(prev_counts: dict[str, int], new_counts: dict[str, int]) -> None:
    """Record [SOURCES_GROWN] event in logs/incidents_log.json without real values."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "[SOURCES_GROWN]",
        "prev_counts": prev_counts,
        "new_counts": new_counts,
    }
    incidents: list[dict[str, Any]] = []
    if INCIDENTS_FILE.exists():
        try:
            incidents = json.loads(INCIDENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            incidents = []
    incidents.append(event)
    INCIDENTS_FILE.write_text(json.dumps(incidents, indent=2, ensure_ascii=False), encoding="utf-8")
    log_step(stage="fetch", record="record_index", mode="DET", status="PASS")


def record_failure_analysis(
    case_id: str,
    component: str,
    root_cause: str,
    mitigation: str,
) -> None:
    """Append a sanitized entry (no real PII) to failure_analysis.md."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"| `{case_id}` | `{ts}` | `{component}` | {root_cause} | {mitigation} |\n"
    try:
        if not FAILURE_ANALYSIS_FILE.exists():
            return
        current = FAILURE_ANALYSIS_FILE.read_text(encoding="utf-8")
        if f"| `{case_id}` |" not in current:
            with open(FAILURE_ANALYSIS_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass


class RunMetrics:
    """Accumulates token, cost, and timing metrics for a pipeline run."""

    def __init__(self) -> None:
        self.started_ts: float = time.time()
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.tokens_in: int = 0
        self.tokens_out: int = 0
        self.cost_usd: float = 0.0
        self.records: dict[str, int] = {"chats": 0, "docs": 0, "tables": 0}
        self.guard_intercepts: int = 0

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    @property
    def seconds(self) -> float:
        return round(time.time() - self.started_ts, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "records": self.records,
            "cost": {
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "seconds": self.seconds,
            },
        }
