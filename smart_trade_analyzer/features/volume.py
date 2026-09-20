"""Volume-derived features -- kept separate from indicators.py's price-based
indicators (RSI/EMA/MACD/StochRSI/Bollinger/ATR are all functions of price;
this module's inputs are volume).

Convention verified directly against the existing project's own vol_sig()
closure (analyzer.py, line ~1992) before being written here: current
candle's volume divided by the simple average of the preceding 19 candles'
volume (a 20-candle window, current candle excluded from its own comparison
average).

DEVIATION FROM LEGACY (contract-driven, not a judgment call): the legacy
function returns a categorical string label ("High (Strong move likely)",
"Average", "Low", ...). Phase 1's FeatureSet contract defines
`volume_ratio: Optional[float]` -- a number, not a label. This module
returns the same underlying ratio as a float; categorical labeling (if
wanted) is a presentation concern for a later phase, not this one.
"""
import math
from typing import Optional, Sequence

VOLUME_RATIO_WINDOW = 20  # current candle + 19 preceding


def _all_finite(values: Sequence[float]) -> bool:
    return all(math.isfinite(v) for v in values)


def volume_ratio(volumes: Sequence[float], window: int = VOLUME_RATIO_WINDOW) -> Optional[float]:
    """Current (last) volume divided by the simple average of the preceding
    `window - 1` volumes (current excluded from its own comparison average).

    None if fewer than `window` volumes are available, or if the trailing
    average is zero (division is not meaningful -- not fabricated as 0 or 1),
    or if any non-finite value appears in the window.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    if len(volumes) < window or not _all_finite(volumes):
        return None
    trailing = volumes[-window:-1]
    avg = sum(trailing) / len(trailing)
    if avg == 0:
        return None
    return volumes[-1] / avg
