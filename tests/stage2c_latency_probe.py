"""Small local persistence latency sanity probe; not a benchmark."""
import asyncio
import json
import os
from statistics import median
from time import perf_counter
from datetime import timedelta

from app.models import SetupProgress
from app.parameters import DEFAULT_PARAMETERS
from app.persistence import create_stores
from app.signal_log import NoOpSignalStore
from app.service import AnalysisService
from tests.test_service_v2 import FakeProvider


def milliseconds(samples):
    ordered = sorted(samples)
    return {"median_ms": round(median(ordered), 3),
            "p95_ms": round(ordered[max(0, int(len(ordered) * .95) - 1)], 3),
            "max_ms": round(max(ordered), 3), "samples": len(ordered)}


def main():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url: raise SystemExit("TEST_DATABASE_URL is required")
    stores = create_stores(url)
    base = asyncio.run(AnalysisService(FakeProvider(), signal_logger=NoOpSignalStore()).analyze_symbol("LATENCY")).decision
    insert_times, lookup_times, transition_times, reserve_times, dedup_times = [], [], [], [], []
    for index in range(30):
        decision = base.model_copy(update={"trigger": (base.trigger or base.market_price) + .1 + index * .001})
        started = perf_counter()
        fingerprint = stores.signal.record_decision(decision,
            parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
        insert_times.append((perf_counter() - started) * 1000)
        started = perf_counter(); assert stores.signal.get_signal(fingerprint)
        lookup_times.append((perf_counter() - started) * 1000)
        later = decision.timestamp + timedelta(minutes=5)
        changed = decision.model_copy(update={"setup_state": SetupProgress.CONFIRMED,
            "timestamp": later, "state_changed_at": later})
        started = perf_counter(); stores.signal.record_decision(changed,
            parameter_snapshot=DEFAULT_PARAMETERS.as_dict(), evidence={"levels": []})
        transition_times.append((perf_counter() - started) * 1000)
        event_key = f"stage2c-latency-{fingerprint}"
        started = perf_counter(); assert stores.notification.reserve(event_key, "READY")
        reserve_times.append((perf_counter() - started) * 1000)
        started = perf_counter(); assert not stores.notification.reserve(event_key, "READY")
        dedup_times.append((perf_counter() - started) * 1000)
    stores.signal.close()
    print(json.dumps({"signal_insert": milliseconds(insert_times),
        "signal_lookup": milliseconds(lookup_times),
        "lifecycle_transition": milliseconds(transition_times),
        "notification_reservation": milliseconds(reserve_times),
        "notification_dedup_lookup": milliseconds(dedup_times)}, sort_keys=True))


if __name__ == "__main__": main()
