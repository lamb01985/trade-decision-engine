from app.models import Bar


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def session_vwap(bars: list[Bar]) -> float:
    total_volume = sum(bar.volume for bar in bars)
    if not bars or total_volume <= 0:
        raise ValueError("VWAP requires bars with positive total volume")
    return sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars) / total_volume


def average_true_range(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(max(current.high-current.low, abs(current.high-previous.close), abs(current.low-previous.close)))
    sample = ranges[-period:]
    return sum(sample) / len(sample) if sample else None
