"""Async client for the Sources API with ETag caching, backoff, growth detection, and local directory loading."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import HTTP_CACHE_DIR, LAST_INDEX_PATH
from app.parse.chats import parse_chats
from app.parse.docs import parse_doc
from app.parse.tables import parse_all_tables
from app.telemetry import log_sources_grown, log_step


class CorpusBundle:
    """Normalized corpus loaded from either the remote Sources API or a local directory (like sample/input)."""

    def __init__(
        self,
        chats: list[dict[str, Any]],
        docs: list[dict[str, Any]],
        tables: list[dict[str, Any]],
        users: list[dict[str, Any]],
        channels: list[dict[str, Any]],
        record_index: dict[str, list[str]],
    ) -> None:
        self.chats = chats
        self.docs = docs
        self.tables = tables
        self.users = users
        self.channels = channels
        self.record_index = record_index


def _check_and_record_growth(new_index: dict[str, list[str]]) -> bool:
    new_counts = {k: len(v) for k, v in new_index.items() if isinstance(v, list)}
    grown = False
    if LAST_INDEX_PATH.exists():
        try:
            prev_index = json.loads(LAST_INDEX_PATH.read_text(encoding="utf-8"))
            prev_counts = {k: len(v) for k, v in prev_index.items() if isinstance(v, list)}
            if sum(new_counts.values()) > sum(prev_counts.values()):
                log_sources_grown(prev_counts, new_counts)
                grown = True
        except Exception:
            pass
    try:
        LAST_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_INDEX_PATH.write_text(json.dumps(new_index, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return grown


async def _fetch_with_etag(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
) -> bytes:
    HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    body_path = HTTP_CACHE_DIR / f"{url_hash}.bin"
    etag_path = HTTP_CACHE_DIR / f"{url_hash}.etag"

    headers: dict[str, str] = {}
    if etag_path.exists() and body_path.exists():
        headers["If-None-Match"] = etag_path.read_text(encoding="utf-8").strip()

    backoff = 0.5
    for attempt in range(4):
        async with sem:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 304 and body_path.exists():
                    return body_path.read_bytes()
                if resp.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(backoff * (2**attempt))
                    continue
                resp.raise_for_status()
                content = resp.content
                body_path.write_bytes(content)
                if "etag" in resp.headers:
                    etag_path.write_text(resp.headers["etag"], encoding="utf-8")
                return content
            except httpx.HTTPError:
                if attempt == 3:
                    if body_path.exists():
                        return body_path.read_bytes()
                    raise
                await asyncio.sleep(backoff * (2**attempt))
    raise RuntimeError(f"Failed to fetch {url}")


async def fetch_from_api(sources_url: str, token: str, save_public_dir: Path | None = None) -> CorpusBundle:
    """Download the entire corpus from the remote Sources API using ETag caching and concurrency control."""
    t0 = time.time()
    base = sources_url.rstrip("/")
    sem = asyncio.Semaphore(20)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        idx_bytes, channels_bytes, users_bytes, docs_idx_bytes, tables_idx_bytes = await asyncio.gather(
            _fetch_with_etag(client, f"{base}/record_index", sem),
            _fetch_with_etag(client, f"{base}/chats/channels", sem),
            _fetch_with_etag(client, f"{base}/chats/users", sem),
            _fetch_with_etag(client, f"{base}/docs", sem),
            _fetch_with_etag(client, f"{base}/tables", sem),
        )

        record_index: dict[str, list[str]] = json.loads(idx_bytes.decode("utf-8"))
        _check_and_record_growth(record_index)
        if save_public_dir is not None:
            save_public_dir.mkdir(parents=True, exist_ok=True)
            (save_public_dir / "record_index.json").write_bytes(idx_bytes)

        channels: list[dict[str, Any]] = json.loads(channels_bytes.decode("utf-8"))
        users: list[dict[str, Any]] = json.loads(users_bytes.decode("utf-8"))
        docs_index: list[dict[str, Any]] = json.loads(docs_idx_bytes.decode("utf-8"))
        tables_index: list[dict[str, Any]] = json.loads(tables_idx_bytes.decode("utf-8"))

        # Fetch all tables
        table_names = [t["name"] for t in tables_index if "name" in t]
        if not table_names:
            table_names = ["clientes.csv", "transacciones.csv", "pqrs.xml", "empleados.xml"]
        table_payloads = await asyncio.gather(
            *(_fetch_with_etag(client, f"{base}/tables/{name}", sem) for name in table_names)
        )
        raw_tables = dict(zip(table_names, table_payloads))

        # Fetch all docs
        doc_payloads = await asyncio.gather(
            *(_fetch_with_etag(client, f"{base}/docs/{d['id']}", sem) for d in docs_index)
        )
        docs_by_id: dict[str, dict[str, Any]] = {}
        for d_meta, d_bytes in zip(docs_index, doc_payloads):
            parsed = parse_doc(d_meta, d_bytes)
            docs_by_id[parsed["id"]] = parsed

        # Fetch channel dates and messages
        channel_names = [c["name"] for c in channels if "name" in c]
        date_lists_bytes = await asyncio.gather(
            *(_fetch_with_etag(client, f"{base}/chats/{ch}", sem) for ch in channel_names)
        )
        channel_dates: dict[str, list[str]] = {
            ch: json.loads(db.decode("utf-8")) for ch, db in zip(channel_names, date_lists_bytes)
        }

        day_tasks = []
        day_keys: list[tuple[str, str]] = []
        for ch, dates in channel_dates.items():
            for dt in dates:
                day_keys.append((ch, dt))
                day_tasks.append(_fetch_with_etag(client, f"{base}/chats/{ch}/{dt}", sem))

        day_payloads = await asyncio.gather(*day_tasks)
        channel_days: dict[str, dict[str, list[dict[str, Any]]]] = {ch: {} for ch in channel_names}
        for (ch, dt), payload in zip(day_keys, day_payloads):
            channel_days[ch][dt] = json.loads(payload.decode("utf-8"))

    chats = parse_chats(channel_days, users, record_index.get("chats"))
    docs_order = record_index.get("docs") or [d["id"] for d in docs_index]
    docs = [docs_by_id[did] for did in docs_order if did in docs_by_id]
    tables = parse_all_tables(raw_tables, record_index.get("tables"))

    log_step(
        stage="fetch",
        record="ALL",
        mode="DET",
        latency_ms=(time.time() - t0) * 1000.0,
        status="PASS",
    )
    return CorpusBundle(
        chats=chats,
        docs=docs,
        tables=tables,
        users=users,
        channels=channels,
        record_index=record_index,
    )


def load_from_local_dir(input_dir: Path, record_index_path: Path | None = None) -> CorpusBundle:
    """Load a corpus from a local directory (such as `sample/input` or `fuentes`)."""
    t0 = time.time()
    if record_index_path is None:
        cand = input_dir / "record_index.json"
        if not cand.exists():
            cand = input_dir.parent / "record_index.json"
        record_index_path = cand

    record_index: dict[str, list[str]] = (
        json.loads(record_index_path.read_text(encoding="utf-8"))
        if record_index_path and record_index_path.exists()
        else {"chats": [], "docs": [], "tables": []}
    )

    chats_dir = input_dir / "chats"
    channels_file = chats_dir / "channels.json"
    users_file = chats_dir / "users.json"
    channels = json.loads(channels_file.read_text(encoding="utf-8")) if channels_file.exists() else []
    users = json.loads(users_file.read_text(encoding="utf-8")) if users_file.exists() else []

    channel_days: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if chats_dir.exists():
        for ch_dir in sorted(chats_dir.iterdir()):
            if ch_dir.is_dir():
                ch_name = ch_dir.name
                channel_days[ch_name] = {}
                for day_file in sorted(ch_dir.glob("*.json")):
                    if day_file.name == "dates.json":
                        continue
                    dt = day_file.stem
                    channel_days[ch_name][dt] = json.loads(day_file.read_text(encoding="utf-8"))

    docs_dir = input_dir / "docs"
    docs_index_file = docs_dir / "index.json"
    docs_index = json.loads(docs_index_file.read_text(encoding="utf-8")) if docs_index_file.exists() else []
    docs_by_id: dict[str, dict[str, Any]] = {}
    for d_meta in docs_index:
        fn_path = docs_dir / d_meta["filename"]
        if fn_path.exists():
            parsed = parse_doc(d_meta, fn_path.read_bytes())
            docs_by_id[parsed["id"]] = parsed

    tables_dir = input_dir / "tables"
    raw_tables: dict[str, bytes] = {}
    if tables_dir.exists():
        for tname in ("clientes.csv", "transacciones.csv", "pqrs.xml", "empleados.xml"):
            tpath = tables_dir / tname
            if tpath.exists():
                raw_tables[tname] = tpath.read_bytes()

    chats = parse_chats(channel_days, users, record_index.get("chats"))
    docs_order = record_index.get("docs") or [d["id"] for d in docs_index]
    docs = [docs_by_id[did] for did in docs_order if did in docs_by_id]
    tables = parse_all_tables(raw_tables, record_index.get("tables"))

    log_step(
        stage="parse",
        record="ALL",
        mode="DET",
        latency_ms=(time.time() - t0) * 1000.0,
        status="PASS",
    )
    return CorpusBundle(
        chats=chats,
        docs=docs,
        tables=tables,
        users=users,
        channels=channels,
        record_index=record_index,
    )
