import json
import sqlite3
from pathlib import Path

from app.models import Bar, StandardDecision, SymbolAnalysis
from app.parameters import EngineParameters
from app.signal_identity import signal_fingerprint
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION
from app.persistence.contracts import SignalStore


class NoOpSignalStore:
    """Hosted adapter: analysis remains available while persistence is unavailable."""
    persistent = False

    def observe(self, analysis: SymbolAnalysis, bars: list[Bar], parameters: EngineParameters) -> None:
        return None

    def get_latest_decision(self, symbol: str) -> StandardDecision | None:
        return None


class SignalLogger:
    """Local evidence log. It records market setups, never personal trades or P/L."""
    def __init__(self, path: Path) -> None:
        self.persistent = True
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
                    setup_family TEXT NOT NULL, setup_state TEXT NOT NULL, quality TEXT NOT NULL,
                    direction TEXT NOT NULL, price REAL NOT NULL, trigger REAL, invalidation REAL,
                    target_1 REAL, target_2 REAL, vwap REAL, structure_5m TEXT, structure_15m TEXT,
                    relative_volume REAL, atr REAL, levels_json TEXT NOT NULL, config_json TEXT NOT NULL,
                    mfe REAL DEFAULT 0, mae REAL DEFAULT 0, target_1_reached INTEGER DEFAULT 0,
                    target_2_reached INTEGER DEFAULT 0, invalidation_reached INTEGER DEFAULT 0,
                    target_1_time TEXT, target_2_time TEXT, invalidation_time TEXT,
                    UNIQUE(symbol, timestamp, setup_family, setup_state)
                );
                CREATE TABLE IF NOT EXISTS signal_prices (
                    signal_id INTEGER NOT NULL, timestamp TEXT NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    UNIQUE(signal_id, timestamp), FOREIGN KEY(signal_id) REFERENCES signals(id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
            for name in ("signal_fingerprint", "engine_version", "setup_rules_version", "parameter_version"):
                if name not in columns:
                    db.execute(f"ALTER TABLE signals ADD COLUMN {name} TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_signals_fingerprint ON signals(signal_fingerprint)")

    def observe(self, analysis: SymbolAnalysis, bars: list[Bar], parameters: EngineParameters) -> None:
        decision = analysis.decision
        if decision is None or decision.setup_family.value == "NONE":
            return
        f5 = analysis.instrument.timeframes["5m"]
        levels = [{"price": x.price, "types": x.types, "importance": x.importance.value,
                   "confluence": x.confluence} for x in analysis.levels]
        fingerprint = signal_fingerprint(symbol=decision.symbol,
            setup_family=decision.setup_family.value, direction=decision.direction,
            trigger=decision.trigger, session=decision.timestamp,
            engine_version=decision.engine_version,
            setup_rules_version=decision.setup_rules_version)
        with self._connect() as db:
            db.execute("""INSERT OR IGNORE INTO signals
                (symbol,timestamp,setup_family,setup_state,quality,direction,price,trigger,invalidation,
                 target_1,target_2,vwap,structure_5m,structure_15m,relative_volume,atr,levels_json,config_json,
                 signal_fingerprint,engine_version,setup_rules_version,parameter_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (decision.symbol, decision.timestamp.isoformat(), decision.setup_family.value,
                 decision.setup_state.value, decision.quality.value, decision.direction,
                 decision.market_price, decision.trigger, decision.invalidation, decision.target_1,
                 decision.target_2, f5.vwap, decision.structure_5m.value, decision.structure_15m.value,
                 f5.relative_volume, decision.atr, json.dumps(levels, sort_keys=True),
                 json.dumps(parameters.as_dict(), sort_keys=True), fingerprint,
                 decision.engine_version or ENGINE_VERSION,
                 decision.setup_rules_version or SETUP_RULES_VERSION,
                 decision.parameter_version or PARAMETER_VERSION))
            self._update_outcomes(db, decision.symbol, bars)

    @staticmethod
    def _update_outcomes(db: sqlite3.Connection, symbol: str, bars: list[Bar]) -> None:
        rows = db.execute("""SELECT id,timestamp,direction,price,target_1,target_2,invalidation,mfe,mae
                             FROM signals WHERE symbol=?""", (symbol,)).fetchall()
        for signal_id, timestamp, direction, entry, target1, target2, invalidation, mfe, mae in rows:
            future = [b for b in bars if b.timestamp.isoformat() > timestamp]
            if not future:
                continue
            for bar in future:
                db.execute("INSERT OR IGNORE INTO signal_prices VALUES (?,?,?,?)",
                           (signal_id, bar.timestamp.isoformat(), bar.high, bar.low))
            favorable = max(b.high-entry for b in future) if direction == "LONG" else max(entry-b.low for b in future)
            adverse = max(entry-b.low for b in future) if direction == "LONG" else max(b.high-entry for b in future)
            def hit(value, kind):
                if value is None: return None
                return next((b.timestamp.isoformat() for b in future if
                    (b.high >= value if (direction == "LONG") == (kind == "target") else b.low <= value)), None)
            t1, t2, inv = hit(target1, "target"), hit(target2, "target"), hit(invalidation, "invalidation")
            db.execute("""UPDATE signals SET mfe=?,mae=?,target_1_reached=?,target_2_reached=?,
                invalidation_reached=?,target_1_time=COALESCE(target_1_time,?),target_2_time=COALESCE(target_2_time,?),
                invalidation_time=COALESCE(invalidation_time,?) WHERE id=?""",
                (max(mfe, favorable), max(mae, adverse), bool(t1), bool(t2), bool(inv), t1, t2, inv, signal_id))
