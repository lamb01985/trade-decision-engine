from datetime import date, datetime, timedelta, timezone

import app.historical_replay as replay_module
from app.engines.market import analyze_symbol
from app.engines.setups import completed_five_minute_bars
from app.historical_replay import ReplayConfig, replay_loaded
from app.persistence import create_stores
from tests.test_v2 import market_bars


def _run(tmp_path, name="one", symbols=("NVDA",), bars=None):
    path = tmp_path / f"{name}.sqlite3"
    stores = create_stores(f"sqlite:///{path}", initialize=True)
    config = ReplayConfig(symbols, date(2026, 8, 12), date(2026, 8, 13), path,
                          tmp_path / "cache", strict_session_schedule=False)
    try:
        result = replay_loaded(config, bars or {symbol: market_bars() for symbol in symbols},
                               stores.research)
        instances = [dict(row) for row in stores.research.instances()]
        observations = {row["setup_instance_id"]:
            [dict(item) for item in stores.research.observations(row["setup_instance_id"])]
            for row in instances}
        return result, instances, observations, stores.research
    finally:
        stores.signal.close()


def _business_signature(instances, observations):
    return [(
        row["setup_instance_id"], row["symbol"], row["session_date"],
        row["setup_family"], row["direction"], row["episode_started_at"],
        [(item["market_timestamp"], item["setup_state"], item["recommended_action"],
          item["event_type"], item["closure_reason"], item["semantic_fingerprint"])
         for item in observations[row["setup_instance_id"]]]) for row in instances]


def test_replay_never_passes_future_bar_and_uses_completed_five_minute_cutoffs(monkeypatch, tmp_path):
    original = replay_module.evaluate_production
    seen = []

    def guarded(symbol, visible, previous, parameters):
        cutoff = max(bar.timestamp for bar in visible)
        assert all(bar.timestamp <= cutoff for bar in visible)
        assert cutoff.astimezone(replay_module.ET).minute % 5 == 4
        seen.append(cutoff)
        return original(symbol, visible, previous, parameters)

    monkeypatch.setattr(replay_module, "evaluate_production", guarded)
    result, _, _, _ = _run(tmp_path)
    assert seen == sorted(seen)
    assert result.cycles_processed == len(seen)


def test_replay_is_deterministic_and_provenance_is_isolated(tmp_path):
    first = _run(tmp_path, "first")
    second = _run(tmp_path, "second")
    assert first[0].replay_run_id == second[0].replay_run_id
    assert _business_signature(first[1], first[2]) == _business_signature(second[1], second[2])
    for rows in first[2].values():
        for row in rows:
            assert row["data_quality"]["source_mode"] == "REPLAY"
            assert row["data_quality"]["replay_run_id"] == first[0].replay_run_id
            assert row["feature_source_at"] <= row["decision_bar_timestamp"]


def test_identical_visible_state_has_live_replay_pipeline_parity(monkeypatch, tmp_path):
    calls = []
    original = replay_module.evaluate_production

    def capture(symbol, visible, previous, parameters):
        result = original(symbol, visible, previous, parameters)
        calls.append((list(visible), previous, result))
        return result

    monkeypatch.setattr(replay_module, "evaluate_production", capture)
    _run(tmp_path)
    visible, previous, replay_result = calls[-1]
    live_result = original("NVDA", visible, previous)
    assert replay_result.decision.model_dump(exclude={"state_changed_at"}) == \
        live_result.decision.model_dump(exclude={"state_changed_at"})
    assert replay_result.candidates == live_result.candidates


def test_provider_gap_is_not_observed_not_no_setup(tmp_path):
    bars = market_bars()
    missing = datetime(2026, 8, 12, 14, 4, tzinfo=timezone.utc)
    bars = [bar for bar in bars if bar.timestamp != missing]
    result, _, _, _ = _run(tmp_path, bars={"NVDA": bars})
    assert result.gaps == 1
    assert result.not_observed_coverage == 1
    assert result.status == "PARTIAL"


def test_session_state_resets_and_open_instances_close_at_regular_close(monkeypatch, tmp_path):
    prior_by_day = {}
    original = replay_module.evaluate_production

    def capture(symbol, visible, previous, parameters):
        day = visible[-1].timestamp.date()
        prior_by_day.setdefault(day, previous)
        return original(symbol, visible, previous, parameters)

    monkeypatch.setattr(replay_module, "evaluate_production", capture)
    _, instances, observations, _ = _run(tmp_path)
    assert all(value is None for value in prior_by_day.values())
    for instance in instances:
        assert observations[instance["setup_instance_id"]][-1]["closure_reason"] in {
            "SESSION_ENDED", "CONDITION_NO_LONGER_PRESENT", "INVALIDATED", "EXPIRED"}


def test_replay_output_rejects_non_sqlite_or_empty_universe(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="SQLite"):
        ReplayConfig(("NVDA",), date(2026, 8, 12), date(2026, 8, 12), tmp_path / "prod.pg")
    with pytest.raises(ValueError, match="symbol"):
        ReplayConfig((), date(2026, 8, 12), date(2026, 8, 12), tmp_path / "safe.sqlite3")


def test_point_in_time_hod_lod_vwap_opening_range_and_prior_day():
    bars = market_bars()
    first_day = [bar for bar in bars if bar.timestamp.date() == date(2026, 8, 12)]
    prefix = first_day[:35]
    early = analyze_symbol("NVDA", prefix)
    later = analyze_symbol("NVDA", first_day[:60])
    assert early.high_of_day == max(bar.high for bar in prefix)
    assert early.low_of_day == min(bar.low for bar in prefix)
    assert early.timeframes["1m"].vwap != later.timeframes["1m"].vwap
    opening = prefix[:30]
    assert early.opening_range_high == max(bar.high for bar in opening)
    assert early.opening_range_low == min(bar.low for bar in opening)
    second_day_prefix = bars[:95]
    second = analyze_symbol("NVDA", second_day_prefix)
    assert second.prior_day_high == max(bar.high for bar in first_day)
    assert second.prior_day_low == min(bar.low for bar in first_day)
    assert second.prior_day_close == first_day[-1].close


def test_in_progress_five_minute_bar_is_not_visible_to_detector():
    bars = market_bars()[:7]  # 09:30-09:36: second 5m bucket is incomplete.
    completed = completed_five_minute_bars(bars)
    assert len(completed) == 1
    assert completed[0].timestamp == bars[0].timestamp
    assert completed[0].close == bars[4].close


def test_versioned_calendar_closes_black_friday_at_1300_without_phantom_cycles(tmp_path):
    from app.historical_replay import regular_session_close
    assert regular_session_close(date(2025, 11, 28)).isoformat() == "13:00:00"
    assert regular_session_close(date(2025, 11, 26)).isoformat() == "16:00:00"

    day = date(2025, 11, 28)
    start = datetime(2025, 11, 28, 14, 30, tzinfo=timezone.utc)
    bars = [replay_module.Bar(timestamp=start + timedelta(minutes=index),
        open=100 + index / 100, high=100.1 + index / 100,
        low=99.9 + index / 100, close=100 + index / 100, volume=10_000)
        for index in range(210)]
    path = tmp_path / "early-close.sqlite3"
    stores = create_stores(f"sqlite:///{path}", initialize=True)
    try:
        result = replay_loaded(ReplayConfig(("SPY",), day, day, path,
            tmp_path / "cache"), {"SPY": bars}, stores.research)
        observations = [item for instance in stores.research.instances()
            for item in stores.research.observations(instance["setup_instance_id"])]
    finally:
        stores.signal.close()
    assert result.cycles_processed == 42
    assert result.gaps == result.not_observed_coverage == 0
    assert all(item["market_timestamp"].hour <= 18 for item in observations)
    assert all(item["market_timestamp"] <= datetime(2025, 11, 28, 18, 0)
               for item in observations)
