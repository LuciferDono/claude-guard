"""Adversarial regression tests.

Every test here corresponds to a bug that shipped in 0.1.0 and that the original
67-test suite certified as working. They failed to catch it because they built a
directory layout the product never encounters and reused one watcher instance
across scans.

The rule these encode: test the shape the real world has, and the lifecycle the
caller actually uses.
"""

import json
import os
import sys
import threading
import uuid as uuidlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_guard.config import Config
from claude_guard.engine import Engine
from claude_guard.state import State, MAX_SEEN_UUIDS
from claude_guard.watcher import (
    SessionWatcher,
    calculate_cost,
    diagnose,
    find_session_files,
    parse_jsonl_line,
    resolve_pricing,
    OPUS_TIER,
    SONNET_TIER,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_record(model="claude-sonnet-5", inp=1_000_000, out=0, uid=None, **usage_extra):
    usage = {"input_tokens": inp, "output_tokens": out}
    usage.update(usage_extra)
    return {
        "uuid": uid or str(uuidlib.uuid4()),
        "type": "assistant",
        "sessionId": "s",
        "message": {"model": model, "usage": usage},
    }


def write_session(dirpath: Path, name=None, records=()):
    """Write a session file in the REAL layout: projects/<proj>/<uuid>.jsonl"""
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / (name or f"{uuidlib.uuid4()}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


@pytest.fixture
def claude_home(tmp_path):
    """A ~/.claude that mirrors the real on-disk shape, decoys included."""
    base = tmp_path / ".claude"
    proj = base / "projects" / "C--Users-someone-Projekts-Demo"
    proj.mkdir(parents=True)
    # Decoys that really do sit next to session files and must NOT be swept up.
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").write_text("not a session")
    sidecar = proj / str(uuidlib.uuid4())
    sidecar.mkdir()
    (sidecar / "notes.jsonl").write_text('{"message":{"model":"claude-opus-5",'
                                         '"usage":{"input_tokens":999999999}}}\n')
    return base, proj


def fresh_engine(tmp_path):
    """Engine whose state persists to disk, as it does in production."""
    state = State()
    state_file = tmp_path / "state.json"
    engine = Engine(config=Config(), state=state)
    engine._state_file = state_file
    state.save = lambda path=None, _s=state, _p=state_file: State.save(_s, _p)
    return engine, state_file


def load_state(state_file):
    return State.load(state_file)


# ---------------------------------------------------------------------------
# BUG 1 — discovery found 0 of 208 real files
# ---------------------------------------------------------------------------

def test_finds_files_in_real_layout(claude_home):
    base, proj = claude_home
    write_session(proj, records=[make_record()])
    write_session(proj, records=[make_record()])

    found = find_session_files(base)
    assert len(found) == 2, (
        "Real layout is projects/<proj>/<uuid>.jsonl. Finding zero here is the "
        "0.1.0 bug: the breaker silently never fires."
    )


def test_does_not_sweep_sidecar_directories(claude_home):
    base, proj = claude_home
    write_session(proj, records=[make_record()])
    found = find_session_files(base)
    assert all(p.parent == proj for p in found)
    assert not any("notes.jsonl" in str(p) for p in found)


def test_legacy_sessions_layout_still_supported(claude_home):
    base, proj = claude_home
    write_session(proj / "sessions", records=[make_record()])
    assert len(find_session_files(base)) == 1


def test_diagnose_reports_unhealthy_when_nothing_found(tmp_path):
    report = diagnose(tmp_path / "nonexistent")
    assert report["healthy"] is False
    assert report["problems"]
    assert "NOT block spending" in " ".join(report["problems"]) or \
           "not found" in " ".join(report["problems"]).lower()


def test_diagnose_healthy_on_real_layout(claude_home):
    base, proj = claude_home
    write_session(proj, records=[make_record()])
    report = diagnose(base)
    assert report["healthy"] is True
    assert report["session_file_count"] == 1


# ---------------------------------------------------------------------------
# BUG 2 — a fresh watcher per hook re-counted the whole file every tool call
# ---------------------------------------------------------------------------

def test_fresh_watcher_per_hook_does_not_double_count(claude_home, tmp_path):
    """The PostToolUse hook builds a NEW SessionWatcher on every tool call.

    In 0.1.0 that re-read each file from byte 0, so one $3 call became $15
    after five tool calls, without bound.
    """
    base, proj = claude_home
    state_file = tmp_path / "state.json"

    # Install: baseline against whatever already exists.
    s0 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s0), claude_dir=base).scan_once()
    s0.save(state_file)

    # Then one real API call happens.
    write_session(proj, records=[make_record(inp=1_000_000)])  # $3.00 sonnet

    for _ in range(10):
        state = State.load(state_file)
        engine = Engine(config=Config(), state=state)
        watcher = SessionWatcher(engine, claude_dir=base)
        watcher.scan_once()
        state.save(state_file)

    final = State.load(state_file)
    assert final.current_session_cost == pytest.approx(3.0), (
        f"Expected $3.00 counted once, got ${final.current_session_cost:.2f}"
    )


def test_uuid_ledger_prevents_recount_when_file_is_reread(claude_home, tmp_path):
    """Offsets are an optimisation; the uuid ledger is the guarantee.

    Whenever a file genuinely is re-read from the start -- a repeated backfill,
    a rotation that resets the offset -- every record must still be counted
    exactly once.
    """
    base, proj = claude_home
    write_session(proj, records=[make_record(inp=1_000_000)])
    state_file = tmp_path / "state.json"

    for _ in range(5):
        state = State.load(state_file)
        state.file_positions = {}  # force a full re-read every time
        engine = Engine(config=Config(), state=state)
        SessionWatcher(engine, claude_dir=base, backfill=True).scan_once()
        state.save(state_file)

    assert State.load(state_file).get_current_day_cost() == pytest.approx(3.0)


def test_appended_records_are_counted_once_each(claude_home, tmp_path):
    base, proj = claude_home
    state_file = tmp_path / "state.json"
    path = proj / f"{uuidlib.uuid4()}.jsonl"
    path.write_text("")

    def scan():
        state = State.load(state_file)
        engine = Engine(config=Config(), state=state)
        SessionWatcher(engine, claude_dir=base).scan_once()
        state.save(state_file)

    scan()  # install / baseline
    for _ in range(2):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(make_record(inp=1_000_000)) + "\n")
        scan()
    scan()  # extra scan must add nothing

    assert State.load(state_file).current_session_cost == pytest.approx(6.0)


def test_truncation_and_rewrite_does_not_double_count(claude_home, tmp_path):
    base, proj = claude_home
    state_file = tmp_path / "state.json"
    rec = make_record(inp=1_000_000)
    path = proj / f"{uuidlib.uuid4()}.jsonl"
    path.write_text("")

    def scan():
        state = State.load(state_file)
        engine = Engine(config=Config(), state=state)
        SessionWatcher(engine, claude_dir=base).scan_once()
        state.save(state_file)

    scan()  # install / baseline
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    scan()
    # Rewrite the same record (file rotated / replaced, same content).
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    scan()

    assert State.load(state_file).current_session_cost == pytest.approx(3.0)


def test_no_file_cap_leaves_sessions_untracked(claude_home, tmp_path):
    """0.1.0 scanned only the 5 most recent files. Spending in the 6th was invisible."""
    base, proj = claude_home
    for _ in range(9):
        write_session(proj, records=[make_record(inp=1_000_000)])

    state = State()
    engine = Engine(config=Config(), state=state)
    SessionWatcher(engine, claude_dir=base, backfill=True).scan_once()
    # Assert on the daily bucket, not session cost: each file is a distinct
    # session id, so current_session_cost correctly holds only the last one.
    # Daily is the aggregate the breaker uses across sessions.
    assert state.get_current_day_cost() == pytest.approx(27.0), (
        "All 9 sessions must be tracked. 0.1.0 capped the scan at 5 files, so "
        "spending in older sessions was invisible to the daily budget."
    )


# ---------------------------------------------------------------------------
# BUG 3 — pricing: unknown models silently billed at the cheapest tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", [
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
])
def test_opus_models_priced_as_opus(model):
    pricing, exact = resolve_pricing(model)
    assert pricing is OPUS_TIER
    assert exact, f"{model} appears in real logs and must be priced exactly"


def test_sonnet_5_priced_as_sonnet():
    pricing, exact = resolve_pricing("claude-sonnet-5")
    assert pricing is SONNET_TIER and exact


def test_unknown_model_uses_most_expensive_tier():
    """Safety asymmetry: over-estimate trips early, under-estimate never trips."""
    pricing, exact = resolve_pricing("claude-somethingnew-9")
    assert pricing is OPUS_TIER
    assert exact is False


def test_unknown_model_never_cheaper_than_any_known_model():
    unknown, _ = resolve_pricing("totally-unknown-model")
    for name in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        known, _ = resolve_pricing(name)
        assert unknown["input"] >= known["input"]
        assert unknown["output"] >= known["output"]


def test_input_tokens_are_not_reduced_by_cache_tokens():
    """Real log shape: input_tokens=3 alongside cache_creation=55614.

    0.1.0 computed max(0, input - cache_read - cache_create), which zeroes real
    input whenever caching is active.
    """
    cost = calculate_cost("claude-sonnet-5", {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
    })
    # 1M input @ $3 + 1M cache read @ $0.30
    assert cost == pytest.approx(3.30)


def test_cache_write_tiers_priced_separately():
    cost = calculate_cost("claude-sonnet-5", {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 2_000_000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 1_000_000,
            "ephemeral_1h_input_tokens": 1_000_000,
        },
    })
    assert cost == pytest.approx(3.75 + 6.00)


def test_unattributed_cache_write_priced_at_expensive_rate():
    cost = calculate_cost("claude-sonnet-5", {
        "cache_creation_input_tokens": 1_000_000,
    })
    assert cost == pytest.approx(6.00)  # 1h rate, not 5m


def test_synthetic_records_are_free():
    assert calculate_cost("<synthetic>", {"input_tokens": 10_000_000}) == 0.0
    assert parse_jsonl_line(json.dumps(make_record(model="<synthetic>"))) is None


def test_config_pricing_override_wins():
    custom = {"input": 1.0, "output": 1.0, "cache_read": 0.0,
              "cache_write_5m": 0.0, "cache_write_1h": 0.0}
    cost = calculate_cost("claude-opus-5", {"input_tokens": 1_000_000},
                          overrides={"claude-opus-5": custom})
    assert cost == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Hostile input — a guard that crashes stops guarding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "", "   ", "\n", "not json", "{", "}", "[]", "null", "true", "42",
    '{"message": null}', '{"message": {"usage": null}}',
    '{"message": {"usage": {}}}', '{"message": {"usage": []}}',
    '{"message": {"model": "claude-opus-5"}}',
    '{"usage": {"input_tokens": "abc"}}',
    '\x00\x01\x02', "�" * 100,
])
def test_malformed_lines_never_raise(line):
    assert parse_jsonl_line(line) is None


@pytest.mark.parametrize("usage", [
    {"input_tokens": -5_000_000},
    {"input_tokens": float("nan")},
    {"input_tokens": float("inf")},
    {"input_tokens": None},
    {"input_tokens": "1000"},
    {"input_tokens": [1, 2, 3]},
])
def test_hostile_usage_values_never_produce_negative_or_nan(usage):
    cost = calculate_cost("claude-sonnet-5", usage)
    assert cost >= 0.0 and cost == cost


def test_oversized_line_is_skipped_not_loaded():
    from claude_guard.watcher import MAX_LINE_BYTES
    assert parse_jsonl_line("{" + "x" * (MAX_LINE_BYTES + 10)) is None


def test_corrupt_state_file_degrades_to_empty(tmp_path):
    p = tmp_path / "state.json"
    for junk in ("{not json", "", "[]", "null", "\x00\x00"):
        p.write_text(junk)
        assert State.load(p).current_session_cost == 0.0


def test_scan_survives_unreadable_and_vanishing_files(claude_home, tmp_path):
    base, proj = claude_home
    good = write_session(proj, records=[make_record(inp=1_000_000)])
    ghost = proj / f"{uuidlib.uuid4()}.jsonl"
    ghost.write_text("")
    state = State()
    engine = Engine(config=Config(), state=state)
    watcher = SessionWatcher(engine, claude_dir=base, backfill=True)
    ghost.unlink()  # disappears mid-scan
    watcher.scan_once()
    assert state.current_session_cost == pytest.approx(3.0)


def test_record_cost_rejects_nan_and_negative():
    s = State()
    s.record_cost(float("nan"), "m", "s")
    s.record_cost(-100.0, "m", "s")
    assert s.current_session_cost == 0.0


def test_missing_claude_dir_is_not_fatal(tmp_path):
    assert find_session_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------

def test_state_save_is_atomic(tmp_path):
    p = tmp_path / "state.json"
    s = State()
    s.record_cost(1.0, "claude-sonnet-5", "sess")
    s.save(p)
    assert json.loads(p.read_text())["current_session_cost"] == 1.0
    assert not list(tmp_path.glob(".state-*.tmp")), "temp file left behind"


def test_concurrent_saves_leave_valid_json(tmp_path):
    p = tmp_path / "state.json"
    errors = []

    def writer(n):
        try:
            for _ in range(15):
                s = State()
                s.record_cost(float(n), "claude-sonnet-5", f"s{n}")
                s.save(p)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    json.loads(p.read_text())  # must parse — never a torn write


def test_seen_uuid_ledger_is_bounded(tmp_path):
    s = State()
    for i in range(MAX_SEEN_UUIDS + 500):
        s.mark_seen(f"u{i}")
    assert len(s.seen_uuids) == MAX_SEEN_UUIDS
    assert len(s._seen_index) == MAX_SEEN_UUIDS
    assert s.has_seen(f"u{MAX_SEEN_UUIDS + 499}")


def test_seen_ledger_round_trips_through_disk(tmp_path):
    p = tmp_path / "state.json"
    s = State()
    s.mark_seen("abc")
    s.save(p)
    assert State.load(p).mark_seen("abc") is False


def test_records_without_uuid_are_still_counted():
    """Cannot dedupe them, but under-counting defeats the guard."""
    s = State()
    assert s.mark_seen(None) is True
    assert s.mark_seen(None) is True


def test_file_positions_pruned_for_deleted_files(claude_home, tmp_path):
    base, proj = claude_home
    path = write_session(proj, records=[make_record()])
    state = State()
    engine = Engine(config=Config(), state=state)
    w = SessionWatcher(engine, claude_dir=base, backfill=True)
    w.scan_once()
    assert len(state.file_positions) == 1
    path.unlink()
    w.scan_once()
    assert state.file_positions == {}


# ---------------------------------------------------------------------------
# BUG 5 — first install retroactively billed the user's entire history
# ---------------------------------------------------------------------------
# Found by stress-testing against a real ~/.claude with 208 session files:
# the first scan recorded $29,200 of historical spend against a $50 default
# daily budget. With action_on_hard_limit="kill" that terminates every session
# immediately on install — the tool bricks Claude Code on day one.

def test_first_run_baselines_and_counts_nothing(claude_home, tmp_path):
    base, proj = claude_home
    for _ in range(4):
        write_session(proj, records=[make_record(inp=10_000_000)])  # $30 each

    state = State()
    engine = Engine(config=Config(), state=state)
    counted = SessionWatcher(engine, claude_dir=base).scan_once()

    assert counted == 0
    assert state.get_current_day_cost() == 0.0, (
        "A fresh install must not retroactively bill existing history."
    )
    assert state.initialized is True
    assert state.baselined_at


def test_spend_after_baseline_is_counted(claude_home, tmp_path):
    base, proj = claude_home
    write_session(proj, records=[make_record(inp=10_000_000)])  # pre-existing
    state_file = tmp_path / "state.json"

    s0 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s0), claude_dir=base).scan_once()
    s0.save(state_file)

    write_session(proj, records=[make_record(inp=1_000_000)])  # new $3.00 call

    s1 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s1), claude_dir=base).scan_once()
    assert s1.get_current_day_cost() == pytest.approx(3.0)


def test_backfill_opt_in_counts_history(claude_home):
    base, proj = claude_home
    write_session(proj, records=[make_record(inp=1_000_000)])
    state = State()
    engine = Engine(config=Config(), state=state)
    SessionWatcher(engine, claude_dir=base, backfill=True).scan_once()
    assert state.get_current_day_cost() == pytest.approx(3.0)


def test_install_against_large_history_does_not_trip_breaker(claude_home, tmp_path):
    """End-to-end: the exact scenario that would have bricked a real install."""
    from claude_guard.engine import BudgetStatus
    base, proj = claude_home
    for _ in range(30):
        write_session(proj, records=[make_record(inp=10_000_000)])  # $900 total

    state = State()
    engine = Engine(config=Config(), state=state)  # defaults: daily $50, kill
    SessionWatcher(engine, claude_dir=base).scan_once()

    assert engine.check_budget().status == BudgetStatus.OK, (
        "Installing claude-guard must never immediately trip the breaker."
    )


def test_lost_offsets_do_not_replay_old_history(claude_home, tmp_path):
    """State corruption must not resurrect already-counted history.

    Measured on a real machine: wiping offsets replayed $28,821 of spend.
    """
    import os, time as _t
    base, proj = claude_home
    state_file = tmp_path / "state.json"
    old = write_session(proj, records=[make_record(inp=10_000_000)])  # $30

    s0 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s0), claude_dir=base).scan_once()
    s0.save(state_file)

    # Age the file so it clearly predates the baseline.
    past = _t.time() - 3600
    os.utime(old, (past, past))

    s1 = State.load(state_file)
    s1.file_positions = {}  # corruption
    SessionWatcher(Engine(config=Config(), state=s1), claude_dir=base).scan_once()
    s1.save(state_file)

    assert State.load(state_file).get_current_day_cost() == 0.0


def test_new_session_after_baseline_is_fully_read(claude_home, tmp_path):
    """The other side of the same rule: a genuinely new file must be read whole."""
    base, proj = claude_home
    state_file = tmp_path / "state.json"

    s0 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s0), claude_dir=base).scan_once()
    s0.save(state_file)

    write_session(proj, records=[make_record(inp=1_000_000),
                                 make_record(inp=1_000_000)])

    s1 = State.load(state_file)
    SessionWatcher(Engine(config=Config(), state=s1), claude_dir=base).scan_once()
    assert s1.get_current_day_cost() == pytest.approx(6.0)
