"""Smart Trade Analyzer -- Opportunity UI.

The only Streamlit-importing file in this project. Two tabs:

  Analyze -- one symbol, picked from a live, searchable Bitget symbol
             list (falls back to manual entry if live discovery fails),
             through the real scanner.scan_symbol() path.
  Scanner -- many symbols at once, discovered live from Bitget, each run
             through the SAME scan_symbol() path via scanner.scan_market()
             -- pure orchestration, no independent LONG/SHORT logic of
             its own (see scanner/multi_scan.py's own module docstring).

No mock data, no hardcoded symbol list, no UI-side trading rules anywhere
in this file. See ui/display_model.py, ui/scanner_display.py, and
ui/symbol_search.py for the (Streamlit-free, independently unit-tested)
logic this file only renders.

Run with:  streamlit run app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smart_trade_analyzer.contracts import MarketType, Timeframe  # noqa: E402
from smart_trade_analyzer.data import BitgetMarketDataSource, discover_tradable_instruments  # noqa: E402
from smart_trade_analyzer.scanner import (  # noqa: E402
    filter_actionable_only, filter_by_min_quality_score, scan_market, scan_symbol, sort_scan_results,
)
from smart_trade_analyzer.ui import (  # noqa: E402
    OpportunityDisplayModel, build_display_model, build_scan_result_rows, default_symbol_index,
    derive_display_symbol, normalize_pair, search_instruments, validate_pair_input,
)
from smart_trade_analyzer.ui.formatting import NOT_AVAILABLE  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opportunity_ui")

st.set_page_config(page_title="Smart Trade Analyzer — Opportunity", page_icon="📈", layout="centered")

_DECISION_STYLE = {
    "LONG": st.success,
    "SHORT": st.error,   # red for SHORT is the conventional trading-UI color, not an error signal
    "WAIT": st.warning,
    "NO_TRADE": None,    # neutral -- no colored box; NO_TRADE is neither good nor bad news
}

_TIMEFRAME_OPTIONS = [tf.value for tf in Timeframe]
_DEFAULT_TIMEFRAME_INDEX = _TIMEFRAME_OPTIONS.index("15m") if "15m" in _TIMEFRAME_OPTIONS else 0
_INSTRUMENT_CACHE_TTL_SECONDS = 300  # Section: Caching -- "a small, explicit cache with a reasonable TTL"


def _na(value):
    """Render None as an honest "Not available" rather than a blank,
    a 0, or any other guessed stand-in."""
    return value if value is not None else NOT_AVAILABLE


@st.cache_data(ttl=_INSTRUMENT_CACHE_TTL_SECONDS, show_spinner=False)
def _cached_discover_instruments(market_type_value: str):
    """Cached at the UI layer only -- data/discovery.py's own function
    stays plain and cache-free (Section: Caching -- "prefer caching
    instrument metadata only"; this is exactly that, and nothing about
    an actual analysis result is ever cached here). Keyed by the plain
    string value (not the MarketType enum) since that is what
    st.cache_data hashes most predictably. A failure here is NOT cached
    by Streamlit (exceptions bypass st.cache_data's store), so a
    transient discovery failure retries on the very next call rather
    than staying "cached broken" for the TTL window.
    """
    market_type = MarketType(market_type_value)
    source = BitgetMarketDataSource(market_type=market_type)
    return discover_tradable_instruments(source)


def _safe_discover_instruments(market_type: MarketType):
    """Returns (instruments, error_message) -- error_message is None on
    success. Never raises and never fabricates a symbol list on failure
    (Section: Error Handling -- "if Bitget instrument discovery fails,
    show a clear user-facing error; do not fabricate symbols")."""
    try:
        return _cached_discover_instruments(market_type.value), None
    except Exception:
        logger.exception("instrument discovery failed for market_type=%s", market_type.value)
        return [], (
            f"Could not load the live {market_type.value} symbol list from Bitget right now "
            f"(the exchange may be temporarily unreachable). You can still analyze a symbol manually below."
        )


# ---------------------------------------------------------------------------
# Shared single-result rendering -- used by BOTH the Analyze tab and the
# Scanner tab's "inspect a symbol" detail view, so a result looks and
# behaves identically no matter which tab produced it.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Analyze tab.
# ---------------------------------------------------------------------------

def run_analysis(pair_raw: str, timeframe_value: str, market_type: MarketType) -> None:
    """The only place scan_symbol() is called for the Analyze tab.
    Populates st.session_state with either a display model or a
    user-facing error string -- never both, never a fabricated
    placeholder result while working.
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
            # Never expose a raw traceback to the user -- but keep the
            # full trace in the application's own log output for whoever
            # is running/debugging `streamlit run app.py`.
            logger.exception("scan_symbol failed for pair=%s timeframe=%s market_type=%s",
                              pair, timeframe_value, market_type.value)
            st.session_state["ui_error"] = (
                f"Something went wrong while analyzing {pair}. This could mean the symbol "
                f"isn't listed on Bitget, or the exchange is temporarily unreachable. "
                f"Please check the symbol and try again."
            )
            st.session_state["ui_result"] = None


def render_analyze_tab() -> None:
    market_choice = st.radio("Market", options=["Spot", "Futures"], horizontal=True, key="analyze_market_choice")
    market_type = MarketType.FUTURES if market_choice == "Futures" else MarketType.SPOT

    instruments, discovery_error = _safe_discover_instruments(market_type)

    selected_pair = None
    if instruments:
        search_query = st.text_input("Search symbol", placeholder="e.g. BTC, ETH, SOL, DOGE",
                                      key="analyze_search_query")
        matches = search_instruments(instruments, search_query, limit=300)
        if matches:
            options = [i.symbol for i in matches]
            selected_pair = st.selectbox("Symbol", options=options, index=default_symbol_index(matches),
                                          key="analyze_symbol_select")
        else:
            st.caption(f'No tradable {market_choice.lower()} symbols match "{search_query}".')
    else:
        if discovery_error:
            st.warning(discovery_error)
        selected_pair = st.text_input("Symbol (manual entry)", value="BTCUSDT", key="analyze_manual_symbol")

    col_tf, col_btn = st.columns([2, 1])
    with col_tf:
        timeframe_choice = st.selectbox("Timeframe", options=_TIMEFRAME_OPTIONS, index=_DEFAULT_TIMEFRAME_INDEX,
                                         key="analyze_timeframe")
    with col_btn:
        st.write("")  # vertical alignment spacer
        analyze_clicked = st.button("Analyze", type="primary", use_container_width=True, key="analyze_button")

    if analyze_clicked:
        if not selected_pair:
            st.session_state["ui_error"] = "Please choose or enter a symbol."
            st.session_state["ui_result"] = None
        else:
            run_analysis(selected_pair, timeframe_choice, market_type)

    st.divider()

    if st.session_state.get("ui_error"):
        st.error(st.session_state["ui_error"])
    elif st.session_state.get("ui_result") is not None:
        render_result(st.session_state["ui_result"])
    else:
        st.info("Pick a symbol and timeframe above, then press **Analyze**.")


# ---------------------------------------------------------------------------
# Scanner tab.
# ---------------------------------------------------------------------------

def run_scan(market_type: MarketType, timeframe_value: str, max_symbols: int) -> None:
    """The only place scan_market() is called. Discovery happens here
    too (not cached separately from the Analyze tab's copy -- both call
    the same _cached_discover_instruments, so a Spot list fetched from
    either tab is reused by the other within the TTL window)."""
    timeframe = Timeframe(timeframe_value)
    instruments, discovery_error = _safe_discover_instruments(market_type)
    if not instruments:
        st.session_state["scan_error"] = discovery_error or "No tradable symbols were found for this market."
        st.session_state["scan_result"] = None
        return

    with st.spinner(f"Scanning up to {max_symbols} {market_type.value} symbols ({timeframe.value})… this can take a little while."):
        try:
            source = BitgetMarketDataSource(market_type=market_type)
            result = scan_market(
                source, instruments, market_type, timeframe, max_symbols=max_symbols,
                log=lambda message: logger.info(message),
            )
            st.session_state["scan_result"] = result
            st.session_state["scan_error"] = None
        except Exception:
            logger.exception("scan_market failed for market_type=%s timeframe=%s", market_type.value, timeframe_value)
            st.session_state["scan_error"] = (
                f"Something went wrong while scanning the {market_type.value} market. Please try again in a moment."
            )
            st.session_state["scan_result"] = None


def render_scanner_tab() -> None:
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        market_choice = st.radio("Market", options=["Spot", "Futures"], horizontal=True, key="scan_market_choice")
    market_type = MarketType.FUTURES if market_choice == "Futures" else MarketType.SPOT
    with col2:
        timeframe_choice = st.selectbox("Timeframe", options=_TIMEFRAME_OPTIONS, index=_DEFAULT_TIMEFRAME_INDEX,
                                         key="scan_timeframe")
    with col3:
        max_symbols = st.number_input("Max symbols", min_value=5, max_value=150, value=30, step=5,
                                       key="scan_max_symbols",
                                       help="Caps how many symbols are scanned, to keep scan time and API load reasonable.")

    scan_clicked = st.button("Scan Market", type="primary", use_container_width=True, key="scan_button")
    if scan_clicked:
        run_scan(market_type, timeframe_choice, int(max_symbols))

    st.divider()

    if st.session_state.get("scan_error"):
        st.error(st.session_state["scan_error"])
        return

    scan_result = st.session_state.get("scan_result")
    if scan_result is None:
        st.info("Choose a market and timeframe above, then press **Scan Market**.")
        return

    if scan_result.failures:
        shown = ", ".join(f.pair for f in scan_result.failures[:10])
        more = f" (+{len(scan_result.failures) - 10} more)" if len(scan_result.failures) > 10 else ""
        st.caption(f"⚠️ {len(scan_result.failures)} of {scan_result.scanned_count} symbol(s) could not be scanned: {shown}{more}")

    show_all = st.checkbox("Show WAIT / NO_TRADE", key="scan_show_all")
    use_min_quality = st.checkbox("Set a minimum quality score", key="scan_use_min_quality")
    min_quality_score = None
    if use_min_quality:
        min_quality_score = st.slider("Minimum quality score", min_value=0, max_value=100, value=60,
                                       key="scan_min_quality_value")

    results = scan_result.results if show_all else filter_actionable_only(scan_result.results)
    results = filter_by_min_quality_score(results, min_quality_score)
    results = sort_scan_results(results)

    st.caption(
        f"Showing {len(results)} of {scan_result.scanned_count} scanned symbol(s) "
        f"(sorted: actionable first, then quality score — this is display order only, not a ranking of trade quality)."
    )

    if not results:
        st.caption("No results match the current filters.")
        return

    rows = build_scan_result_rows(results)
    table_data = [
        {
            "Symbol": row.pair, "Decision": row.decision, "Direction": row.direction or "—",
            "Setup": row.setup_family or "—",
            "Quality": f"{row.quality_score} ({row.quality_grade})" if row.quality_score else "—",
            "Entry": row.entry or "—", "SL": row.stop_loss or "—", "TP1": row.take_profit_1 or "—",
            "R:R": row.risk_reward or "—", "Regime": row.market_regime or "—",
        }
        for row in rows
    ]
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    st.markdown("##### Inspect a symbol")
    pair_options = [r.pair for r in results]
    inspect_pair = st.selectbox("Choose a symbol to see full details", options=pair_options, key="scan_inspect_select")
    if inspect_pair:
        selected_result = next((r for r in results if r.pair == inspect_pair), None)
        if selected_result is not None:
            render_result(build_display_model(selected_result))


def main() -> None:
    st.title("Smart Trade Analyzer")
    st.caption("Every result below comes from the real analytical pipeline; nothing here is simulated.")

    analyze_tab, scanner_tab = st.tabs(["Analyze", "Scanner"])
    with analyze_tab:
        render_analyze_tab()
    with scanner_tab:
        render_scanner_tab()


main()
