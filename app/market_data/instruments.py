"""Bounded first-phase instrument registry."""
SYMBOLS = ('QQQ', 'SPY', 'IWM')
POLICY_VERSION = 'etf-rth-v1'


def instrument(symbol):
    if symbol not in SYMBOLS:
        raise ValueError('UNSUPPORTED_INSTRUMENT')
    return dict(canonical_id=f'ETF:US:{symbol}', provider_symbol=symbol, asset_class='ETF', currency='USD')
