"""Dual-watermark batch planning and projection application.

Each projection stream carries two high-watermarks:

* ``watermark_seq``        — highest seq among successfully applied events.
* ``watermark_event_time`` — highest event_time among successfully applied events.

A successful strict (default) batch must advance BOTH ends at once. When only
one end can legitimately advance, the whole batch is rejected: neither the
projection nor either watermark moves.

Opt-in ``catch_up`` mode commits a seq-only advance, holds the event_time end
at its previous value, and marks the batch as catch-up. Both modes refuse to
apply any event whose seq or event_time sits behind the previous watermark,
because such an event would roll back one end of the projection frontier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RESULT_APPLIED_UPSERT = "applied_upsert"
RESULT_APPLIED_DELETE = "applied_delete"
RESULT_DUPLICATE = "skipped_duplicate"
RESULT_STALE = "skipped_stale"
RESULT_REJECTED = "rejected_no_commit"


def norm_time(value: Any) -> str:
    """Normalize an ISO-8601 timestamp to ``YYYY-MM-DDTHH:MM:SSZ``."""
    if value in (None, ""):
        return ""
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid event_time: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _relation(candidate: int | str, watermark: int | str) -> str:
    if not watermark:
        return "advance"
    if candidate > watermark:
        return "advance"
    if candidate < watermark:
        return "rollback"
    return "flat"


def _relation_text(name: str, relation: str, candidate: Any, watermark: Any) -> str:
    wm = watermark or "(空)"
    shown = candidate if candidate not in (None, "") else "(空)"
    if relation == "advance":
        return f"{name} 可前进 {wm}→{shown}"
    if relation == "flat":
        return f"{name} 未前进（候选 {shown} = 水位 {wm}）"
    return f"{name} 回拨（候选 {shown} < 水位 {wm}）"


def _finish_counts(counts: dict[str, Any], projection: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = dict(counts)
    out["projected_alive"] = sum(1 for r in projection.values() if not r["deleted"])
    out["projected_deleted"] = sum(1 for r in projection.values() if r["deleted"])
    return out


def apply_batch(
    *,
    projection: list[dict[str, Any]],
    events: list[dict[str, Any]],
    wm_seq: int,
    wm_time: str,
    mode: str,
    known_event_ids: set[str] | None = None,
    updated_at: str,
) -> dict[str, Any]:
    """Plan one batch and, unless rejected, apply it to a copy of the projection."""
    if mode not in ("strict", "catch_up"):
        raise ValueError(f"unknown commit mode: {mode}")

    known = set(known_event_ids or ())
    current: dict[str, dict[str, Any]] = {row["entity_key"]: dict(row) for row in projection}

    ordered = sorted(
        events, key=lambda e: (int(e.get("seq", 0)), str(e.get("event_id", "")))
    )
    duplicates = [e for e in ordered if str(e["event_id"]) in known]
    live = [e for e in ordered if str(e["event_id"]) not in known]
    seq_ahead = [e for e in live if int(e.get("seq", 0)) > wm_seq]
    seq_behind = [e for e in live if int(e.get("seq", 0)) <= wm_seq]
    rollback_ids = {
        str(e["event_id"])
        for e in seq_ahead
        if wm_time and norm_time(e.get("event_time")) < wm_time
    }
    time_rollback = [e for e in seq_ahead if str(e["event_id"]) in rollback_ids]
    advanceable = [e for e in seq_ahead if str(e["event_id"]) not in rollback_ids]

    counts: dict[str, Any] = {
        "source_events": len(events),
        "applied_upsert": 0,
        "applied_delete": 0,
        "skipped_duplicate": len(duplicates),
        "skipped_stale": 0,
        "max_seq": 0,
        "max_event_time": "",
    }
    log: list[dict[str, Any]] = []

    def log_event(ev: dict[str, Any], result: str, detail: str = "") -> None:
        log.append(
            {
                "event_id": str(ev["event_id"]),
                "entity_key": str(ev["entity_key"]),
                "op": str(ev.get("op", "upsert")),
                "seq": int(ev.get("seq", 0)),
                "event_time": norm_time(ev.get("event_time")),
                "result": result,
                "detail": detail,
                "payload": ev.get("payload"),
            }
        )

    for ev in duplicates:
        log_event(ev, RESULT_DUPLICATE, "event_id 已在前批成功应用")

    # What this batch saw: candidates over every not-yet-applied event.
    seen = live or duplicates
    seq_candidate = max((int(e.get("seq", 0)) for e in seen), default=wm_seq)
    time_candidate = max((norm_time(e.get("event_time")) for e in seen), default=wm_time)
    seq_rel = _relation(seq_candidate, wm_seq)
    time_rel = _relation(time_candidate, wm_time)
    reason_parts = [
        _relation_text("seq", seq_rel, seq_candidate, wm_seq),
        _relation_text("event_time", time_rel, time_candidate, wm_time),
    ]

    wm_seq_after, wm_time_after = wm_seq, wm_time
    if not live:
        decision, advance_mode = "reject", "none"
        reason_code = "no_pending_events"
        reason_text = "本波没有未应用的事件，整趟拒绝：投影与双水位均不动。"
    elif not seq_ahead:
        decision, advance_mode = "reject", "none"
        reason_code = "time_only_prohibited" if time_rel == "advance" else "seq_rollback_prohibited"
        reason_text = (
            "只有 event_time 端可前进、seq 端未前进或回拨：应用会对回拨 seq 端，"
            + "；".join(reason_parts)
            + "。投影与双水位均不动。"
        )
    elif time_rollback:
        decision, advance_mode = "reject", "none"
        reason_code = "late_event_prohibited"
        reason_text = (
            "seq 端可前进，但其中 "
            f"{len(time_rollback)} 个事件的 event_time 早于时间水位"
            f"（如 {time_rollback[0]['event_id']}），应用会回拨 event_time 端；"
            + "；".join(reason_parts)
            + "。投影与双水位均不动。"
        )
    else:
        applied_seq = max(int(e.get("seq", 0)) for e in advanceable)
        applied_time = max(norm_time(e.get("event_time")) for e in advanceable)
        applied_seq_rel = _relation(applied_seq, wm_seq)
        applied_time_rel = _relation(applied_time, wm_time)
        if applied_time_rel == "advance":
            decision, advance_mode = "commit", "both"
            reason_code = "both_advance"
            reason_text = (
                f"双端同进：seq {wm_seq}→{applied_seq}，"
                f"event_time {wm_time or '(空)'}→{applied_time}。"
            )
            wm_seq_after, wm_time_after = applied_seq, applied_time
        elif mode == "strict":
            decision, advance_mode = "reject", "none"
            reason_code = "strict_requires_both"
            reason_text = (
                f"默认整趟拒绝（严格双端）：seq 可前进 {wm_seq}→{applied_seq}，"
                f"event_time 未前进（保持 {wm_time or '(空)'}）；"
                "投影与双水位均不动。"
            )
        else:
            decision, advance_mode = "catch_up", "seq_only"
            reason_code = "seq_only_catch_up"
            reason_text = (
                f"追赶模式：仅推进 seq {wm_seq}→{applied_seq}；"
                f"event_time 端保持 {wm_time or '(空)'} 不变。"
            )
            wm_seq_after = applied_seq

    stale_detail = (
        "seq 不前进，未参与本批推进判定"
        if decision == "reject"
        else "seq 不前进，禁止应用（会回拨 seq 端）"
    )

    if decision == "reject":
        for ev in seq_behind:
            counts["skipped_stale"] += 1
            log_event(ev, RESULT_STALE, stale_detail)
        for ev in live:
            if ev in seq_behind:
                continue
            log_event(ev, RESULT_REJECTED, reason_code)
        counts["max_seq"] = seq_candidate
        counts["max_event_time"] = time_candidate
        return {
            "decision": decision,
            "advance_mode": advance_mode,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "seq_candidate": seq_candidate,
            "time_candidate": time_candidate,
            "wm_seq_before": wm_seq,
            "wm_time_before": wm_time,
            "wm_seq_after": wm_seq,
            "wm_time_after": wm_time,
            "counts": _finish_counts(counts, current),
            "projection": sorted(current.values(), key=lambda r: r["entity_key"]),
            "applied_log": log,
        }

    for ev in seq_behind:
        counts["skipped_stale"] += 1
        log_event(ev, RESULT_STALE, stale_detail)

    for ev in advanceable:
        seq = int(ev.get("seq", 0))
        entity_key = str(ev["entity_key"])
        op = str(ev.get("op", "upsert"))
        if op == "delete":
            current[entity_key] = {
                "entity_key": entity_key,
                "payload": None,
                "applied_seq": seq,
                "applied_event_id": str(ev["event_id"]),
                "deleted": True,
                "updated_at": updated_at,
            }
            counts["applied_delete"] += 1
            log_event(ev, RESULT_APPLIED_DELETE)
        else:
            current[entity_key] = {
                "entity_key": entity_key,
                "payload": ev.get("payload") or {},
                "applied_seq": seq,
                "applied_event_id": str(ev["event_id"]),
                "deleted": False,
                "updated_at": updated_at,
            }
            counts["applied_upsert"] += 1
            log_event(ev, RESULT_APPLIED_UPSERT)

    counts["max_seq"] = seq_candidate
    counts["max_event_time"] = time_candidate
    return {
        "decision": decision,
        "advance_mode": advance_mode,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "seq_candidate": seq_candidate,
        "time_candidate": time_candidate,
        "wm_seq_before": wm_seq,
        "wm_time_before": wm_time,
        "wm_seq_after": wm_seq_after,
        "wm_time_after": wm_time_after,
        "counts": _finish_counts(counts, current),
        "projection": sorted(current.values(), key=lambda r: r["entity_key"]),
        "applied_log": log,
    }
