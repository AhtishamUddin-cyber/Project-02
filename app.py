"""Smart Trade Analyzer -- Opportunity UI (MVP).

The only Streamlit-importing file in this project. Everything it shows
comes from one real call to scanner.scan_symbol() (Phase 6's public
pipeline entry point) via the real BitgetMarketDataSource adapter -- no
mock data, no hardcoded symbol, no UI-side trading rules. See
ui/display_model.py for the (Streamlit-free, independently unit-tested)
logic that turns the backend's OpportunityResult into what this file
renders.

Run with:  streamlit run app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smart_trade_analyzer.contracts import MarketType, Timeframe  # noqa: E402
from smart_trade_analyzer.data import BitgetMarketDataSource  # noqa: E402
from smart_trade_analyzer.scanner import scan_symbol  # noqa: E402
from smart_trade_analyzer.ui import (  # noqa: E402
    OpportunityDisplayModel, build_display_model, derive_display_symbol, normalize_pair, validate_pair_input,
)
from smart_trade_analyzer.ui.formatting import NOT_AVAILABLE  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opportunity_ui")

st.set_page_config(page_title="Smart Trade Analyzer — Opportunity", page_icon="📈", layout="centered")

_DECISION_STYLE = {
    # (Streamlit container function, whether to show the pending/setup direction as extra context)
    "LONG": st.success,
    "SHORT": st.error,   # red for SHORT is the conventional trading-UI color, not an error signal
    "WAIT": st.warning,
    "NO_TRADE": None,    # neutral -- no colored box; NO_TRADE is neither good nor bad news
}

_TIMEFRAME_OPTIONS = [tf.value for tf in Timeframe]
_DEFAULT_TIMEFRAME_INDEX = _TIMEFRAME_OPTIONS.index("15m") if "15m" in _TIMEFRAME_OPTIONS else 0


def _na(value):
    """Render None as an honest "Not available" rather than a blank,
    a 0, or any other guessed stand-in (Section 7)."""
    return value if value is not None else NOT_AVAILABLE


def run_analysis(pair_raw: str, timeframe_value: str, market_type: MarketType) -> None:
    """The only place scan_symbol() is called. Populates st.session_state
    with either a display model or a user-facing error string -- never
    both, never a fabricated placeholder result while working (Section 5).
    """
    error = validate_pair_input(pair_raw)
    if error:
        st.session_state["ui_error"] = error
        st.session_state["ui_result"] = None
        return

    pair = normalize_pair(pair_raw)
    symbol = derive_display_symbol(pair)
    timeframe = Timeframe(timeframe_value)

    with st.spinner(f"Analyzing {pair} ({timeframe.value})…"):
        try:
            source = BitgetMarketDataSource(market_type=market_type)
            result = scan_symbol(source, symbol=symbol, pair=pair, market_type=market_type, timeframe=timeframe)
            st.session_state["ui_result"] = build_display_model(result)
            st.session_state["ui_error"] = None
        except Exception:
            # Never expose a raw traceback to the user (Section 12) --
            # but keep the full trace in the application's own log output
            # for whoever is running/debugging `streamlit run app.py`.
            logger.exception("scan_symbol failed for pair=%s timeframe=%s market_type=%s",
                              pair, timeframe_value, market_type.value)
            st.session_state["ui_error"] = (
                f"Something went wrong while analyzing {pair}. This could mean the symbol "
                f"isn't listed on Bitget, or the exchange is temporarily unreachable. "
                f"Please check the symbol and try again."
            )
            st.session_state["ui_result"] = None


def render_decision(model: OpportunityDisplayModel) -> None:
    st.subheader(f"{model.symbol} · {model.timeframe} · {model.market_type.title()}")
    box = _DECISION_STYLE.get(model.decision)
    headline = f"**{model.decision_headline}**"
    if model.direction and model.decision in ("LONG", "SHORT"):
        headline += f" — {model.direction}"
    if box is not None:
        box(headline)
    else:
        st.markdown(f"### {headline}")

    if model.decision == "WAIT":
        st.caption(
            "Setup exists, but current conditions do not satisfy all requirements for an actionable trade."
            + (f" The pending setup leans **{model.pending_direction}**." if model.pending_direction else "")
        )
    elif model.decision == "NO_TRADE":
        st.caption("No actionable trade opportunity right now.")

    meta_cols = st.columns(3)
    meta_cols[0].metric("Setup family", _na(model.setup_family))
    meta_cols[1].metric("Quality", f"{model.quality_score} ({model.quality_grade})" if model.has_quality else NOT_AVAILABLE)
    meta_cols[2].metric("Regime", _na(model.market_regime))
    if model.has_quality:
        st.caption("Quality is this project's own analytical setup score (0–100) and grade — not a probability, win rate, or confidence percentage.")


def render_trade_plan(model: OpportunityDisplayModel) -> None:
    st.markdown("#### Trade Plan")
    if not model.has_trade_plan:
        st.caption("No trade plan is available for this result.")
        return
    row1 = st.columns(3)
    row1[0].metric("Entry", model.entry or NOT_AVAILABLE)
    row1[1].metric("Stop Loss", model.stop_loss or NOT_AVAILABLE)
    row1[2].metric("R : R", model.risk_reward or NOT_AVAILABLE)
    row2 = st.columns(3)
    row2[0].metric("TP1", model.take_profit_1 or NOT_AVAILABLE)
    row2[1].metric("TP2", model.take_profit_2 or NOT_AVAILABLE)
    row2[2].metric("Invalidation", model.invalidation_price or NOT_AVAILABLE)
    st.caption(
        f"Entry zone: {_na(model.entry_zone)}  ·  Confirmation price: {_na(model.confirmation_price)}"
    )


def render_reasons_and_warnings(model: OpportunityDisplayModel) -> None:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Why this setup?")
        if model.reasons:
            for reason in model.reasons:
                st.markdown(f"- {reason}")
        else:
            st.caption(NOT_AVAILABLE)
    with col2:
        st.markdown("#### Warnings")
        if model.warnings:
            for warning in model.warnings:
                st.markdown(f"- {warning}")
        else:
            st.caption("None.")


def render_technical_metadata(model: OpportunityDisplayModel) -> None:
    with st.expander("Technical metadata"):
        st.write(f"**Signal timestamp:** {_na(model.signal_timestamp)}")
        if model.is_stale is None:
            st.write("**Freshness:** Not available")
        elif model.is_stale:
            st.write("**Freshness:** ⚠️ Stale — conditions may have changed since this was generated.")
        else:
            st.write("**Freshness:** Fresh")
        st.write(f"**Data quality:** {model.data_quality_state}")
        if model.data_quality_reasons:
            for reason in model.data_quality_reasons:
                st.write(f"- {reason}")


def render_result(model: OpportunityDisplayModel) -> None:
    render_decision(model)
    st.divider()
    render_trade_plan(model)
    st.divider()
    render_reasons_and_warnings(model)
    st.divider()
    render_technical_metadata(model)


def main() -> None:
    st.title("Smart Trade Analyzer")
    st.caption("Opportunity scanner — every result below comes from the real analytical pipeline; nothing here is simulated.")

    with st.form("analyze_form"):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            pair_input = st.text_input("Symbol", value=st.session_state.get("ui_pair_input", "BTCUSDT"),
                                        placeholder="e.g. BTCUSDT")
        with col2:
            timeframe_choice = st.selectbox("Timeframe", options=_TIMEFRAME_OPTIONS, index=_DEFAULT_TIMEFRAME_INDEX)
        with col3:
            market_choice = st.selectbox("Market", options=["Spot", "Futures"], index=0)
        submitted = st.form_submit_button("Analyze", type="primary", use_container_width=True)

    if submitted:
        st.session_state["ui_pair_input"] = pair_input
        market_type = MarketType.FUTURES if market_choice == "Futures" else MarketType.SPOT
        run_analysis(pair_input, timeframe_choice, market_type)

    st.divider()

    if st.session_state.get("ui_error"):
        st.error(st.session_state["ui_error"])
    elif st.session_state.get("ui_result") is not None:
        render_result(st.session_state["ui_result"])
    else:
        st.info("Enter a symbol and timeframe above, then press **Analyze**.")


main()
