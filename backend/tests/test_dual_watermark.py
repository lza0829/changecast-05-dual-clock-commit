"""Dual-watermark commit invariants, exercised against a temp SQLite file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="changecast-test-"))

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from app.engines.project import apply_batch, norm_time  # noqa: E402
from app.seed import init_db  # noqa: E402
from app.services import project_service as svc  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    for p in Path(os.environ["DATA_DIR"]).glob("*.db"):
        p.unlink()
    init_db()
    yield


def _stream_id(code: str) -> int:
    return next(s["id"] for s in svc.list_streams() if s["code"] == code)


def test_both_ends_advance_together():
    sid = _stream_id("member_profile")
    batch = svc.run_project(sid, mode="strict", wave=1)
    assert batch["decision"] == "commit"
    assert batch["advance_mode"] == "both"
    assert batch["wm_seq_after"] == 5
    assert batch["wm_time_after"] == "2026-03-01T10:00:00Z"
    assert batch["reconcile"]["all_ok"]


def test_strict_rejects_when_only_seq_can_advance():
    sid = _stream_id("member_profile")
    svc.run_project(sid, mode="strict", wave=1)  # wm seq=5, time=10:00
    batch = svc.run_project(sid, mode="strict", wave=2)  # seq 6, time flat
    assert batch["decision"] == "reject"
    assert batch["wm_seq_after"] == batch["wm_seq_before"] == 5
    assert batch["wm_time_after"] == batch["wm_time_before"] == "2026-03-01T10:00:00Z"
    assert batch["applied_upsert"] == batch["applied_delete"] == 0
    assert batch["projection_fingerprint"] == batch["prev_fingerprint"]
    assert batch["reconcile"]["all_ok"]


def test_strict_rejects_when_only_time_can_advance():
    sid = _stream_id("member_profile")
    svc.run_project(sid, mode="strict", wave=1)
    svc.run_project(sid, mode="strict", wave=2)  # rejected, no movement
    svc.run_project(sid, mode="strict", wave=3)
    before = svc.list_watermarks()[0]
    batch = svc.run_project(sid, mode="strict", wave=4)  # seq 6 < 7, time 11:00 > 10:30
    assert batch["reason_code"] == "time_only_prohibited"
    assert batch["wm_seq_after"] == 7
    assert batch["wm_time_after"] == "2026-03-01T10:30:00Z"
    after = svc.list_watermarks()[0]
    assert before["watermark_seq"] == after["watermark_seq"]
    assert before["watermark_event_time"] == after["watermark_event_time"]


def test_catch_up_advances_seq_only_and_is_marked():
    sid = _stream_id("member_profile")
    svc.run_project(sid, mode="strict", wave=1)
    batch = svc.run_project(sid, mode="catch_up", wave=2)
    assert batch["decision"] == "catch_up"
    assert batch["advance_mode"] == "seq_only"
    assert batch["wm_seq_before"] == 5 and batch["wm_seq_after"] == 6
    assert batch["wm_time_before"] == batch["wm_time_after"] == "2026-03-01T10:00:00Z"
    assert batch["mode"] == "catch_up"
    assert batch["reason_code"] == "seq_only_catch_up"
    assert batch["reconcile"]["all_ok"]


def test_catch_up_refuses_projection_rollback():
    sid = _stream_id("member_profile")
    svc.run_project(sid, mode="strict", wave=1)
    batch = svc.run_project(sid, mode="catch_up", wave=4)  # seq 6 < 5? no: 6 > 5, time 11:00 > 10:00
    # wave 4 advances both ends under catch_up -> commit
    assert batch["decision"] == "commit"
    batch2 = svc.run_project(sid, mode="catch_up", wave=5)  # seq 8, time 10:15 < watermark
    assert batch2["decision"] == "reject"
    assert batch2["reason_code"] == "late_event_prohibited"


def test_catch_up_rejects_time_only():
    sid = _stream_id("member_profile")
    for wave, mode in ((1, "strict"), (2, "catch_up"), (3, "strict")):
        svc.run_project(sid, mode=mode, wave=wave)
    batch = svc.run_project(sid, mode="catch_up", wave=4)  # seq candidate 6 < 7
    assert batch["decision"] == "reject"
    assert batch["reason_code"] == "time_only_prohibited"
    assert batch["wm_seq_after"] == batch["wm_seq_before"] == 7
    assert batch["wm_time_after"] == batch["wm_time_before"] == "2026-03-01T10:30:00Z"


def test_rejected_events_stay_pending_and_rerun_is_noop():
    sid = _stream_id("member_profile")
    svc.run_project(sid, mode="strict", wave=1)
    svc.run_project(sid, mode="strict", wave=2)  # rejected
    rerun = svc.run_project(sid, mode="strict", wave=2)
    assert rerun["decision"] == "reject"
    assert rerun["reason_code"] == "strict_requires_both"  # still pending, not duplicate


def test_both_watermarks_move_in_one_transaction():
    sid = _stream_id("member_profile")
    for wave, mode in ((1, "strict"), (2, "catch_up"), (3, "strict")):
        svc.run_project(sid, mode=mode, wave=wave)
    board = next(w for w in svc.list_watermarks() if w["stream_id"] == sid)
    assert board["watermark_seq"] == board["evidence"]["max_applied_seq"] == 7
    assert board["watermark_event_time"] == board["evidence"]["max_applied_event_time"]


def test_engine_normalizes_time():
    assert norm_time("2026-03-01T08:00:00+00:00") == "2026-03-01T08:00:00Z"
    assert norm_time("2026-03-01T16:00:00+08:00") == "2026-03-01T08:00:00Z"


def test_engine_rejects_empty_wave():
    result = apply_batch(
        projection=[], events=[], wm_seq=0, wm_time="",
        mode="strict", known_event_ids=set(), updated_at="t",
    )
    assert result["decision"] == "reject"
    assert result["reason_code"] == "no_pending_events"
    assert result["wm_seq_after"] == 0 and result["wm_time_after"] == ""
