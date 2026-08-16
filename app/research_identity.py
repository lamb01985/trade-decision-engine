"""Deterministic identifiers for the append-only research measurement ledger."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping

IDENTITY_SCHEMA_VERSION = "setup-instance-v1"
OCCURRENCE_POLICY_VERSION = "occurrence-v1"
CLOSURE_POLICY_VERSION = "closure-v1"
COVERAGE_CONTRACT_VERSION = "scan-coverage-v1"
MEASUREMENT_CONTRACT_VERSION = "measurement-v1"
OBSERVATION_CONTRACT_VERSION = "setup-observation-v1"
FEATURE_DEFINITION_VERSION = "features-v1"
CONTEXT_CONTRACT_VERSION = "market-context-v1"
REGIME_CONTRACT_VERSION = "market-regime-v1"
SETUP_CATALOG_VERSION = "setup-catalog-v1"


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _canonical_anchor(value: Any) -> Any:
    """Normalize structural numeric anchors independent of Python int/float decoding."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    if isinstance(value, Mapping):
        return {str(key): _canonical_anchor(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_anchor(item) for item in value]
    return _canonical(value)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def anchor_key(anchor_type: str, anchor: Mapping[str, Any]) -> str:
    return sha256_hex({"anchor_type": anchor_type, "anchor": _canonical_anchor(anchor)})


def setup_instance_id(*, symbol: str, session_date: date, setup_family: str,
                      direction: str, family_anchor_type: str,
                      family_anchor: Mapping[str, Any],
                      first_qualifying_bar_at: datetime, engine_version: str,
                      setup_rules_version: str, parameter_version: str,
                      identity_schema_version: str = IDENTITY_SCHEMA_VERSION) -> str:
    payload = {
        "identity_schema_version": identity_schema_version,
        "symbol": symbol.upper(),
        "session_date": session_date,
        "setup_family": setup_family,
        "direction": direction,
        "family_anchor_type": family_anchor_type,
        "family_anchor": _canonical_anchor(family_anchor),
        "first_qualifying_bar_at": first_qualifying_bar_at,
        "engine_version": engine_version,
        "setup_rules_version": setup_rules_version,
        "parameter_version": parameter_version,
    }
    return "set_" + sha256_hex(payload)


def universe_hash(symbols: list[str] | tuple[str, ...], universe_version: str) -> str:
    return sha256_hex({"universe_version": universe_version,
                       "symbols": sorted({symbol.upper() for symbol in symbols})})
