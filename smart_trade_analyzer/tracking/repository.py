"""Persistent trade storage: a repository abstraction over stdlib sqlite3.

Design
------
* ONE table, `tracked_trades`: the frozen snapshot columns plus the mutable
  lifecycle-state columns. No market data is stored beyond the trade's own
  evidence prices (nothing is duplicated from the analyzer or the exchange).
* The database defends the rules itself, so a bug in application code cannot
  silently rewrite history:
    - CHECK constraints: enum values, plan geometry, status<->outcome
      consistency, R present exactly for WIN/LOSS, closed_at present exactly
      for closed trades.
    - `signal_id` UNIQUE: the same signal can never be tracked twice.
    - A partial UNIQUE index over the frozen plan while a trade is active: the
      same plan cannot be tracked twice concurrently, even from a different
      analysis run of the same setup.
    - A trigger aborts any UPDATE that names a frozen snapshot column.
    - A trigger aborts any UPDATE of a row whose status is already terminal.
* One short-lived connection per operation (Streamlit runs scripts on
  different threads; sqlite3 connections are thread-bound). Writes use
  BEGIN IMMEDIATE, so two browser sessions refreshing at once serialize
  instead of overwriting each other.
* Reads never create the database file. The file and schema are created
  lazily by the first write.
* Timestamps are stored as ISO-8601 text (naive UTC, microsecond precision);
  prices as REAL, which round-trips Python floats exactly.
* Schema versioning via PRAGMA user_version; a database written by a newer
  schema is refused rather than clobbered.

Column naming: `pair` is the exchange symbol (BTCUSDT) used for ticker calls;
`symbol` is the base display symbol (BTC), as on OpportunityResult.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Sequence, Union

from ..contracts import Direction, MarketType, Timeframe
from .constants import DB_ENV_VAR, DEFAULT_DB_FILENAME, SCHEMA_VERSION
from .errors import (
    DuplicateActivePlanError, DuplicateSignalError, DuplicateTradeError, RepositoryError, SchemaVersionError,
)
from .models import (
    ACTIVE_STATUSES, TERMINAL_STATUSES, STATUS_TO_OUTCOME, TrackedTrade, TradeOutcome, TradeSnapshot, TradeStatus,
)

TABLE = "tracked_trades"

FROZEN_COLUMNS = (
    "trade_id", "signal_id", "symbol", "pair", "market", "timeframe", "direction", "setup",
    "quality_score", "grade", "entry", "entry_zone_low", "entry_zone_high", "confirmation_price",
    "invalidation_price", "stop_loss", "take_profit_1", "take_profit_2", "risk_reward_1", "risk_reward_2",
    "analysis_price", "analysis_price_source", "signal_generated_at", "tracking_price", "tracked_at",
    "expires_at",
)
MUTABLE_COLUMNS = (
    "status", "outcome", "tp1_hit_at", "tp1_hit_price", "tp2_hit_at", "tp2_hit_price", "sl_hit_at",
    "sl_hit_price", "closed_at", "close_reason", "realized_r", "latest_price", "latest_price_at",
    "observation_count", "max_observation_gap_seconds",
)
ALL_COLUMNS = FROZEN_COLUMNS + MUTABLE_COLUMNS


def _sql_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_ACTIVE_SQL = _sql_list([s.value for s in ACTIVE_STATUSES])
_TERMINAL_SQL = _sql_list([s.value for s in TERMINAL_STATUSES])

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    -- identity ----------------------------------------------------------
    trade_id            TEXT PRIMARY KEY,
    signal_id           TEXT NOT NULL UNIQUE,
    -- frozen snapshot (immutable; enforced by trigger) -------------------
    symbol              TEXT NOT NULL,
    pair                TEXT NOT NULL,
    market              TEXT NOT NULL CHECK (market IN ({_sql_list([m.value for m in MarketType])})),
    timeframe           TEXT NOT NULL CHECK (timeframe IN ({_sql_list([t.value for t in Timeframe])})),
    direction           TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    setup               TEXT NOT NULL,
    quality_score       REAL NOT NULL,
    grade               TEXT NOT NULL,
    entry               REAL NOT NULL CHECK (entry > 0),
    entry_zone_low      REAL,
    entry_zone_high     REAL,
    confirmation_price  REAL NOT NULL CHECK (confirmation_price > 0),
    invalidation_price  REAL,
    stop_loss           REAL NOT NULL CHECK (stop_loss > 0),
    take_profit_1       REAL NOT NULL CHECK (take_profit_1 > 0),
    take_profit_2       REAL NOT NULL CHECK (take_profit_2 > 0),
    risk_reward_1       REAL NOT NULL CHECK (risk_reward_1 > 0),
    risk_reward_2       REAL NOT NULL CHECK (risk_reward_2 > 0),
    analysis_price      REAL,
    analysis_price_source TEXT NOT NULL,
    signal_generated_at TEXT NOT NULL,
    tracking_price      REAL NOT NULL CHECK (tracking_price > 0),
    tracked_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    -- mutable lifecycle state --------------------------------------------
    status              TEXT NOT NULL CHECK (status IN ({_sql_list([s.value for s in TradeStatus])})),
    outcome             TEXT NOT NULL CHECK (outcome IN ({_sql_list([o.value for o in TradeOutcome])})),
    tp1_hit_at          TEXT,
    tp1_hit_price       REAL,
    tp2_hit_at          TEXT,
    tp2_hit_price       REAL,
    sl_hit_at           TEXT,
    sl_hit_price        REAL,
    closed_at           TEXT,
    close_reason        TEXT,
    realized_r          REAL,
    latest_price        REAL,
    latest_price_at     TEXT,
    observation_count   INTEGER NOT NULL CHECK (observation_count >= 1),
    max_observation_gap_seconds REAL CHECK (max_observation_gap_seconds IS NULL OR max_observation_gap_seconds >= 0),
    CHECK (tracked_at < expires_at),
    CHECK (
        (direction = 'LONG'  AND stop_loss < entry AND entry < take_profit_1 AND take_profit_1 < take_profit_2)
        OR
        (direction = 'SHORT' AND take_profit_2 < take_profit_1 AND take_profit_1 < entry AND entry < stop_loss)
    ),
    CHECK (
        (status = 'OPEN'          AND outcome = 'OPEN')
        OR (status = 'TP1_HIT'       AND outcome = 'OPEN')
        OR (status = 'TP2_HIT'       AND outcome = 'WIN')
        OR (status = 'STOP_LOSS_HIT' AND outcome = 'LOSS')
        OR (status = 'EXPIRED'       AND outcome = 'EXPIRED')
        OR (status = 'INVALIDATED'   AND outcome = 'INVALIDATED')
    ),
    CHECK ((outcome IN ('WIN', 'LOSS')) = (realized_r IS NOT NULL)),
    CHECK ((status IN ({_ACTIVE_SQL})) = (closed_at IS NULL))
)
"""

CREATE_FROZEN_TRIGGER_SQL = f"""
CREATE TRIGGER IF NOT EXISTS trg_{TABLE}_frozen_columns
BEFORE UPDATE OF {", ".join(FROZEN_COLUMNS)} ON {TABLE}
BEGIN
    SELECT RAISE(ABORT, '{TABLE}: frozen snapshot columns are immutable');
END
"""

CREATE_TERMINAL_TRIGGER_SQL = f"""
CREATE TRIGGER IF NOT EXISTS trg_{TABLE}_closed_rows
BEFORE UPDATE ON {TABLE}
WHEN OLD.status IN ({_TERMINAL_SQL})
BEGIN
    SELECT RAISE(ABORT, '{TABLE}: a closed trade is immutable');
END
"""

CREATE_ACTIVE_PLAN_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE}_active_plan
ON {TABLE} (pair, market, timeframe, direction, setup, entry, stop_loss, take_profit_1, take_profit_2)
WHERE status IN ({_ACTIVE_SQL})
"""

CREATE_STATUS_INDEX_SQL = f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_status ON {TABLE} (status)"

SCHEMA_STATEMENTS = (
    CREATE_TABLE_SQL, CREATE_FROZEN_TRIGGER_SQL, CREATE_TERMINAL_TRIGGER_SQL,
    CREATE_ACTIVE_PLAN_INDEX_SQL, CREATE_STATUS_INDEX_SQL,
)

_INSERT_SQL = f"INSERT INTO {TABLE} ({', '.join(ALL_COLUMNS)}) VALUES ({', '.join(':' + c for c in ALL_COLUMNS)})"
_UPDATE_SQL = (
    f"UPDATE {TABLE} SET " + ", ".join(f"{c} = :{c}" for c in MUTABLE_COLUMNS) + " WHERE trade_id = :trade_id"
)


def default_db_path() -> Path:
    """$SMART_TRADE_ANALYZER_DB if set, else <repo root>/trade_history.sqlite3.
    Resolved at call time (not import time) so it can be redirected."""
    override = os.environ.get(DB_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / DEFAULT_DB_FILENAME


# ---------------------------------------------------------------------------
# Row <-> model mapping.
# ---------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.isoformat(timespec="microseconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return None if value is None else datetime.fromisoformat(value)


def _f(value: Optional[float]) -> Optional[float]:
    return None if value is None else float(value)


def trade_to_params(trade: TrackedTrade) -> Dict[str, Any]:
    s = trade.snapshot
    return {
        "trade_id": trade.trade_id, "signal_id": s.signal_id, "symbol": s.symbol, "pair": s.pair,
        "market": s.market.value, "timeframe": s.timeframe.value, "direction": s.direction.value,
        "setup": s.setup, "quality_score": float(s.quality_score), "grade": s.grade,
        "entry": float(s.entry), "entry_zone_low": _f(s.entry_zone_low), "entry_zone_high": _f(s.entry_zone_high),
        "confirmation_price": float(s.confirmation_price), "invalidation_price": _f(s.invalidation_price),
        "stop_loss": float(s.stop_loss), "take_profit_1": float(s.take_profit_1),
        "take_profit_2": float(s.take_profit_2), "risk_reward_1": float(s.risk_reward_1),
        "risk_reward_2": float(s.risk_reward_2), "analysis_price": _f(s.analysis_price),
        "analysis_price_source": s.analysis_price_source, "signal_generated_at": _iso(s.signal_generated_at),
        "tracking_price": float(trade.tracking_price), "tracked_at": _iso(trade.tracked_at),
        "expires_at": _iso(trade.expires_at),
        "status": trade.status.value, "outcome": STATUS_TO_OUTCOME[trade.status].value,
        "tp1_hit_at": _iso(trade.tp1_hit_at), "tp1_hit_price": _f(trade.tp1_hit_price),
        "tp2_hit_at": _iso(trade.tp2_hit_at), "tp2_hit_price": _f(trade.tp2_hit_price),
        "sl_hit_at": _iso(trade.sl_hit_at), "sl_hit_price": _f(trade.sl_hit_price),
        "closed_at": _iso(trade.closed_at), "close_reason": trade.close_reason,
        "realized_r": _f(trade.realized_r), "latest_price": _f(trade.latest_price),
        "latest_price_at": _iso(trade.latest_price_at), "observation_count": int(trade.observation_count),
        "max_observation_gap_seconds": _f(trade.max_observation_gap_seconds),
    }


def row_to_trade(row: sqlite3.Row) -> TrackedTrade:
    snapshot = TradeSnapshot(
        signal_id=row["signal_id"], symbol=row["symbol"], pair=row["pair"],
        market=MarketType(row["market"]), timeframe=Timeframe(row["timeframe"]),
        direction=Direction(row["direction"]), setup=row["setup"], quality_score=row["quality_score"],
        grade=row["grade"], entry=row["entry"], confirmation_price=row["confirmation_price"],
        stop_loss=row["stop_loss"], take_profit_1=row["take_profit_1"], take_profit_2=row["take_profit_2"],
        risk_reward_1=row["risk_reward_1"], risk_reward_2=row["risk_reward_2"],
        signal_generated_at=_parse(row["signal_generated_at"]),
        entry_zone_low=row["entry_zone_low"], entry_zone_high=row["entry_zone_high"],
        invalidation_price=row["invalidation_price"], analysis_price=row["analysis_price"],
        analysis_price_source=row["analysis_price_source"],
    )
    return TrackedTrade(
        trade_id=row["trade_id"], snapshot=snapshot, tracking_price=row["tracking_price"],
        tracked_at=_parse(row["tracked_at"]), expires_at=_parse(row["expires_at"]),
        status=TradeStatus(row["status"]),
        tp1_hit_at=_parse(row["tp1_hit_at"]), tp1_hit_price=row["tp1_hit_price"],
        tp2_hit_at=_parse(row["tp2_hit_at"]), tp2_hit_price=row["tp2_hit_price"],
        sl_hit_at=_parse(row["sl_hit_at"]), sl_hit_price=row["sl_hit_price"],
        closed_at=_parse(row["closed_at"]), close_reason=row["close_reason"], realized_r=row["realized_r"],
        latest_price=row["latest_price"], latest_price_at=_parse(row["latest_price_at"]),
        observation_count=row["observation_count"],
        max_observation_gap_seconds=row["max_observation_gap_seconds"],
    )


# ---------------------------------------------------------------------------
# The abstraction the service depends on.
# ---------------------------------------------------------------------------

class TradeRepository(Protocol):
    def add(self, trade: TrackedTrade) -> None: ...
    def get(self, trade_id: str) -> Optional[TrackedTrade]: ...
    def find_by_signal_id(self, signal_id: str) -> Optional[TrackedTrade]: ...
    def find_active_by_plan(self, snapshot: TradeSnapshot) -> Optional[TrackedTrade]: ...
    def list_trades(self, statuses: Optional[Sequence[TradeStatus]] = None) -> List[TrackedTrade]: ...
    def list_active(self) -> List[TrackedTrade]: ...
    def mutate(self, trade_id: str, fn: Callable[[TrackedTrade], TrackedTrade]) -> Optional[TrackedTrade]: ...


class SqliteTradeRepository:
    """TradeRepository over a local SQLite file. See the module docstring."""

    def __init__(self, db_path: Union[str, "os.PathLike[str]"], busy_timeout_seconds: float = 10.0):
        if str(db_path) == ":memory:":
            raise ValueError("an in-memory database cannot persist across connections; pass a file path")
        self._path = Path(db_path)
        self._busy_timeout = busy_timeout_seconds

    @property
    def path(self) -> Path:
        return self._path

    # -- connection / schema ------------------------------------------------

    @contextmanager
    def _connection(self, *, create: bool) -> Iterator[sqlite3.Connection]:
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=self._busy_timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_schema(conn)
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _begin(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _rollback_quietly(conn: sqlite3.Connection) -> None:
        if conn.in_transaction:
            conn.execute("ROLLBACK")

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None
        if version == SCHEMA_VERSION and has_table:
            return
        if version == 0 and not has_table:
            self._begin(conn)
            try:
                for statement in SCHEMA_STATEMENTS:  # every statement is IF NOT EXISTS: safe under a race
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
            except BaseException:
                self._rollback_quietly(conn)
                raise
            return
        raise SchemaVersionError(
            f"{self._path} has schema version {version} (table present: {has_table}); "
            f"this code understands version {SCHEMA_VERSION} only. Refusing to modify it."
        )

    # -- reads (never create the file) --------------------------------------

    def _select(self, where: str, params: Sequence[Any], order: str = "") -> List[TrackedTrade]:
        if not self._path.exists():
            return []
        with self._connection(create=False) as conn:
            rows = conn.execute(f"SELECT * FROM {TABLE} {where} {order}", tuple(params)).fetchall()
        return [row_to_trade(r) for r in rows]

    def get(self, trade_id: str) -> Optional[TrackedTrade]:
        found = self._select("WHERE trade_id = ?", (trade_id,))
        return found[0] if found else None

    def find_by_signal_id(self, signal_id: str) -> Optional[TrackedTrade]:
        found = self._select("WHERE signal_id = ?", (signal_id,))
        return found[0] if found else None

    def find_active_by_plan(self, snapshot: TradeSnapshot) -> Optional[TrackedTrade]:
        found = self._select(
            f"WHERE status IN ({_ACTIVE_SQL}) AND pair = ? AND market = ? AND timeframe = ? AND direction = ? "
            f"AND setup = ? AND entry = ? AND stop_loss = ? AND take_profit_1 = ? AND take_profit_2 = ?",
            snapshot.plan_fingerprint,
        )
        return found[0] if found else None

    def list_trades(self, statuses: Optional[Sequence[TradeStatus]] = None) -> List[TrackedTrade]:
        order = "ORDER BY tracked_at DESC, trade_id ASC"
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            return self._select(f"WHERE status IN ({marks})", [s.value for s in statuses], order)
        return self._select("", (), order)

    def list_active(self) -> List[TrackedTrade]:
        return self.list_trades(ACTIVE_STATUSES)

    # -- writes ---------------------------------------------------------------

    def add(self, trade: TrackedTrade) -> None:
        """Insert a new trade atomically. Raises DuplicateSignalError if this
        signal is already tracked, DuplicateActivePlanError if an identical
        plan is still active. Nothing is written on failure."""
        with self._connection(create=True) as conn:
            self._begin(conn)
            try:
                same_signal = conn.execute(
                    f"SELECT * FROM {TABLE} WHERE signal_id = ?", (trade.snapshot.signal_id,)).fetchone()
                if same_signal is not None:
                    raise DuplicateSignalError(
                        f"signal {trade.snapshot.signal_id} is already tracked", row_to_trade(same_signal))
                if trade.is_active:
                    same_plan = conn.execute(
                        f"SELECT * FROM {TABLE} WHERE status IN ({_ACTIVE_SQL}) AND pair = ? AND market = ? "
                        f"AND timeframe = ? AND direction = ? AND setup = ? AND entry = ? AND stop_loss = ? "
                        f"AND take_profit_1 = ? AND take_profit_2 = ?", trade.snapshot.plan_fingerprint).fetchone()
                    if same_plan is not None:
                        raise DuplicateActivePlanError(
                            "an identical plan is already being tracked", row_to_trade(same_plan))
                conn.execute(_INSERT_SQL, trade_to_params(trade))
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:  # backstop: a constraint we did not pre-check
                self._rollback_quietly(conn)
                raise DuplicateTradeError(f"trade rejected by a database constraint: {exc}") from exc
            except BaseException:
                self._rollback_quietly(conn)
                raise

    def mutate(self, trade_id: str, fn: Callable[[TrackedTrade], TrackedTrade]) -> Optional[TrackedTrade]:
        """Atomically read -> fn -> write one trade. `fn` receives the current
        stored state and returns the new one; it may not change any frozen
        field. A trade that is already closed is returned untouched and `fn`
        is not called. Returns the stored state afterwards, or None if the
        trade does not exist."""
        if not self._path.exists():
            return None
        with self._connection(create=False) as conn:
            self._begin(conn)
            try:
                row = conn.execute(f"SELECT * FROM {TABLE} WHERE trade_id = ?", (trade_id,)).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return None
                current = row_to_trade(row)
                if not current.is_active:
                    conn.execute("ROLLBACK")
                    return current
                updated = fn(current)
                if (updated.trade_id != current.trade_id or updated.snapshot != current.snapshot
                        or updated.tracking_price != current.tracking_price
                        or updated.tracked_at != current.tracked_at or updated.expires_at != current.expires_at):
                    raise RepositoryError("frozen trade fields cannot be modified")
                if updated != current:
                    params = trade_to_params(updated)
                    conn.execute(_UPDATE_SQL, {c: params[c] for c in MUTABLE_COLUMNS + ("trade_id",)})
                conn.execute("COMMIT")
                return updated
            except BaseException:
                self._rollback_quietly(conn)
                raise
