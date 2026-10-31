"""In-memory capability evidence; plan labels never establish entitlement."""
from datetime import timedelta


class Capabilities:
    def __init__(self):
        self.evidence = {}

    def observe(self, symbol, family, fields, now, denied=False):
        self.evidence[(symbol, family)] = dict(fields=list(fields), checked=now, denied=denied)

    def feed(self, symbol, now):
        # Only a separately approved, verified deployment probe may establish feed recency.
        return 'UNKNOWN'

    def records(self, profile, now):
        rows = []
        for symbol in ('QQQ', 'SPY', 'IWM', 'VIX', 'NQ', 'ES'):
            for family in ('snapshot', 'bars'):
                evidence = self.evidence.get((symbol, family))
                valid = evidence and now < evidence['checked'] + (timedelta(minutes=15) if evidence['denied'] else timedelta(hours=24))
                rows.append(dict(profile=profile, asset_class='ETF' if symbol in ('QQQ','SPY','IWM') else 'INDEX' if symbol=='VIX' else 'FUTURES',
                    endpoint_family=family, instrument_scope=symbol, field_group='quote' if family=='snapshot' else 'ohlcv', recency_class='UNKNOWN',
                    state=('NOT_ENTITLED' if evidence['denied'] else 'OBSERVED_AVAILABLE') if valid else 'UNKNOWN',
                    evidence_source='DEPLOYMENT_PROBED' if valid else 'PUBLIC_DOCUMENTATION',
                    checked_at=evidence['checked'].isoformat().replace('+00:00','Z') if valid else now.isoformat().replace('+00:00','Z'),
                    expires_at=(evidence['checked']+(timedelta(minutes=15) if evidence['denied'] else timedelta(hours=24))).isoformat().replace('+00:00','Z') if valid else now.isoformat().replace('+00:00','Z'),
                    observed_fields=evidence['fields'] if valid else [], known_quota=None))
        return rows
