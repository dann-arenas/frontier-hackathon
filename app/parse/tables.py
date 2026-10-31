"""Parse CSV (Latin-1/UTF-8) and XML tables into normalized TableRecord dictionaries."""
from __future__ import annotations

import csv
import io
from typing import Any

from lxml import etree


def parse_clientes_csv(raw_bytes: bytes) -> list[dict[str, Any]]:
    text = raw_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    records: list[dict[str, Any]] = []
    for row in reader:
        cid = str(row.get("cliente_id", "")).strip()
        if not cid:
            continue
        cols = {str(k): str(v if v is not None else "") for k, v in row.items() if k is not None}
        records.append(
            {
                "id": f"clientes:{cid}",
                "table": "clientes",
                "columns": cols,
            }
        )
    return records


def parse_transacciones_csv(raw_bytes: bytes) -> list[dict[str, Any]]:
    text = raw_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=",")
    records: list[dict[str, Any]] = []
    for row in reader:
        tx_id = str(row.get("tx_id", "")).strip()
        if not tx_id:
            continue
        cols = {str(k): str(v if v is not None else "") for k, v in row.items() if k is not None}
        records.append(
            {
                "id": f"transacciones:{tx_id}",
                "table": "transacciones",
                "columns": cols,
            }
        )
    return records


def parse_pqrs_xml(raw_bytes: bytes) -> list[dict[str, Any]]:
    root = etree.fromstring(raw_bytes)
    records: list[dict[str, Any]] = []
    for el in root:
        if not isinstance(el.tag, str) or el.tag != "pqr":
            continue
        radicado = str(el.attrib.get("radicado", "")).strip()
        if not radicado:
            continue
        cols: dict[str, str] = {str(k): str(v) for k, v in el.attrib.items()}
        for child in el:
            if isinstance(child.tag, str):
                cols[child.tag] = (child.text or "").strip()
        records.append(
            {
                "id": f"pqrs:{radicado}",
                "table": "pqrs",
                "columns": cols,
            }
        )
    return records


def parse_empleados_xml(raw_bytes: bytes) -> list[dict[str, Any]]:
    root = etree.fromstring(raw_bytes)
    records: list[dict[str, Any]] = []
    for el in root:
        if not isinstance(el.tag, str) or el.tag != "empleado":
            continue
        emp_id = str(el.attrib.get("id", "")).strip()
        if not emp_id:
            continue
        cols: dict[str, str] = {str(k): str(v) for k, v in el.attrib.items()}
        for child in el:
            if isinstance(child.tag, str):
                cols[child.tag] = (child.text or "").strip()
        records.append(
            {
                "id": f"empleados:{emp_id}",
                "table": "empleados",
                "columns": cols,
            }
        )
    return records


def parse_all_tables(
    raw_tables: dict[str, bytes],
    record_index_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []

    parsers = [
        ("clientes.csv", parse_clientes_csv),
        ("transacciones.csv", parse_transacciones_csv),
        ("pqrs.xml", parse_pqrs_xml),
        ("empleados.xml", parse_empleados_xml),
    ]
    for name, fn in parsers:
        if name in raw_tables and raw_tables[name]:
            for r in fn(raw_tables[name]):
                rid = r["id"]
                if rid not in records_by_id:
                    ordered_ids.append(rid)
                records_by_id[rid] = r

    if record_index_order:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rid in record_index_order:
            if rid in records_by_id:
                result.append(records_by_id[rid])
                seen.add(rid)
        for rid in ordered_ids:
            if rid not in seen:
                result.append(records_by_id[rid])
        return result

    return [records_by_id[rid] for rid in ordered_ids]
