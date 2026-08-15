from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from app.versioning import ENGINE_VERSION, SETUP_RULES_VERSION


def signal_fingerprint(*, symbol: str, setup_family: str, direction: str,
                       trigger: float | None, session: date | datetime | str,
                       engine_version: str = ENGINE_VERSION,
                       setup_rules_version: str = SETUP_RULES_VERSION) -> str:
    """Stable identity for one setup thesis; lifecycle state is intentionally excluded."""
    if isinstance(session, str):
        session_date = datetime.fromisoformat(session.replace("Z", "+00:00")).date().isoformat()
    elif isinstance(session, datetime):
        session_date = session.date().isoformat()
    else:
        session_date = session.isoformat()
    identity = [symbol.upper(), setup_family, direction, None if trigger is None else round(trigger, 4),
                session_date, engine_version, setup_rules_version]
    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:24]
    return f"sig_{digest}"

