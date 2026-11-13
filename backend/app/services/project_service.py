from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.project import apply_batch
from app.repositories import batches as batch_repo
from app.repositories import projections as proj_repo
from app.repositories import streams as stream_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    return data


def waves_for_fixture(events: list[dict[str, Any]]) -> list[int]:
    return sorted({int(e.get("wave", 1)) for e in events})


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        return {
            "stream_count": len(streams),
            "enabled_streams": sum(1 for s in streams if s["enabled"]),
            "batch_count": conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"],
            "committed_batches": conn.execute(
                "SELECT COUNT(*) c FROM project_batches WHERE decision IN ('commit','catch_up')"
            ).fetchone()["c"],
            "rejected_batches": conn.execute(
                "SELECT COUNT(*) c FROM project_batches WHERE decision='reject'"
            ).fetchone()["c"],
            "catch_up_batches": conn.execute(
                "SELECT COUNT(*) c FROM project_batches WHERE decision='catch_up'"
            ).fetchone()["c"],
            "projection_alive": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=0"
            ).fetchone()["c"],
            "projection_deleted": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=1"
            ).fetchone()["c"],
            "ooo_buffered": conn.execute("SELECT COUNT(*) c FROM ooo_buffer").fetchone()["c"],
            "apply_order": conn.execute(
                "SELECT value FROM settings WHERE key='apply_order'"
            ).fetchone()["value"],
            "commit_mode_default": conn.execute(
                "SELECT value FROM settings WHERE key='commit_mode_default'"
            ).fetchone()["value"],
            "dual_watermark_streams": [
                {
                    "stream_id": s["id"],
                    "code": s["code"],
                    "title": s["title"],
                    "watermark_seq": s["watermark_seq"],
                    "watermark_event_time": s["watermark_event_time"],
                    "watermark_advanced": s["watermark_event_time"] != "" or s["watermark_seq"] > 0,
                }
                for s in streams
            ],
        }
    finally:
        conn.close()


def list_streams() -> list[dict[str, Any]]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        for s in streams:
            s["waves"] = waves_for_fixture(_load_fixture(s["fixture"]))
            s["evidence"] = proj_repo.applied_watermark_evidence(conn, s["id"])
        return streams
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = batch_repo.list_batches(conn, limit=limit)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
            r["result_counts"] = batch_repo.batch_result_counts(conn, r["id"])
        return rows
    finally:
        conn.close()


def list_projections(stream_id: int | None = None, include_deleted: bool = True) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_projections(
            conn, stream_id=stream_id, include_deleted=include_deleted
        )
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_events(batch_id: int | None = None, stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_applied_events(conn, batch_id=batch_id, stream_id=stream_id)
    finally:
        conn.close()


def list_ooo_buffer(stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return proj_repo.list_ooo_buffer(conn, stream_id=stream_id)
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def _reconcile(batch: dict[str, Any], fingerprint: str,
               evidence: dict[str, Any]) -> dict[str, Any]:
    """Cross-check the batch against the stored projection and the dual watermarks."""
    counts = batch["counts"]
    checks = [
        {
            "name": "双水位同成同败",
            "ok": (batch["decision"] == "reject" and batch["wm_seq_after"] == batch["wm_seq_before"]
                   and batch["wm_time_after"] == batch["wm_time_before"])
            or (batch["decision"] != "reject" and batch["watermark_match"]),
            "detail": (
                f"seq {batch['wm_seq_before']}→{batch['wm_seq_after']}；"
                f"event_time {batch['wm_time_before'] or '(空)'}→{batch['wm_time_after'] or '(空)'}"
            ),
        },
        {
            "name": "双水位与已应用事件互证",
            "ok": evidence["max_applied_seq"] == batch["wm_seq_after"]
            and evidence["max_applied_event_time"] == batch["wm_time_after"],
            "detail": (
                f"已应用事件 max seq={evidence['max_applied_seq']}，"
                f"max event_time={evidence['max_applied_event_time'] or '(空)'}；"
                f"水位 seq={batch['wm_seq_after']}，event_time={batch['wm_time_after'] or '(空)'}"
            ),
        },
        {
            "name": "批次计数与快照互证",
            "ok": counts["projected_alive"] == batch["snapshot_alive"]
            and counts["projected_deleted"] == batch["snapshot_deleted"],
            "detail": (
                f"批次记录 存活/已删={counts['projected_alive']}/{counts['projected_deleted']}；"
                f"快照实测={batch['snapshot_alive']}/{batch['snapshot_deleted']}"
            ),
        },
        {
            "name": "拒绝批不动投影",
            "ok": batch["decision"] != "reject" or (fingerprint == batch["projection_fingerprint"]
                                                    and fingerprint == batch["prev_fingerprint"]),
            "detail": (
                f"拒绝批指纹={batch['projection_fingerprint'][:12]}…，"
                f"上一成功批指纹={batch['prev_fingerprint'][:12]}…，当前快照指纹={fingerprint[:12]}…"
                if batch["decision"] == "reject"
                else "本批为提交批，投影指纹按提交后新值记录。"
            ),
        },
    ]
    return {
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "fingerprint": fingerprint,
    }


def run_project(stream_id: int, *, mode: str | None = None, wave: int | None = None,
                note: str | None = None) -> dict[str, Any]:
    """Run one batch over a fixture wave under the dual-watermark commit rules."""
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        if not stream["enabled"]:
            raise ValueError("stream disabled")

        effective_mode = mode or "strict"
        if effective_mode not in ("strict", "catch_up"):
            raise ValueError(f"unknown mode: {effective_mode}")

        all_events = _load_fixture(stream["fixture"])
        waves = waves_for_fixture(all_events)
        if wave is None:
            wave = 1
        events = [e for e in all_events if int(e.get("wave", 1)) == int(wave)]
        if not events:
            raise ValueError(f"wave {wave} has no events")

        current_rows = proj_repo.get_stream_projection(conn, stream_id)
        known = proj_repo.list_known_event_ids(conn, stream_id)
        now = _now()
        prev_fingerprint = proj_repo.projection_fingerprint(current_rows)

        result = apply_batch(
            projection=current_rows,
            events=events,
            wm_seq=stream["watermark_seq"],
            wm_time=stream["watermark_event_time"],
            mode=effective_mode,
            known_event_ids=known,
            updated_at=now,
        )

        # One transaction for projection rows and both watermarks together.
        # A rejected batch writes no projection rows and moves neither watermark;
        # both ends always move in a single UPDATE so they cannot tear apart.
        if result["decision"] != "reject":
            proj_repo.replace_stream_projection(
                conn, stream_id=stream_id, rows=result["projection"]
            )
            stream_repo.set_watermarks(
                conn,
                stream_id,
                watermark_seq=result["wm_seq_after"],
                watermark_event_time=result["wm_time_after"],
            )

        batch_projection = proj_repo.get_stream_projection(conn, stream_id)
        fingerprint = proj_repo.projection_fingerprint(batch_projection)
        snap_alive = sum(1 for r in batch_projection if not r["deleted"])
        snap_deleted = sum(1 for r in batch_projection if r["deleted"])
        refreshed = stream_repo.get_stream(conn, stream_id)
        assert refreshed is not None

        batch_no = batch_repo.next_batch_no(conn, date_tag=now[:10].replace("-", ""))
        counts = dict(result["counts"])
        counts["projected_alive"] = snap_alive
        counts["projected_deleted"] = snap_deleted
        fields = {
            "batch_no": batch_no,
            "stream_id": stream_id,
            "status": "completed" if result["decision"] != "reject" else "rejected",
            "source_events": counts["source_events"],
            "applied_upsert": counts["applied_upsert"],
            "applied_delete": counts["applied_delete"],
            "skipped_duplicate": counts["skipped_duplicate"],
            "skipped_stale": counts["skipped_stale"],
            "projected_alive": snap_alive,
            "projected_deleted": snap_deleted,
            "max_seq": counts["max_seq"],
            "max_event_time": counts["max_event_time"],
            "mode": effective_mode,
            "decision": result["decision"],
            "advance_mode": result["advance_mode"],
            "reason_code": result["reason_code"],
            "reason_text": result["reason_text"],
            "wave": int(wave),
            "wm_seq_before": result["wm_seq_before"],
            "wm_seq_after": result["wm_seq_after"],
            "wm_time_before": result["wm_time_before"],
            "wm_time_after": result["wm_time_after"],
            "projection_fingerprint": fingerprint,
            "prev_fingerprint": prev_fingerprint,
            "note": note,
            "created_at": now,
        }
        batch_id = batch_repo.insert_batch(conn, fields=fields)
        for ev in result["applied_log"]:
            batch_repo.insert_applied_event(
                conn, batch_id=batch_id, stream_id=stream_id, event=ev, created_at=now
            )
        conn.commit()

        # Evidence is read after this batch's event rows are written so the
        # watermark/count cross-check covers the just-committed batch too.
        evidence = proj_repo.applied_watermark_evidence(conn, stream_id)
        batch = batch_repo.get_batch(conn, batch_id)
        assert batch is not None
        batch["stream_code"] = stream["code"]
        batch["stream_title"] = stream["title"]
        batch["counts"] = counts
        batch["result_counts"] = batch_repo.batch_result_counts(conn, batch_id)
        batch["snapshot_alive"] = snap_alive
        batch["snapshot_deleted"] = snap_deleted
        batch["watermark_match"] = (
            refreshed["watermark_seq"] == result["wm_seq_after"]
            and refreshed["watermark_event_time"] == result["wm_time_after"]
        )
        batch["stream_watermarks"] = {
            "stream_id": stream_id,
            "seq": refreshed["watermark_seq"],
            "event_time": refreshed["watermark_event_time"],
        }
        batch["event_preview"] = batch_repo.list_applied_events(conn, batch_id=batch_id, limit=50)
        batch["projection_preview"] = batch_projection[:20]
        batch["reconcile"] = _reconcile(batch, fingerprint, evidence)
        return batch
    finally:
        conn.close()


def list_watermarks() -> list[dict[str, Any]]:
    """Dual-watermark board: stream, both watermarks, evidence and latest batch."""
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        out: list[dict[str, Any]] = []
        for s in streams:
            evidence = proj_repo.applied_watermark_evidence(conn, s["id"])
            latest = batch_repo.latest_batch_for_stream(conn, s["id"])
            snapshot = proj_repo.get_stream_projection(conn, s["id"])
            out.append(
                {
                    "stream_id": s["id"],
                    "code": s["code"],
                    "title": s["title"],
                    "watermark_seq": s["watermark_seq"],
                    "watermark_event_time": s["watermark_event_time"],
                    "evidence": evidence,
                    "projection_alive": sum(1 for r in snapshot if not r["deleted"]),
                    "projection_deleted": sum(1 for r in snapshot if r["deleted"]),
                    "projection_fingerprint": proj_repo.projection_fingerprint(snapshot),
                    "latest_batch": latest,
                    "waves": waves_for_fixture(_load_fixture(s["fixture"]))
                    if (FIXTURES_DIR / s["fixture"]).exists()
                    else [],
                }
            )
        return out
    finally:
        conn.close()


def reset_stream(stream_id: int) -> dict[str, Any]:
    """Clear a stream's projection, both watermarks and applied-event history."""
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        conn.execute("DELETE FROM projection_rows WHERE stream_id=?", (stream_id,))
        batch_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM project_batches WHERE stream_id=?", (stream_id,)
            ).fetchall()
        ]
        for batch_id in batch_ids:
            conn.execute("DELETE FROM applied_events WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM project_batches WHERE stream_id=?", (stream_id,))
        stream_repo.set_watermarks(conn, stream_id, watermark_seq=0, watermark_event_time="")
        conn.commit()
        refreshed = stream_repo.get_stream(conn, stream_id)
        assert refreshed is not None
        return refreshed
    finally:
        conn.close()


def run_demo(stream_id: int) -> dict[str, Any]:
    """Reset the stream, then walk every fixture wave in order.

    Each wave is applied strictly first so a reject path is always visible;
    if it is rejected, the same wave is retried in catch-up mode, which only
    succeeds when the seq end can legitimately advance alone.
    """
    reset_stream(stream_id)
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if stream is None:
            raise ValueError("stream not found")
        all_events = _load_fixture(stream["fixture"])
    finally:
        conn.close()
    waves = waves_for_fixture(all_events)
    steps: list[dict[str, Any]] = []
    for i, wave in enumerate(waves):
        batch = run_project(stream_id, mode="strict", wave=wave, note=f"demo wave {wave} strict")
        steps.append(_demo_step(wave, "strict", batch))
        if batch["decision"] == "reject" and i > 0:
            caught = run_project(
                stream_id, mode="catch_up", wave=wave, note=f"demo wave {wave} catch_up"
            )
            steps.append(_demo_step(wave, "catch_up", caught))
    return {
        "stream_id": stream_id,
        "waves": waves,
        "steps": steps,
    }


def _demo_step(wave: int, mode: str, batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "wave": wave,
        "mode": mode,
        "decision": batch["decision"],
        "advance_mode": batch["advance_mode"],
        "reason_code": batch["reason_code"],
        "reason_text": batch["reason_text"],
        "batch_no": batch["batch_no"],
        "batch_id": batch["id"],
        "wm_seq_before": batch["wm_seq_before"],
        "wm_seq_after": batch["wm_seq_after"],
        "wm_time_before": batch["wm_time_before"],
        "wm_time_after": batch["wm_time_after"],
        "reconcile_ok": batch["reconcile"]["all_ok"],
    }
