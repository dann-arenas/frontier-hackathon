"""Parse Slack-format chat exports into normalized ChatRecord dictionaries."""
from __future__ import annotations

from typing import Any


def parse_chats(
    channel_days: dict[str, dict[str, list[dict[str, Any]]]],
    users: list[dict[str, Any]],
    record_index_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Convert raw Slack channel/day messages into normalized ChatRecord dicts.
    Preserves internal `_uid` and `_channel` fields for entity linking and resolution.
    """
    users_by_id: dict[str, dict[str, Any]] = {u["id"]: u for u in users if "id" in u}
    records_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []

    for channel_name in sorted(channel_days.keys()):
        days_map = channel_days[channel_name]
        # First pass across the channel to find root ts if thread_ts is omitted
        for date_str in sorted(days_map.keys()):
            msgs = days_map[date_str]
            current_root_ts: str | None = None
            for msg in msgs:
                msg_id = msg.get("client_msg_id") or msg.get("id")
                if not msg_id:
                    continue
                ts = str(msg.get("ts", ""))
                thread_ts = msg.get("thread_ts")
                if thread_ts:
                    root_ts = str(thread_ts)
                else:
                    root_ts = ts
                    current_root_ts = ts
                if not root_ts and current_root_ts:
                    root_ts = current_root_ts

                thread_id = f"{channel_name}:{root_ts}"
                uid = str(msg.get("user") or "")
                user_obj = users_by_id.get(uid, {})
                full_real_name = str(
                    msg.get("username")
                    or user_obj.get("real_name")
                    or user_obj.get("profile", {}).get("real_name")
                    or user_obj.get("name")
                    or uid
                )
                words = full_real_name.split()
                if len(words) >= 3 and not user_obj.get("is_bot"):
                    raw_author = " ".join(words[:-1])
                else:
                    raw_author = full_real_name

                files = msg.get("files") or []
                attachments: list[dict[str, str]] = []
                for f in files:
                    if isinstance(f, dict):
                        fn = f.get("name") or f.get("filename") or f.get("title") or ""
                        attachments.append({"filename": str(fn)})

                rec = {
                    "id": str(msg_id),
                    "thread_id": thread_id,
                    "author": str(raw_author),
                    "ts": ts,
                    "text": str(msg.get("text") or ""),
                    "attachments": attachments,
                    "_uid": uid,
                    "_full_real_name": full_real_name,
                    "_channel": channel_name,
                    "_is_bot": bool(
                        user_obj.get("is_bot")
                        or msg.get("subtype") == "bot_message"
                        or uid == "UKIPIBOT01"
                    ),
                }
                if str(msg_id) not in records_by_id:
                    ordered_ids.append(str(msg_id))
                records_by_id[str(msg_id)] = rec

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
