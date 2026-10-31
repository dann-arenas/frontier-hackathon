"""Export `entities.json` from the canonical registry, pruning unused pseudonyms and originals."""
from __future__ import annotations

from typing import Any

from app.normalize import contiene, es_nombre_suelto, texto_de_registro
from app.registry import EntityRegistry

PSEUDONYM_EXPORT_KEYS = (
    "email",
    "phone",
    "cedula",
    "handle",
    "address",
    "birth_iso",
    "plate",
    "account",
    "passport",
)


def _is_kept_original(orig: str, kept_originals: list[str]) -> bool:
    return any(contiene(k, orig) for k in kept_originals)


def export_entities(
    reg: EntityRegistry,
    records: list[dict[str, Any]],
    for_anonymize: bool = False,
) -> list[dict[str, Any]]:
    """
    Build the `entities` list for `entities.json` or `POST /v1/anonymize`.
    Guarantees:
    - Every exported `person` has `records` non-empty and `pseudonyms` pruned strictly
      to attributes that appear (`contiene()`) in at least one of its `records`.
    - Every exported `originals` item is free of residual appearances in the output.
    """
    record_text_by_id: dict[str, str] = {
        str(r["id"]): texto_de_registro(r) for r in records if isinstance(r, dict) and "id" in r
    }
    entero = "\n".join(record_text_by_id.values())

    # Collect kept entities that appeared in `records`
    kept_out: list[dict[str, Any]] = []
    kept_originals_flat: list[str] = []
    for k in sorted(reg.kept.values(), key=lambda x: x.entity_id):
        has_rec = any(rid in record_text_by_id for rid in k.records_found)
        if not has_rec:
            continue
        origs_k = sorted(s for s in k.originals_found if s) or [k.name]
        for ok in origs_k:
            if len(ok) >= 4 and any(c.isalpha() for c in ok):
                kept_originals_flat.append(ok)
        kept_out.append(
            {
                "entity_id": k.entity_id,
                "type": "organization_or_place",
                "originals": origs_k,
                "pseudonyms": {},
                "kept": True,
                "reason": k.reason,
                "records": [],
            }
        )

    persons_out: list[dict[str, Any]] = []
    for p in sorted(reg.persons.values(), key=lambda x: x.entity_id):
        recs = sorted(rid for rid in p.records_found if rid in record_text_by_id)
        if not recs:
            continue

        ps = p.pseudonyms
        short_name = ps.get("short_name") or ps.get("name") or ""
        name_val = short_name

        if not for_anonymize and name_val:
            if not any(contiene(record_text_by_id[rid], name_val) for rid in recs):
                # Fallback if person only appeared by given_name or surname1 in recs
                for alt_key in ("full_name", "given_name", "surname1"):
                    alt_val = ps.get(alt_key, "")
                    if alt_val and any(contiene(record_text_by_id[rid], alt_val) for rid in recs):
                        name_val = alt_val
                        break

        pseuds_clean: dict[str, str] = {"name": name_val}
        for key in PSEUDONYM_EXPORT_KEYS:
            val = ps.get(key, "")
            if not val:
                continue
            if key == "handle" and "handle" not in p.attrs_used:
                continue
            if any(contiene(record_text_by_id[rid], val) for rid in recs):
                pseuds_clean[key] = val

        # Filter originals_found
        origs_clean: list[str] = []
        for orig in sorted(p.originals_found):
            if not orig or len(orig) < 2:
                continue
            if len(orig) == 4 and orig.isdigit():
                continue
            if len(orig) >= 3 and not _is_kept_original(orig, kept_originals_flat) and not es_nombre_suelto(orig):
                if contiene(entero, orig):
                    # Do not declare an original that still collides with a kept/common substring in entero
                    continue
            origs_clean.append(orig)

        if not origs_clean and (p.short_name or p.full_name):
            fallback_o = p.short_name or p.full_name
            if not contiene(entero, fallback_o):
                origs_clean.append(fallback_o)

        persons_out.append(
            {
                "entity_id": p.entity_id,
                "type": "person",
                "originals": origs_clean,
                "pseudonyms": pseuds_clean,
                "records": recs,
            }
        )

    return persons_out + kept_out
