"""Read-only access to validated Phase 2 artifacts. Never initializes the store."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

router = APIRouter(prefix="/api/v1/market-intelligence", tags=["Market intelligence"])


class SymbolEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    source_timestamp: AwareDatetime


class OptionsEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    source_cutoff: AwareDatetime
    positioning: dict[str, Any]
    unusual_activity: dict[str, Any]
    trade_flow: dict[str, Any]
    volatility: dict[str, Any]
    gamma: dict[str, Any]


class MarketReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["market-intelligence-v1"]
    report_id: str
    report_date: date
    generated_at: AwareDatetime
    observation_cutoff: AwareDatetime
    market_regime: dict[str, Any]
    daily_features: dict[str, SymbolEvidence]
    intraday_features: dict[str, Any]
    intraday_playbook: list[SymbolEvidence]
    swing_playbook: list[SymbolEvidence]
    options_intelligence: list[dict[str, Any]]
    options_flow_intelligence: list[OptionsEvidence]
    data_quality: dict[str, Any]
    data_errors: list[Any]


class ReportResult(BaseModel):
    report: MarketReport
    retrieved_at: AwareDatetime
    freshness: Literal["RECENT", "STALE", "FUTURE_TIMESTAMP"]
    freshness_policy: str = "Observation cutoff older than 24 hours is stale; this is not a live quote feed."


class ReportHistory(BaseModel):
    reports: list[ReportResult]
    limit: int
    offset: int


def read_reports(limit: int, offset: int = 0) -> list[ReportResult]:
    path = Path(os.environ.get("MARKET_INTELLIGENCE_DB", "data/market_intelligence.sqlite3")).resolve()
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT report_id, report_json FROM intelligence_reports ORDER BY generated_at DESC, report_id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        now = datetime.now(timezone.utc)
        results = []
        for report_id, content in rows:
            raw = json.loads(content)
            raw["report_id"] = report_id  # Phase 2 stores the payload before assigning its ID.
            report = MarketReport.model_validate(raw)
            age = (now - report.observation_cutoff).total_seconds()
            results.append(ReportResult(report=report, retrieved_at=now,
                                       freshness="FUTURE_TIMESTAMP" if age < 0 else "STALE" if age > 86400 else "RECENT"))
        return results
    except (sqlite3.Error, ValueError, ValidationError):
        raise HTTPException(503, "Market intelligence artifact is unavailable or invalid") from None


@router.get("/latest", response_model=ReportResult)
def latest_report():
    rows = read_reports(1)
    if not rows:
        raise HTTPException(404, "No market intelligence report available")
    return rows[0]


@router.get("/history", response_model=ReportHistory)
def report_history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return ReportHistory(reports=read_reports(limit, offset), limit=limit, offset=offset)
