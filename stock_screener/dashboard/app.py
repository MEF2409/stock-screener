"""Streamlit dashboard for stock screener."""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.data.fetcher import get_ohlcv
from stock_screener.data.bulk_refresh import refresh_all_ohlcv
from stock_screener.indicators.indicators import enrich_ohlcv_with_indicators
from stock_screener.universe.builder import get_universe, update_universe
from stock_screener.scanners.scanners import (
    scan_runaway_gap,
    scan_bullish_divergence,
    scan_bearish_divergence,
    scan_gap_up_normal_volume,
)


def format_currency(val):
    """Format a float as USD."""
    if pd.isna(val):
        return "—"
    return f"${val:,.2f}"


def format_number(val):
    """Format a number with thousands separator."""
    if pd.isna(val):
        return "—"
    return f"{val:,.0f}"


def create_price_chart(df: pd.DataFrame) -> go.Figure:
    """Create an interactive price chart with MAs."""
    fig = go.Figure()

    # Add candlestick-like lines for OHLC
    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["Close"],
        mode="lines",
        name="Close",
        line=dict(color="royalblue", width=2),
    ))

    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["MA_50"],
        mode="lines",
        name="MA(50)",
        line=dict(color="orange", width=1, dash="dash"),
    ))

    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["MA_100"],
        mode="lines",
        name="MA(100)",
        line=dict(color="green", width=1, dash="dash"),
    ))

    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["MA_200"],
        mode="lines",
        name="MA(200)",
        line=dict(color="red", width=1, dash="dash"),
    ))

    fig.update_layout(
        title="Price with Moving Averages",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        hovermode="x unified",
        height=400,
    )

    return fig


def create_rsi_chart(df: pd.DataFrame) -> go.Figure:
    """Create an RSI chart."""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["RSI_14"],
        mode="lines",
        name="RSI(14)",
        line=dict(color="purple", width=2),
    ))

    # Overbought / oversold zones
    fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="Overbought (70)")
    fig.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="Oversold (30)")

    fig.update_layout(
        title="RSI(14)",
        xaxis_title="Date",
        yaxis_title="RSI",
        hovermode="x unified",
        height=300,
        yaxis=dict(range=[0, 100]),
    )

    return fig


def create_volume_chart(df: pd.DataFrame) -> go.Figure:
    """Create a volume chart."""
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df["Date"],
        y=df["Volume"],
        name="Volume",
        marker=dict(color="lightblue"),
    ))

    fig.add_trace(go.Scatter(
        x=df["Date"],
        y=df["Avg_Volume_30d"],
        mode="lines",
        name="Avg(30d)",
        line=dict(color="red", width=2),
    ))

    fig.update_layout(
        title="Volume",
        xaxis_title="Date",
        yaxis_title="Volume",
        hovermode="x unified",
        height=300,
    )

    return fig


def detail_view(ticker: str):
    """Show detailed view for a stock with charts."""
    st.header(f"Stock Detail: {ticker}")

    try:
        df = get_ohlcv(ticker)
        if df.empty:
            st.error(f"No data found for {ticker}")
            return

        df = enrich_ohlcv_with_indicators(df)

        # Summary stats
        col1, col2, col3, col4 = st.columns(4)
        latest = df.iloc[-1]

        with col1:
            st.metric("Close", format_currency(latest["Close"]))
        with col2:
            st.metric("RSI(14)", f"{latest['RSI_14']:.2f}")
        with col3:
            st.metric("Volume", format_number(latest["Volume"]))
        with col4:
            st.metric("Avg Vol(30d)", format_number(latest["Avg_Volume_30d"]))

        # Charts
        st.plotly_chart(create_price_chart(df.tail(100)), width='stretch')
        st.plotly_chart(create_rsi_chart(df.tail(100)), width='stretch')
        st.plotly_chart(create_volume_chart(df.tail(100)), width='stretch')

        # Raw data table (last 10 days)
        st.subheader("Recent OHLCV Data")
        display_df = df.tail(10)[["Date", "Open", "High", "Low", "Close", "Volume", "RSI_14"]].copy()
        display_df["Open"] = display_df["Open"].apply(format_currency)
        display_df["High"] = display_df["High"].apply(format_currency)
        display_df["Low"] = display_df["Low"].apply(format_currency)
        display_df["Close"] = display_df["Close"].apply(format_currency)
        display_df["Volume"] = display_df["Volume"].apply(format_number)
        display_df["RSI_14"] = display_df["RSI_14"].apply(lambda x: f"{x:.2f}" if not pd.isna(x) else "—")

        st.dataframe(display_df, width='stretch', hide_index=True)

    except Exception as e:
        st.error(f"Error loading details for {ticker}: {str(e)}")


def scanner_results_table(scanner_name: str, results: list):
    """Display results table for a scanner."""
    if not results:
        st.info(f"No flags found for {scanner_name}.")
        return

    # Convert to DataFrame for display
    display_data = []
    for result in results:
        row = {"Ticker": result["ticker"]}
        if "open" in result:
            row["Open"] = format_currency(result.get("open"))
            row["Close"] = format_currency(result.get("close"))
            row["Volume"] = format_number(result.get("volume"))
        if "rsi" in result:
            row["RSI"] = f"{result['rsi']:.2f}" if not pd.isna(result.get("rsi")) else "—"
        if "low" in result:
            row["Low"] = format_currency(result.get("low"))
        if "high" in result:
            row["High"] = format_currency(result.get("high"))

        display_data.append(row)

    df_display = pd.DataFrame(display_data)
    st.dataframe(df_display, width='stretch', hide_index=True)


def main():
    st.set_page_config(page_title="Stock Screener Dashboard", layout="wide")
    st.title("📈 Stock Screener Dashboard")

    # Initialize database
    init_db()

    # Sidebar: data refresh
    with st.sidebar:
        st.header("Data Management")

        if st.button("🔄 Update Universe"):
            with st.spinner("Updating universe..."):
                update_universe()
            st.success("Universe updated!")

        if st.button("📊 Refresh Data"):
            with st.spinner("Refreshing OHLCV data for all stocks..."):
                refresh_all_ohlcv(days_back=365)
            st.success("Data refreshed!")

        # Stock detail view
        st.header("Detail View")
        ticker_input = st.text_input("Enter ticker to view details (e.g., AAPL):").upper()

    # Main content
    tab_gap, tab_bull_div, tab_bear_div, tab_short = st.tabs([
        "Bull #1 — Runaway Gap",
        "Bull #2 — Bullish Divergence",
        "Bear #1 — Bearish Divergence",
        "Bear #2 — Gap Up Normal Volume",
    ])

    universe = get_universe()
    tickers = universe["ticker"].tolist()

    # Tab 1: Runaway Gap
    with tab_gap:
        st.header("Bull #1 — Runaway Gap")
        st.markdown("""
        **Setup**: Gap up on heavy volume
        - Today's open > yesterday's close (gap up)
        - Today's volume ≥ 1.3 × 30-day average daily volume
        """)

        with st.spinner("Scanning..."):
            results = []
            for ticker in tickers:
                result = scan_runaway_gap(ticker)
                if result["flagged"]:
                    results.append(result)

        st.write(f"**{len(results)} flags found**")
        scanner_results_table("Runaway Gap", results)

    # Tab 2: Bullish Divergence
    with tab_bull_div:
        st.header("Bull #2 — Bullish Divergence")
        st.markdown("""
        **Setup**: New 52-week low, but RSI makes a higher low
        - Today's price is at a new 52-week low
        - Today's RSI(14) > RSI on the date of the previous 52-week low
        """)

        with st.spinner("Scanning..."):
            results = []
            for ticker in tickers:
                result = scan_bullish_divergence(ticker)
                if result["flagged"]:
                    results.append(result)

        st.write(f"**{len(results)} flags found**")
        scanner_results_table("Bullish Divergence", results)

    # Tab 3: Bearish Divergence
    with tab_bear_div:
        st.header("Bear #1 — Bearish Divergence")
        st.markdown("""
        **Setup**: New 52-week high, but RSI makes a lower high
        - Today's price is at a new 52-week high
        - Today's RSI(14) < RSI on the date of the previous 52-week high
        """)

        with st.spinner("Scanning..."):
            results = []
            for ticker in tickers:
                result = scan_bearish_divergence(ticker)
                if result["flagged"]:
                    results.append(result)

        st.write(f"**{len(results)} flags found**")
        scanner_results_table("Bearish Divergence", results)

    # Tab 4: Gap Up Normal Volume
    with tab_short:
        st.header("Bear #2 — Gap Up on Normal Volume")
        st.markdown("""
        **Setup**: Gap up but on light volume, below key moving averages (gap fill candidate)
        - Today's open > yesterday's close (gap up)
        - Today's volume < 1.3 × 30-day average (normal range)
        - Today's open < 50-day AND 100-day AND 200-day moving averages
        """)

        with st.spinner("Scanning..."):
            results = []
            for ticker in tickers:
                result = scan_gap_up_normal_volume(ticker)
                if result["flagged"]:
                    results.append(result)

        st.write(f"**{len(results)} flags found**")
        scanner_results_table("Gap Up Normal Volume", results)

    # Detail view modal
    if ticker_input:
        st.divider()
        detail_view(ticker_input)


if __name__ == "__main__":
    main()
