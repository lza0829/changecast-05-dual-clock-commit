from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlite3


def _row_payload(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
    d.pop("payload_json", None)
    d["deleted"] = bool(d["deleted"])
    return d


def replace_stream_projection(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    rows: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM projection_rows WHERE stream_id=?", (stream_id,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO projection_rows(
                stream_id, entity_key, payload_json, applied_seq, applied_event_id, deleted, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                stream_id,
                row["entity_key"],
                json.dumps(row.get("payload"), ensure_ascii=False, sort_keys=True)
                if row.get("payload") is not None
                else None,
                row["applied_seq"],
                row["applied_event_id"],
                1 if row.get("deleted") else 0,
                row.get("updated_at") or "",
            ),
        )


def list_projections(
    conn: sqlite3.Connection,
    *,
    stream_id: int | None = None,
    include_deleted: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM projection_rows WHERE 1=1"
    params: list[Any] = []
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    if not include_deleted:
        sql += " AND deleted=0"
    sql += " ORDER BY stream_id, entity_key"
    return [_row_payload(r) for r in conn.execute(sql, params).fetchall()]


def get_stream_projection(conn: sqlite3.Connection, stream_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM projection_rows WHERE stream_id=? ORDER BY entity_key",
        (stream_id,),
    ).fetchall()
    return [_row_payload(r) for r in rows]


def projection_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Stable digest of a projection snapshot, used to prove 'no movement' on reject."""
    payload = sorted(
        (
            {
                "entity_key": r["entity_key"],
                "payload": r.get("payload"),
                "applied_seq": r["applied_seq"],
                "applied_event_id": r["applied_event_id"],
                "deleted": bool(r.get("deleted")),
            }
            for r in rows
        ),
        key=lambda r: r["entity_key"],
    )
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def list_known_event_ids(conn: sqlite3.Connection, stream_id: int) -> set[str]:
    """Only successfully applied events count as known; rejected/skipped ones stay pending."""
    rows = conn.execute(
        """
        SELECT DISTINCT event_id FROM applied_events
        WHERE stream_id=? AND result IN ('applied_upsert', 'applied_delete')
        """,
        (stream_id,),
    ).fetchall()
    return {r["event_id"] for r in rows}


def applied_watermark_evidence(conn: sqlite3.Connection, stream_id: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT seq, event_time FROM applied_events
        WHERE stream_id=? AND result IN ('applied_upsert', 'applied_delete')
        """,
        (stream_id,),
    ).fetchall()
    seqs = [int(r["seq"]) for r in rows]
    times = [str(r["event_time"]) for r in rows if r["event_time"]]
    return {
        "applied_events": len(rows),
        "max_applied_seq": max(seqs) if seqs else 0,
        "max_applied_event_time": max(times) if times else "",
    }


def list_ooo_buffer(conn: sqlite3.Connection, stream_id: int | None = None) -> list[dict[str, Any]]:
    if stream_id is None:
        rows = conn.execute("SELECT * FROM ooo_buffer ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ooo_buffer WHERE stream_id=? ORDER BY id DESC",
            (stream_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"]) if d["payload_json"] else None
        d.pop("payload_json", None)
        out.append(d)
    return out
