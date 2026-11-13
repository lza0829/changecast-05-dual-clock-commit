from __future__ import annotations

from typing import Any

import sqlite3

BATCH_FIELDS = (
    "batch_no",
    "stream_id",
    "status",
    "source_events",
    "applied_upsert",
    "applied_delete",
    "skipped_duplicate",
    "skipped_stale",
    "projected_alive",
    "projected_deleted",
    "max_seq",
    "max_event_time",
    "mode",
    "decision",
    "advance_mode",
    "reason_code",
    "reason_text",
    "wave",
    "wm_seq_before",
    "wm_seq_after",
    "wm_time_before",
    "wm_time_after",
    "projection_fingerprint",
    "prev_fingerprint",
    "note",
    "created_at",
)


def insert_batch(conn: sqlite3.Connection, *, fields: dict[str, Any]) -> int:
    missing = [f for f in BATCH_FIELDS if f not in fields]
    if missing:
        raise ValueError(f"batch fields missing: {missing}")
    cols = ", ".join(BATCH_FIELDS)
    placeholders = ", ".join("?" for _ in BATCH_FIELDS)
    cur = conn.execute(
        f"INSERT INTO project_batches({cols}) VALUES ({placeholders})",
        tuple(fields[f] for f in BATCH_FIELDS),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def next_batch_no(conn: sqlite3.Connection, *, date_tag: str) -> str:
    """Allocate a globally unique batch number from a monotonic counter.

    A plain COUNT(*) would collide after a stream reset removes old batches,
    so the suffix comes from a settings counter that never rewinds.
    """
    row = conn.execute("SELECT value FROM settings WHERE key='batch_seq'").fetchone()
    counter = int(row["value"]) if row else 0
    counter += 1
    conn.execute(
        "INSERT INTO settings(key, value) VALUES ('batch_seq', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(counter),),
    )
    return f"PB-{date_tag}-{counter:04d}"


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM project_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(r) if r else None


def latest_batch_for_stream(conn: sqlite3.Connection, stream_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT * FROM project_batches WHERE stream_id=? ORDER BY id DESC LIMIT 1",
        (stream_id,),
    ).fetchone()
    return dict(r) if r else None


def insert_applied_event(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    stream_id: int,
    event: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO applied_events(
            batch_id, stream_id, event_id, entity_key, op, seq, event_time, result, detail, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            stream_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            event["seq"],
            event["event_time"],
            event["result"],
            event.get("detail") or "",
            created_at,
        ),
    )


def list_applied_events(
    conn: sqlite3.Connection,
    *,
    batch_id: int | None = None,
    stream_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applied_events WHERE 1=1"
    params: list[Any] = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        params.append(batch_id)
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def batch_result_counts(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT result, COUNT(*) c FROM applied_events WHERE batch_id=? GROUP BY result
        """,
        (batch_id,),
    ).fetchall()
    return {r["result"]: int(r["c"]) for r in rows}
