"""All finite-value-set concepts in the system, as enums.

Design rule 8 ("use enums instead of arbitrary strings wherever the value has
a finite known set") applies throughout this package — this module is the
single place those value sets are defined.

Direction vs. Decision — the mechanism that makes NEUTRAL structurally unable
to leak into a final decision:

    Direction has three members: LONG, SHORT, NEUTRAL.
    Decision  has four members:  LONG, SHORT, WAIT, NO_TRADE.

Direction is used everywhere a directional *lean* is recorded before a final
call is made (regime reads, individual evidence items, category scores) --
NEUTRAL is a legitimate, informative value in those contexts (see
confluence.py's EvidenceItem for the specific reasoning).

Decision is used in exactly one place downstream (SignalDecision.decision and
SignalRecord.decision) and simply has no NEUTRAL member at all -- there is no
value to assign even if something tried. Where a Direction must also be
recorded alongside a Decision (SignalDecision.direction, SignalRecord.direction),
those fields are validated in their own modules to allow only LONG/SHORT
(matching a LONG/SHORT decision) or None (for WAIT/NO_TRADE) -- Direction.NEUTRAL
is rejected there by construction, not by a special-cased check (see the
__post_init__ methods in decision.py and signal_record.py).
"""
from enum import Enum


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class Direction(str, Enum):
    """A directional lean. Valid everywhere EXCEPT as a final SignalRecord
    decision -- see module docstring above."""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class Decision(str, Enum):
    """The only four values a completed analysis may resolve to.

    Deliberately has no NEUTRAL member. Per the approved specification:
      WAIT     = a setup is forming but confirmation is missing.
      NO_TRADE = no valid setup OR a critical gate/risk/data failure.
    """
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


class DataQualityState(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class RegimeType(str, Enum):
    STRONG_UPTREND = "STRONG_UPTREND"
    UPTREND = "UPTREND"
    RANGE = "RANGE"
    DOWNTREND = "DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class SetupType(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    PULLBACK = "PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    REVERSAL = "REVERSAL"
    RANGE_MEAN_REVERSION = "RANGE_MEAN_REVERSION"


class EvidenceCategory(str, Enum):
    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    STRUCTURE = "STRUCTURE"
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    HTF = "HTF"          # a weighted evidence category, NOT an automatic hard
                          # blocker (approved decision #10) -- enforced in the
                          # future quality_gate/ phase, not here
    FLOW = "FLOW"
    SENTIMENT = "SENTIMENT"


class QualityGrade(str, Enum):
    A = "A"   # strong setup
    B = "B"   # acceptable setup
    C = "C"   # weak / marginal
    F = "F"   # fails the quality gate outright
