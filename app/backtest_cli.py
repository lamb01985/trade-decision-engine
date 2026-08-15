"""CLI for the non-optimizing production-engine baseline backtest."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.backtesting import (
    BacktestConfig,
    BacktestPeriods,
    DateRange,
    ExecutionAssumptions,
    run_and_write_report,
)
from app.config import settings
from app.market_data.massive import MassiveMarketDataProvider


DEFAULT_SYMBOLS = ("QQQ", "SPY", "IWM", "NVDA", "AMD", "META")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use ISO date format YYYY-MM-DD") from exc


def _symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))
    if not symbols:
        raise argparse.ArgumentTypeError("Provide at least one comma-separated symbol")
    return symbols


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a non-optimizing, event-driven baseline through the production StandardDecision engine."
    )
    parser.add_argument("--symbols", type=_symbols, default=DEFAULT_SYMBOLS)
    parser.add_argument("--dev-start", required=True, type=_parse_date)
    parser.add_argument("--dev-end", required=True, type=_parse_date)
    parser.add_argument("--validation-start", required=True, type=_parse_date)
    parser.add_argument("--validation-end", required=True, type=_parse_date)
    parser.add_argument("--holdout-start", required=True, type=_parse_date)
    parser.add_argument("--holdout-end", required=True, type=_parse_date)
    parser.add_argument("--include-final-holdout", action="store_true",
                        help="Evaluate the holdout only after rules and assumptions are frozen.")
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--fee-bps-per-side", type=float, default=0.0)
    parser.add_argument("--target-1-fraction", type=float, default=0.5)
    parser.add_argument("--same-bar-policy", choices=("CONSERVATIVE_STOP", "AMBIGUOUS"), default="CONSERVATIVE_STOP")
    parser.add_argument("--minimum-sample-size", type=int, default=30)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/historical_cache"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtest_reports"))
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> None:
    if not settings.massive_api_key:
        raise SystemExit("MASSIVE_API_KEY is required. Add it to a local .env file before fetching historical data.")
    periods = BacktestPeriods(
        development=DateRange(args.dev_start, args.dev_end),
        validation=DateRange(args.validation_start, args.validation_end),
        final_holdout=DateRange(args.holdout_start, args.holdout_end),
    )
    config = BacktestConfig(
        symbols=args.symbols,
        periods=periods,
        include_final_holdout=args.include_final_holdout,
        minimum_sample_size=args.minimum_sample_size,
        execution=ExecutionAssumptions(
            slippage_bps=args.slippage_bps,
            fee_bps_per_side=args.fee_bps_per_side,
            target_1_fraction=args.target_1_fraction,
            same_bar_policy=args.same_bar_policy,
        ),
    )
    suffix = "with_holdout" if config.include_final_holdout else "development_validation"
    report_json = args.output_dir / f"baseline_{suffix}.json"
    report_markdown = args.output_dir / f"baseline_{suffix}.md"
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    try:
        result = await run_and_write_report(provider, args.cache_dir, config, report_json, report_markdown)
    finally:
        await provider.close()
    print(f"Bars processed: {result.bars_processed}")
    print(f"Confirmed setups: {result.confirmed_setups}")
    print(f"Trades generated: {len(result.generated_trades)}")
    print(f"Ambiguous trades: {len(result.ambiguous_trades)}")
    print(f"JSON report: {report_json}")
    print(f"Markdown report: {report_markdown}")


def main() -> None:
    asyncio.run(_main(parse_args()))


if __name__ == "__main__":
    main()
