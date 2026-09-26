"""SQLite repository tests: round-trip fidelity, the DB-level guards
(frozen columns, closed rows, duplicate plans), schema versioning, and safe
concurrent mutation. A fresh SqliteTradeRepository per test (tmp_path).
"""
import dataclasses
import sqlite3
import threading
from datetime import timedelta

import pytest

from smart_trade_analyzer.contracts import Direction
from smart_trade_analyzer.tests.tracking_support import T0, fresh_trade, run_ticks
from smart_trade_analyzer.tracking import (
    DuplicateActivePlanError, DuplicateSignalError, RepositoryError, SchemaVersionError, SqliteTradeRepository,
    TradeStatus, apply_observation, default_db_path,
)
from smart_trade_analyzer.tracking.outcome import expire_trade
from smart_trade_analyzer.tracking.repository import SCHEMA_VERSION


@pytest.fixture
def repo(tmp_path):
    return SqliteTradeRepository(tmp_path / "trade_history.sqlite3")


def test_reads_on_a_nonexistent_database_do_not_create_a_file(repo):
    assert repo.list_trades() == [] and repo.get("missing") is None and not repo.path.exists()


def test_add_then_get_round_trips_every_field_exactly(repo):
    trade = run_ticks(fresh_trade(Direction.LONG), (103.4, 60))
    repo.add(trade)
    assert repo.get(trade.trade_id) == trade
    assert repo.path.exists()


def test_round_trip_preserves_a_closed_trade_exactly(repo):
    trade = run_ticks(fresh_trade(Direction.SHORT), (94.0, 60))
    repo.add(trade)
    assert repo.get(trade.trade_id) == trade and repo.get(trade.trade_id).status is TradeStatus.TP2_HIT


def test_find_by_signal_id(repo):
    trade = fresh_trade(Direction.LONG)
    repo.add(trade)
    assert repo.find_by_signal_id(trade.snapshot.signal_id) == trade
    assert repo.find_by_signal_id("nope") is None


def test_list_trades_orders_newest_tracked_first(repo):
    older = fresh_trade(trade_id="t-old", tracked_at=T0, signal_id="sig-1")
    newer = fresh_trade(trade_id="t-new", tracked_at=T0 + timedelta(hours=1), signal_id="sig-2", entry=101.0,
                        confirmation_price=101.0)  # a distinct plan, so it isn't rejected as a duplicate
    repo.add(older)
    repo.add(newer)
    assert [t.trade_id for t in repo.list_trades()] == ["t-new", "t-old"]


def test_list_trades_filters_by_status(repo):
    open_trade = fresh_trade(trade_id="t-open", signal_id="sig-open")
    closed = run_ticks(fresh_trade(trade_id="t-closed", signal_id="sig-closed"), (97.0, 60))
    repo.add(open_trade)
    repo.add(closed)
    assert [t.trade_id for t in repo.list_trades([TradeStatus.STOP_LOSS_HIT])] == ["t-closed"]
    assert {t.trade_id for t in repo.list_active()} == {"t-open"}


# -- duplicate protection ----------------------------------------------------

def test_tracking_the_same_signal_twice_is_refused(repo):
    trade = fresh_trade(signal_id="sig-dup")
    repo.add(trade)
    with pytest.raises(DuplicateSignalError) as info:
        repo.add(dataclasses.replace(trade, trade_id="different-trade-id"))
    assert info.value.existing == trade
    assert len(repo.list_trades()) == 1


def test_an_identical_active_plan_from_a_different_signal_is_refused(repo):
    first = fresh_trade(signal_id="sig-a", trade_id="trade-a")
    repo.add(first)
    same_plan_new_signal = fresh_trade(signal_id="sig-b", trade_id="trade-b")
    with pytest.raises(DuplicateActivePlanError):
        repo.add(same_plan_new_signal)
    assert len(repo.list_trades()) == 1


def test_a_plan_identical_to_an_already_closed_trade_can_be_tracked_again(repo):
    closed = run_ticks(fresh_trade(signal_id="sig-a", trade_id="trade-a"), (106.0, 60))
    repo.add(closed)
    reopened = fresh_trade(signal_id="sig-b", trade_id="trade-b")  # same plan fingerprint, new signal
    repo.add(reopened)  # must not raise: the old one is no longer active
    assert len(repo.list_trades()) == 2


def test_failed_add_persists_nothing(repo):
    trade = fresh_trade(signal_id="sig-a")
    repo.add(trade)
    with pytest.raises(DuplicateSignalError):
        repo.add(dataclasses.replace(trade, trade_id="other-id"))
    assert len(repo.list_trades()) == 1  # the failed insert added nothing


# -- database-level guards (defense in depth beyond the Python model) --------

def test_frozen_columns_cannot_be_updated_even_via_raw_sql(repo):
    trade = fresh_trade()
    repo.add(trade)
    with sqlite3.connect(str(repo.path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tracked_trades SET stop_loss = 1.0 WHERE trade_id = ?", (trade.trade_id,))


def test_a_closed_row_is_immutable_even_via_raw_sql(repo):
    trade = run_ticks(fresh_trade(), (106.0, 60))
    repo.add(trade)
    with sqlite3.connect(str(repo.path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tracked_trades SET latest_price = 999 WHERE trade_id = ?", (trade.trade_id,))


def test_plan_geometry_check_constraint_rejects_a_direct_insert(repo):
    repo.add(fresh_trade())  # ensures the schema exists on disk
    trade = fresh_trade(Direction.LONG, trade_id="bad", signal_id="sig-bad")
    from smart_trade_analyzer.tracking.repository import trade_to_params
    params = trade_to_params(trade)
    params["stop_loss"] = 200.0  # violates LONG geometry directly at the DB layer
    with sqlite3.connect(str(repo.path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tracked_trades (" + ", ".join(params) + ") VALUES (" +
                ", ".join(":" + k for k in params) + ")", params)


# -- mutate(): atomic read/apply/write, frozen-field protection --------------

def test_mutate_persists_the_functions_result(repo):
    repo.add(fresh_trade())
    trade_id = repo.list_trades()[0].trade_id
    after = repo.mutate(trade_id, lambda t: apply_observation(t, apply_observation.__globals__["PriceObservation"]
                                                              .tick(103.0, T0 + timedelta(seconds=60))))
    assert after.status is TradeStatus.TP1_HIT
    assert repo.get(trade_id).status is TradeStatus.TP1_HIT


def test_mutate_on_missing_trade_returns_none_and_touches_nothing(repo):
    assert repo.mutate("does-not-exist", lambda t: t) is None


def test_mutate_on_an_already_closed_trade_is_a_no_op(repo):
    trade = run_ticks(fresh_trade(), (97.0, 60))
    repo.add(trade)
    calls = []
    result = repo.mutate(trade.trade_id, lambda t: calls.append(t) or expire_trade(t))
    assert calls == [] and result == trade  # fn was never even invoked on a closed trade


def test_mutate_rejects_a_function_that_changes_a_frozen_field(repo):
    repo.add(fresh_trade())
    trade_id = repo.list_trades()[0].trade_id
    with pytest.raises(RepositoryError):
        repo.mutate(trade_id, lambda t: dataclasses.replace(t, tracking_price=1.0))
    assert repo.get(trade_id).tracking_price != 1.0


def _barrier_synced_repo(repo, monkeypatch, n=2):
    """Forces n callers of repo.mutate() to call BEGIN IMMEDIATE at (as near
    as possible) the same instant, so a real lock race happens instead of
    accidentally-sequential execution."""
    barrier = threading.Barrier(n)
    original_begin = SqliteTradeRepository._begin

    def synced_begin(conn):
        barrier.wait(timeout=5)
        return original_begin(conn)
    monkeypatch.setattr(SqliteTradeRepository, "_begin", staticmethod(synced_begin))


def test_concurrent_mutate_on_two_different_trades_does_not_cross_contaminate(repo, monkeypatch):
    """Two 'refreshes' racing on DIFFERENT trades must both land correctly --
    this is what BEGIN IMMEDIATE's serialization exists to guarantee, without
    the separate (and separately tested) out-of-order-observation rule
    entering into it."""
    from smart_trade_analyzer.tracking.models import PriceObservation
    trade_a = fresh_trade(trade_id="trade-a", signal_id="sig-a")
    trade_b = fresh_trade(trade_id="trade-b", signal_id="sig-b", entry=101.0, confirmation_price=101.0)
    repo.add(trade_a)
    repo.add(trade_b)
    _barrier_synced_repo(repo, monkeypatch)

    results = {}
    def worker(trade_id, price):
        results[trade_id] = repo.mutate(trade_id, lambda t: apply_observation(
            t, PriceObservation.tick(price, T0 + timedelta(seconds=60))))

    threads = [threading.Thread(target=worker, args=(tid, px))
               for tid, px in (("trade-a", 103.0), ("trade-b", 101.5))]  # trade-b: entry 101, SL 98, TP1 103 -- 101.5 touches neither
    for th in threads: th.start()
    for th in threads: th.join()

    assert results["trade-a"] is not None and results["trade-b"] is not None
    assert repo.get("trade-a").status is TradeStatus.TP1_HIT and repo.get("trade-a").observation_count == 2
    assert repo.get("trade-b").status is TradeStatus.OPEN and repo.get("trade-b").observation_count == 2


def test_concurrent_mutate_applying_the_identical_observation_is_not_double_counted(repo, monkeypatch):
    """Two overlapping 'refreshes' that both fetched the SAME ticker sample
    for the SAME trade (e.g. two browser tabs) must not corrupt state or
    count the same observation twice -- BEGIN IMMEDIATE serializes the two
    writers, and the second sees its own observation as a duplicate."""
    from smart_trade_analyzer.tracking.models import PriceObservation
    trade = fresh_trade()
    repo.add(trade)
    _barrier_synced_repo(repo, monkeypatch)

    same_observation = PriceObservation.tick(101.0, T0 + timedelta(seconds=60))
    results = []
    def worker():
        results.append(repo.mutate(trade.trade_id, lambda t: apply_observation(t, same_observation)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads: th.start()
    for th in threads: th.join()

    assert all(r is not None for r in results)
    final = repo.get(trade.trade_id)
    assert final.observation_count == 2  # applied exactly once, not twice
    assert final.latest_price_at == T0 + timedelta(seconds=60) and final.latest_price == 101.0


def test_concurrent_mutate_serializes_rather_than_corrupts(repo):
    """Two racing writers, one holding a chronologically-earlier observation
    and one a later one, must not corrupt state. Whichever happens to commit
    first decides whether BOTH observations are legitimately in order
    (count 1 -> 2 -> 3) or the earlier one loses the race and is then
    correctly rejected as out-of-order by the OTHER writer's own read
    (count 1 -> 2, discarding the loser) -- both are valid serializations.
    What must NEVER happen, under either race outcome: a write silently lost
    entirely (count stuck at 1), or the recorded latest price regressing
    behind the chronologically later observation. Each iteration gets its own
    scoped patch (pytest.MonkeyPatch.context()) so the single-threaded
    repo.add() call that sets up the next iteration is never itself routed
    through a barrier meant for that iteration's worker threads."""
    from smart_trade_analyzer.tracking.models import PriceObservation
    for later_first in (False, True):
        trade_id = f"trade-{later_first}"
        entry = 101.0 if later_first else 100.0  # distinct plans: avoids the duplicate-active-plan guard
        repo.add(fresh_trade(trade_id=trade_id, signal_id=f"sig-{later_first}", entry=entry, confirmation_price=entry))
        order = [120, 60] if later_first else [60, 120]
        results = []

        with pytest.MonkeyPatch.context() as mp:
            _barrier_synced_repo(repo, mp)

            def worker(seconds):
                results.append(repo.mutate(trade_id, lambda t: apply_observation(
                    t, PriceObservation.tick(101.0, T0 + timedelta(seconds=seconds)))))

            threads = [threading.Thread(target=worker, args=(s,)) for s in order]
            for th in threads: th.start()
            for th in threads: th.join()

        assert len(results) == 2 and all(r is not None for r in results)  # neither call raised or vanished
        final = repo.get(trade_id)
        assert final.observation_count in (2, 3)  # never 1 (a write silently lost)
        assert final.latest_price_at == T0 + timedelta(seconds=120)  # never regresses behind the later observation


# -- schema versioning --------------------------------------------------------

def test_schema_is_created_lazily_on_first_write(tmp_path):
    repo = SqliteTradeRepository(tmp_path / "fresh.sqlite3")
    assert not repo.path.exists()
    repo.add(fresh_trade())
    with sqlite3.connect(str(repo.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_a_newer_schema_version_is_refused_not_silently_reused(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(str(path)) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.execute("CREATE TABLE tracked_trades (trade_id TEXT PRIMARY KEY)")
    repo = SqliteTradeRepository(path)
    with pytest.raises(SchemaVersionError):
        repo.list_trades()
    with pytest.raises(SchemaVersionError):
        repo.add(fresh_trade())


def test_in_memory_path_is_rejected():
    with pytest.raises(ValueError):
        SqliteTradeRepository(":memory:")


def test_default_db_path_honors_the_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom_dir" / "custom.sqlite3"
    monkeypatch.setenv("SMART_TRADE_ANALYZER_DB", str(target))
    assert default_db_path() == target


def test_default_db_path_falls_back_to_the_repo_root(monkeypatch):
    monkeypatch.delenv("SMART_TRADE_ANALYZER_DB", raising=False)
    path = default_db_path()
    assert path.name == "trade_history.sqlite3"
