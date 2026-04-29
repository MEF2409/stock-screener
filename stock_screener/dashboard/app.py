"""Streamlit dashboard for stock screener."""

import json
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

WATCHLISTS_FILE = Path.home() / ".stock-screener" / "watchlists.json"

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.data.fetcher import get_ohlcv
from stock_screener.data.bulk_refresh import refresh_all_ohlcv
from stock_screener.indicators.indicators import enrich_ohlcv_with_indicators
from stock_screener.universe.builder import (
    get_universe, update_universe, add_to_universe, remove_from_universe,
)
from stock_screener.earnings.earnings import update_earnings_calendar
from stock_screener.scanners.scanners import (
    scan_runaway_gap,
    scan_bullish_divergence,
    scan_bearish_divergence,
    scan_gap_up_normal_volume,
)
from stock_screener.backtest.backtest import backtest_scanner, summarize_results
from stock_screener.auth.users import (
    signup as user_signup, list_users, set_status, delete_user,
    is_admin, get_approved_credentials, seed_from_yaml,
)
from stock_screener.trades.trades import (
    add_trade, close_trade, delete_trade as remove_trade,
    list_trades, compute_pnl, grade_closed_trade,
)
from stock_screener.exits import evaluate_exit, SETUP_CHOICES


# Bloomberg-terminal-style palette
ACCENT = "#00d9ff"        # electric cyan
BG = "#0d1117"            # base background
SURFACE = "#161b22"       # raised surface
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
BULL = "#3fb950"
BEAR = "#f85149"
WARN = "#d29922"

# Shared AG Grid styling — kills white default backgrounds, makes sortable headers obvious
GRID_CSS = {
    ".ag-root-wrapper": {"border": f"1px solid {BORDER}", "border-radius": "10px", "overflow": "hidden"},
    ".ag-header": {"background-color": f"{SURFACE} !important", "border-bottom": f"1px solid {BORDER} !important"},
    ".ag-header-cell": {"cursor": "pointer !important", "transition": "background 0.15s ease"},
    ".ag-header-cell:hover": {"background-color": "rgba(0,217,255,0.06) !important"},
    ".ag-header-cell-label": {
        "color": f"{MUTED} !important", "letter-spacing": "0.05em",
        "text-transform": "uppercase", "font-size": "0.72rem", "font-weight": "600",
    },
    ".ag-sort-indicator-container": {"opacity": "0.85 !important", "color": f"{ACCENT} !important"},
    ".ag-sort-none-icon, .ag-sort-ascending-icon, .ag-sort-descending-icon": {"color": f"{ACCENT} !important"},
    # Body / cells — kill default white backgrounds
    ".ag-body-viewport, .ag-center-cols-viewport, .ag-center-cols-container": {"background-color": f"{BG} !important"},
    ".ag-pinned-left-cols-container, .ag-pinned-left-header": {"background-color": f"{SURFACE} !important"},
    ".ag-row": {"background-color": f"{BG} !important", "border-bottom": f"1px solid {BORDER} !important"},
    ".ag-row-even, .ag-row-odd": {"background-color": f"{BG} !important"},
    ".ag-row-hover": {"background-color": "#1a2028 !important"},
    ".ag-row-selected, .ag-row-selected.ag-row-hover": {
        "background-color": "rgba(0,217,255,0.1) !important",
        "border-left": f"3px solid {ACCENT} !important",
    },
    ".ag-cell": {"display": "flex", "align-items": "center", "border-right": "none !important"},
    # Pinned ticker column gets subtle separation
    ".ag-pinned-left-cols-container .ag-cell": {"background-color": f"{SURFACE} !important"},
    ".ag-pinned-left-cols-container .ag-row-hover .ag-cell": {"background-color": "#1c232c !important"},
    ".ag-pinned-left-cols-container .ag-row-selected .ag-cell": {"background-color": "rgba(0,217,255,0.12) !important"},
    # Filter menu / popups
    ".ag-popup, .ag-menu": {"background-color": f"{SURFACE} !important", "border": f"1px solid {BORDER} !important"},
    ".ag-input-field-input": {"background-color": f"{BG} !important", "color": f"{TEXT} !important", "border": f"1px solid {BORDER} !important"},
}


def inject_css():
    css = textwrap.dedent(f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: {TEXT};
    }}
    .stApp {{
        background: radial-gradient(ellipse at top, #11161d 0%, {BG} 60%);
    }}
    .mp-title {{
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        font-size: 2.4rem;
        letter-spacing: -0.02em;
        background: linear-gradient(90deg, {ACCENT} 0%, #4ad8ff 50%, #ffffff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 0.1rem;
    }}
    .mp-subtitle {{
        color: {MUTED};
        font-size: 0.9rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 1.5rem;
    }}
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: {SURFACE};
        padding: 6px;
        border-radius: 12px;
        border: 1px solid {BORDER};
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 8px;
        padding: 8px 18px;
        color: {MUTED};
        font-weight: 500;
        transition: all 0.18s ease;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        color: {TEXT};
        background: rgba(255,255,255,0.03);
    }}
    .stTabs [aria-selected="true"] {{
        background: linear-gradient(135deg, rgba(0,217,255,0.15), rgba(0,217,255,0.05));
        color: {ACCENT} !important;
        border: 1px solid rgba(0,217,255,0.3);
    }}
    .stTabs [data-baseweb="tab-highlight"] {{ display: none; }}
    /* Overview tab — visually separated from scanner tabs */
    .stTabs [data-baseweb="tab-list"] > [data-baseweb="tab"]:first-child {{
        background: rgba(255,255,255,0.04);
        color: {TEXT};
        margin-right: 10px;
        position: relative;
    }}
    .stTabs [data-baseweb="tab-list"] > [data-baseweb="tab"]:first-child::after {{
        content: '';
        position: absolute;
        right: -6px;
        top: 25%;
        height: 50%;
        width: 1px;
        background: {BORDER};
    }}
    .stTabs [data-baseweb="tab-list"] > [data-baseweb="tab"]:first-child[aria-selected="true"] {{
        background: linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
        color: {TEXT} !important;
        border: 1px solid {BORDER};
    }}
    .mp-tile {{
        background: linear-gradient(135deg, {SURFACE} 0%, #1c2128 100%);
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 18px 20px;
        position: relative;
        overflow: hidden;
        transition: all 0.2s ease;
    }}
    .mp-tile:hover {{
        border-color: rgba(0,217,255,0.4);
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,217,255,0.08);
    }}
    .mp-tile::before {{
        content: '';
        position: absolute;
        top: 0; left: 0;
        width: 3px; height: 100%;
        background: linear-gradient(180deg, {ACCENT}, transparent);
    }}
    .mp-tile-label {{
        font-size: 0.72rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: {MUTED};
        margin-bottom: 6px;
    }}
    .mp-tile-value {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.9rem;
        font-weight: 600;
        color: {TEXT};
        line-height: 1.1;
    }}
    .mp-tile-meta {{
        font-size: 0.78rem;
        color: {MUTED};
        margin-top: 4px;
    }}
    .mp-tile-accent {{ color: {ACCENT}; }}
    .mp-tile-bull {{ color: {BULL}; }}
    .mp-tile-bear {{ color: {BEAR}; }}
    .mp-tile-warn {{ color: {WARN}; }}
    /* Responsive stats grid for detail view */
    .mp-stats-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 10px;
        margin: 8px 0 24px 0;
    }}
    .mp-stat {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 12px 14px;
        min-width: 0;
    }}
    .mp-stat-label {{
        font-size: 0.68rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: {MUTED};
        margin-bottom: 4px;
        white-space: nowrap;
    }}
    .mp-stat-value {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.15rem;
        font-weight: 600;
        color: {TEXT};
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .mp-stat-bull .mp-stat-value {{ color: {BULL}; }}
    .mp-stat-bear .mp-stat-value {{ color: {BEAR}; }}
    .mp-stat-warn .mp-stat-value {{ color: {WARN}; }}
    .mp-stat-accent .mp-stat-value {{ color: {ACCENT}; }}
    /* Detail view header */
    .mp-detail-header {{
        display: flex;
        align-items: baseline;
        gap: 14px;
        margin: 8px 0 18px 0;
    }}
    .mp-detail-ticker {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 2rem;
        font-weight: 700;
        color: {ACCENT};
        letter-spacing: 0.04em;
    }}
    .mp-detail-tagline {{
        color: {MUTED};
        font-size: 0.78rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }}
    /* Empty state */
    .mp-empty {{
        background: {SURFACE};
        border: 1px dashed {BORDER};
        border-radius: 12px;
        padding: 32px 20px;
        text-align: center;
        color: {MUTED};
    }}
    .mp-empty-icon {{
        font-size: 2rem;
        margin-bottom: 6px;
        opacity: 0.5;
    }}
    /* Section labels */
    .mp-section-label {{
        color: {MUTED};
        font-size: 0.72rem;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin: 18px 0 10px 0;
    }}
    /* Compare view */
    .mp-compare-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px;
        margin: 8px 0 24px 0;
    }}
    .mp-compare-card {{
        background: linear-gradient(135deg, {SURFACE} 0%, #1c2128 100%);
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 16px 18px;
        position: relative;
        overflow: hidden;
    }}
    .mp-compare-card::before {{
        content: '';
        position: absolute;
        top: 0; left: 0;
        width: 3px; height: 100%;
        background: linear-gradient(180deg, {ACCENT}, transparent);
    }}
    /* Selected ticker chip */
    .mp-selected-chip {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 14px;
        background: linear-gradient(135deg, rgba(0,217,255,0.12), rgba(0,217,255,0.04));
        border: 1px solid rgba(0,217,255,0.35);
        border-radius: 20px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 600;
        color: {ACCENT};
        font-size: 0.85rem;
        letter-spacing: 0.04em;
    }}
    /* Sidebar polish */
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {{
        font-size: 0.75rem !important;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: {MUTED} !important;
        font-weight: 600;
        margin-top: 0.5rem;
    }}
    .mp-scanner-card {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 16px 18px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        transition: all 0.18s ease;
    }}
    .mp-scanner-card:hover {{
        border-color: {ACCENT};
        background: #1a2028;
    }}
    .mp-scanner-name {{
        font-weight: 600;
        font-size: 1rem;
        color: {TEXT};
    }}
    .mp-scanner-desc {{
        font-size: 0.78rem;
        color: {MUTED};
        margin-top: 2px;
    }}
    .mp-scanner-count {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.6rem;
        font-weight: 700;
        color: {ACCENT};
    }}
    .mp-scanner-count-zero {{ color: {MUTED}; }}
    .stButton > button {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.15s ease;
    }}
    .stButton > button:hover {{
        border-color: {ACCENT};
        color: {ACCENT};
        background: rgba(0,217,255,0.05);
    }}
    .stDownloadButton > button {{
        background: rgba(0,217,255,0.08);
        color: {ACCENT};
        border: 1px solid rgba(0,217,255,0.3);
    }}
    .stDownloadButton > button:hover {{
        background: rgba(0,217,255,0.15);
        border-color: {ACCENT};
    }}
    section[data-testid="stSidebar"] {{
        background: {SURFACE};
        border-right: 1px solid {BORDER};
    }}
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    header[data-testid="stHeader"] {{ background: transparent; }}
    ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
    ::-webkit-scrollbar-track {{ background: {BG}; }}
    ::-webkit-scrollbar-thumb {{ background: {BORDER}; border-radius: 5px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: {ACCENT}; }}
    .stMetric [data-testid="stMetricValue"] {{
        font-family: 'JetBrains Mono', monospace !important;
        color: {ACCENT};
    }}
    h1, h2, h3 {{
        letter-spacing: -0.01em;
        font-weight: 600;
    }}
    </style>
    <script>
    // Keyboard shortcuts:
    //   Esc → click sidebar "Clear selection" button
    //   /   → focus the sidebar Filter input
    document.addEventListener('keydown', function(e) {{
        const doc = window.parent.document;
        const tag = (e.target.tagName || '').toLowerCase();
        const isTyping = tag === 'input' || tag === 'textarea';
        if (e.key === 'Escape') {{
            const buttons = doc.querySelectorAll('button');
            for (const b of buttons) {{
                if (b.innerText && b.innerText.includes('Clear selection')) {{
                    b.click();
                    break;
                }}
            }}
        }} else if (e.key === '/' && !isTyping) {{
            const inputs = doc.querySelectorAll('section[data-testid="stSidebar"] input[type="text"]');
            for (const inp of inputs) {{
                if (inp.placeholder && inp.placeholder.includes('press /')) {{
                    e.preventDefault();
                    inp.focus();
                    break;
                }}
            }}
        }}
    }});
    </script>
    """).strip()
    st.markdown(css, unsafe_allow_html=True)


def metric_tile(label: str, value: str, meta: str = "", color: str = "accent") -> str:
    color_class = {
        "accent": "mp-tile-accent",
        "bull": "mp-tile-bull",
        "bear": "mp-tile-bear",
        "warn": "mp-tile-warn",
        "default": "",
    }.get(color, "mp-tile-accent")
    return f"""
    <div class="mp-tile">
        <div class="mp-tile-label">{label}</div>
        <div class="mp-tile-value {color_class}">{value}</div>
        {f'<div class="mp-tile-meta">{meta}</div>' if meta else ''}
    </div>
    """


def scanner_card(name: str, count: int, description: str) -> str:
    count_class = "mp-scanner-count" if count > 0 else "mp-scanner-count mp-scanner-count-zero"
    return f"""
    <div class="mp-scanner-card">
        <div>
            <div class="mp-scanner-name">{name}</div>
            <div class="mp-scanner-desc">{description}</div>
        </div>
        <div class="{count_class}">{count}</div>
    </div>
    """


def detail_stat(label: str, value: str, color: str = "default") -> str:
    color_class = {
        "bull": "mp-stat-bull",
        "bear": "mp-stat-bear",
        "warn": "mp-stat-warn",
        "accent": "mp-stat-accent",
        "default": "",
    }.get(color, "")
    return f'<div class="mp-stat {color_class}"><div class="mp-stat-label">{label}</div><div class="mp-stat-value">{value}</div></div>'


@st.cache_data(ttl=300, show_spinner=False)
def run_all_scanners(tickers_tuple: tuple) -> dict:
    """Run all four scanners across the universe, once. Cached for 5 min.
    Attaches `_sparkline` (30 closes), `_pct_from_high` (% off 52w high),
    `_pct_from_low` (% off 52w low), and `_scanned_at` per result."""
    scanned_at = datetime.now().isoformat()
    out: dict = {"Momentum": [], "Reversal": [], "Caution": [], "Fade": [], "_scanned_at": scanned_at}

    enrichment_cache: dict[str, dict] = {}

    def enrich_for(ticker: str) -> dict:
        if ticker not in enrichment_cache:
            try:
                df = get_ohlcv(ticker)
                if df.empty:
                    enrichment_cache[ticker] = {"sparkline": [], "high_52w": None, "low_52w": None, "close": None}
                else:
                    closes = df["Close"]
                    high_52w = float(df["High"].max())
                    low_52w = float(df["Low"].min())
                    last_close = float(closes.iloc[-1])
                    enrichment_cache[ticker] = {
                        "sparkline": closes.tail(30).tolist(),
                        "high_52w": high_52w,
                        "low_52w": low_52w,
                        "close": last_close,
                    }
            except Exception:
                enrichment_cache[ticker] = {"sparkline": [], "high_52w": None, "low_52w": None, "close": None}
        return enrichment_cache[ticker]

    for ticker in tickers_tuple:
        for name, scan_fn in (
            ("Momentum", scan_runaway_gap),
            ("Reversal", scan_bullish_divergence),
            ("Caution", scan_bearish_divergence),
            ("Fade", scan_gap_up_normal_volume),
        ):
            r = scan_fn(ticker)
            if r["flagged"]:
                e = enrich_for(ticker)
                r["_sparkline"] = e["sparkline"]
                if e["high_52w"] and e["close"]:
                    r["_pct_from_high"] = (e["close"] / e["high_52w"] - 1) * 100
                else:
                    r["_pct_from_high"] = None
                if e["low_52w"] and e["close"]:
                    r["_pct_from_low"] = (e["close"] / e["low_52w"] - 1) * 100
                else:
                    r["_pct_from_low"] = None
                out[name].append(r)
    return out


def to_sparkline_text(values) -> str:
    """Convert a numeric series to Unicode bar chars (▁▂▃▄▅▆▇█)."""
    if not values or len(values) < 2:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = (mx - mn) or 1
    return "".join(chars[min(7, int((v - mn) / rng * 7))] for v in values)


def time_ago(iso_ts: str) -> str:
    """Human-friendly 'X ago' string."""
    try:
        then = datetime.fromisoformat(iso_ts)
        diff = (datetime.now() - then).total_seconds()
        if diff < 60: return "just now"
        if diff < 3600: return f"{int(diff/60)}m ago"
        if diff < 86400: return f"{int(diff/3600)}h ago"
        return f"{int(diff/86400)}d ago"
    except Exception:
        return ""


@st.cache_data(ttl=300, show_spinner=False)
def get_last_refresh_time() -> str | None:
    """Most recent OHLCV date in the DB, formatted."""
    try:
        db_path = Path(__file__).resolve().parents[2] / "db" / "screener.db"
        if not db_path.exists():
            return None
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
        if row and row[0]:
            dt = datetime.strptime(row[0], "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
    except Exception:
        return None
    return None


def load_watchlists() -> dict[str, list[str]]:
    if not WATCHLISTS_FILE.exists():
        return {}
    try:
        return json.loads(WATCHLISTS_FILE.read_text())
    except Exception:
        return {}


def save_watchlists(data: dict[str, list[str]]) -> None:
    WATCHLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLISTS_FILE.write_text(json.dumps(data, indent=2))


def style_plotly(fig: go.Figure, title: str = None) -> go.Figure:
    """Apply Bloomberg-terminal palette to a Plotly figure."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font=dict(family="Inter, sans-serif", color=TEXT, size=12),
        title=dict(text=title, font=dict(family="Inter, sans-serif", color=TEXT, size=14)) if title else None,
        margin=dict(l=20, r=20, t=40 if title else 20, b=30),
        legend=dict(
            bgcolor="rgba(22,27,34,0.7)",
            bordercolor=BORDER,
            borderwidth=1,
            font=dict(size=11),
        ),
        xaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER, linecolor=BORDER),
        yaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER, linecolor=BORDER),
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=ACCENT, font=dict(family="JetBrains Mono, monospace")),
    )
    return fig


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


def create_price_chart(df: pd.DataFrame, signals: list = None) -> go.Figure:
    """signals: list of dicts {'date': str, 'label': str, 'color': str} to mark on the chart."""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["Date"],
        open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="OHLC",
        increasing=dict(line=dict(color=BULL), fillcolor=BULL),
        decreasing=dict(line=dict(color=BEAR), fillcolor=BEAR),
    ))
    fig.add_trace(go.Scatter(x=df["Date"], y=df["MA_50"], mode="lines", name="MA(50)",
                             line=dict(color="#a78bfa", width=1.2)))
    fig.add_trace(go.Scatter(x=df["Date"], y=df["MA_100"], mode="lines", name="MA(100)",
                             line=dict(color="#fb923c", width=1.2)))
    fig.add_trace(go.Scatter(x=df["Date"], y=df["MA_200"], mode="lines", name="MA(200)",
                             line=dict(color="#fbbf24", width=1.2)))

    # Signal annotations — place an arrow + label at the signal date
    if signals:
        for sig in signals:
            sig_date = sig.get("date")
            if not sig_date:
                continue
            try:
                row = df[df["Date"] == sig_date]
                if row.empty:
                    continue
                price = float(row["High"].iloc[0])
                fig.add_annotation(
                    x=sig_date, y=price,
                    text=f"<b>{sig['label']}</b>",
                    showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor=sig.get("color", ACCENT),
                    bgcolor=sig.get("color", ACCENT),
                    font=dict(color="#0d1117", size=11, family="Inter,sans-serif"),
                    bordercolor=sig.get("color", ACCENT), borderwidth=1,
                    ax=0, ay=-40, opacity=0.95,
                )
            except Exception:
                continue

    fig.update_layout(
        height=420, hovermode="x unified",
        xaxis_title=None, yaxis_title="Price ($)",
        xaxis=dict(rangeslider=dict(visible=False)),
    )
    return style_plotly(fig, title="Price · Moving Averages")


def create_rsi_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["RSI_14"], mode="lines", name="RSI(14)",
        line=dict(color=ACCENT, width=2),
        fill="tozeroy", fillcolor="rgba(0,217,255,0.06)",
    ))
    fig.add_hline(y=70, line_dash="dash", line_color=BEAR, line_width=1,
                  annotation_text="Overbought 70", annotation_font_color=BEAR, annotation_position="right")
    fig.add_hline(y=30, line_dash="dash", line_color=BULL, line_width=1,
                  annotation_text="Oversold 30", annotation_font_color=BULL, annotation_position="right")
    fig.add_hline(y=50, line_dash="dot", line_color=BORDER, line_width=1)
    fig.update_layout(
        height=240, hovermode="x unified",
        xaxis_title=None, yaxis_title="RSI",
        yaxis=dict(range=[0, 100]),
    )
    return style_plotly(fig, title="RSI(14)")


def create_volume_chart(df: pd.DataFrame) -> go.Figure:
    # Color volume bars by daily price direction (green = up day, red = down day)
    colors = [BULL if c >= o else BEAR for c, o in zip(df["Close"], df["Open"])]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["Date"], y=df["Volume"], name="Volume",
                         marker=dict(color=colors, opacity=0.7)))
    fig.add_trace(go.Scatter(x=df["Date"], y=df["Avg_Volume_30d"], mode="lines",
                             name="Avg(30d)", line=dict(color=ACCENT, width=2)))
    fig.update_layout(
        height=240, hovermode="x unified",
        xaxis_title=None, yaxis_title="Volume",
    )
    return style_plotly(fig, title="Volume")


@st.cache_data(ttl=300, show_spinner=False)
def get_signal_history(days: int = 90) -> pd.DataFrame:
    """Pull scan_history for the last N days."""
    try:
        db_path = Path(__file__).resolve().parents[2] / "db" / "screener.db"
        if not db_path.exists():
            return pd.DataFrame(columns=["run_date", "scanner", "ticker"])
        with sqlite3.connect(str(db_path)) as conn:
            return pd.read_sql_query(
                "SELECT run_date, scanner, ticker FROM scan_history "
                "WHERE date(run_date) >= date('now', ?) ORDER BY run_date",
                conn, params=(f"-{days} days",),
            )
    except Exception:
        return pd.DataFrame(columns=["run_date", "scanner", "ticker"])


def render_sector_heatmap(universe_df: pd.DataFrame, scans: dict):
    """Sector × Scanner counts, displayed as a heatmap."""
    if universe_df.empty or "sector" not in universe_df.columns:
        return
    sector_map = dict(zip(universe_df["ticker"], universe_df["sector"].fillna("Unknown")))

    rows = []
    for scanner in ("Momentum", "Reversal", "Caution", "Fade"):
        for r in scans.get(scanner, []):
            rows.append({"sector": sector_map.get(r["ticker"], "Unknown"), "scanner": scanner})
    if not rows:
        return
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(index="sector", columns="scanner", aggfunc=len, fill_value=0)
    # Reindex columns to canonical order
    for c in ("Momentum", "Reversal", "Caution", "Fade"):
        if c not in pivot.columns:
            pivot[c] = 0
    pivot = pivot[["Momentum", "Reversal", "Caution", "Fade"]]
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
        colorscale=[[0, BG], [0.4, "rgba(0,217,255,0.3)"], [1, ACCENT]],
        showscale=False, hoverongaps=False,
        text=pivot.values, texttemplate="%{text}",
        textfont={"family": "JetBrains Mono, monospace", "size": 12, "color": TEXT},
    ))
    fig.update_layout(height=max(180, 28 * len(pivot) + 80), margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(style_plotly(fig, title="Sector × Scanner Density"), width='stretch')


def render_signal_density(days: int = 90):
    """Time-series chart of daily signal counts per scanner from scan_history."""
    hist = get_signal_history(days=days)
    if hist.empty:
        st.markdown(
            f'<div class="mp-empty"><div class="mp-empty-icon">○</div>'
            f'No scan history yet — runs accumulate via the daily cron job.</div>',
            unsafe_allow_html=True,
        )
        return
    counts = hist.groupby(["run_date", "scanner"]).size().reset_index(name="count")
    fig = go.Figure()
    palette = {"Momentum": BULL, "Reversal": ACCENT, "Caution": WARN, "Fade": BEAR}
    for scanner in ("Momentum", "Reversal", "Caution", "Fade"):
        sub = counts[counts["scanner"] == scanner]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["run_date"], y=sub["count"], mode="lines+markers", name=scanner,
            line=dict(color=palette.get(scanner, ACCENT), width=2),
            marker=dict(size=5),
        ))
    fig.update_layout(height=280, hovermode="x unified", yaxis_title="Signals")
    st.plotly_chart(style_plotly(fig, title=f"Signal Density · last {days} days"), width='stretch')


def render_trades_tab(owner: str):
    """Trade journal: log positions, see live P&L, grade closed trades."""
    if not owner:
        st.info("Log in to track trades.")
        return

    st.markdown(
        f'<div style="color:{MUTED};font-size:0.85rem;margin-bottom:14px;">'
        f'Log every trade you take. Open positions show live P&L + suggested exits. '
        f'Closed trades get graded against the best price reached in the 30 days after exit.'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Add trade form ----
    with st.expander("➕ Log a new trade", expanded=False):
        setup_labels = {
            "Manual / other": "manual",
            "Momentum (Runaway Gap)": "momentum",
            "Reversal (Bullish Div.)": "reversal",
            "Caution (Bearish Div.)": "caution",
            "Fade (Gap Up + Light Vol)": "fade",
        }
        with st.form("add_trade", clear_on_submit=True):
            f1, f2, f3 = st.columns(3)
            with f1:
                t_ticker = st.text_input("Ticker", placeholder="AAPL").upper().strip()
                t_side = st.selectbox("Side", ["long", "short"])
                t_setup_label = st.selectbox(
                    "Setup", list(setup_labels.keys()),
                    help="The exit advisor uses this to pick the right playbook.",
                )
            with f2:
                t_entry_date = st.date_input("Entry date", value=datetime.now().date())
                t_entry_price = st.number_input("Entry price", min_value=0.01, value=100.00, step=0.01, format="%.2f")
            with f3:
                t_shares = st.number_input("Shares", min_value=1, value=100, step=1)
                t_already_closed = st.checkbox("Already closed?")
            t_exit_date = None
            t_exit_price = None
            if t_already_closed:
                e1, e2 = st.columns(2)
                with e1:
                    t_exit_date = st.date_input("Exit date", value=datetime.now().date(), key="add_exit_date")
                with e2:
                    t_exit_price = st.number_input("Exit price", min_value=0.01, value=100.00, step=0.01, format="%.2f", key="add_exit_price")
            t_notes = st.text_input("Notes (optional)")

            if st.form_submit_button("Save trade", type="primary"):
                if not t_ticker:
                    st.error("Ticker required.")
                else:
                    add_trade(
                        owner=owner, ticker=t_ticker, side=t_side,
                        entry_date=t_entry_date.isoformat(), entry_price=t_entry_price,
                        shares=t_shares,
                        exit_date=t_exit_date.isoformat() if t_exit_date else None,
                        exit_price=t_exit_price if t_already_closed else None,
                        notes=t_notes,
                        setup=setup_labels[t_setup_label],
                    )
                    st.success(f"Logged {t_side} {t_shares} {t_ticker} @ ${t_entry_price:.2f}")
                    st.rerun()

    # ---- Open positions ----
    open_df = list_trades(owner, status="open")
    closed_df = list_trades(owner, status="closed")

    st.markdown('<div class="mp-section-label">Open Positions</div>', unsafe_allow_html=True)
    if open_df.empty:
        st.markdown(
            '<div class="mp-empty"><div class="mp-empty-icon">○</div>'
            'No open trades. Log one above.</div>',
            unsafe_allow_html=True,
        )
    else:
        total_unrealized = 0.0
        for _, t in open_df.iterrows():
            trade = t.to_dict()
            try:
                df = get_ohlcv(trade["ticker"])
                current = float(df.iloc[-1]["Close"]) if not df.empty else None
            except Exception:
                df, current = None, None
            pnl_info = compute_pnl(trade, current_price=current)
            pnl = pnl_info.get("pnl") or 0
            pct = pnl_info.get("pct") or 0
            total_unrealized += pnl

            color = BULL if pnl >= 0 else BEAR
            sign = "+" if pnl >= 0 else ""
            verdict = evaluate_exit(trade, df) if df is not None else None
            setup_label = (trade.get("setup") or "manual").lower()

            with st.container():
                cols = st.columns([2, 1, 1, 1, 1, 1])
                with cols[0]:
                    setup_pill = (
                        f"<span style='display:inline-block;padding:2px 8px;border-radius:10px;"
                        f"background:rgba(0,217,255,0.10);color:{ACCENT};font-size:0.68rem;"
                        f"font-weight:600;letter-spacing:0.06em;text-transform:uppercase;"
                        f"margin-left:8px;'>{setup_label}</span>"
                    )
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono,monospace;font-weight:700;color:{ACCENT};font-size:1.1rem;'>"
                        f"{trade['ticker']} <span style='color:{MUTED};font-weight:400;font-size:0.8rem;'>· "
                        f"{trade['side'].upper()} · {trade['shares']} sh</span>{setup_pill}</div>"
                        f"<div style='color:{MUTED};font-size:0.78rem;'>Entered {trade['entry_date']} @ ${trade['entry_price']:.2f}</div>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    st.markdown(detail_stat("Mark", f"${current:.2f}" if current else "—"), unsafe_allow_html=True)
                with cols[2]:
                    st.markdown(detail_stat("P&L", f"{sign}${pnl:,.2f}",
                                            color="bull" if pnl >= 0 else "bear"), unsafe_allow_html=True)
                with cols[3]:
                    st.markdown(detail_stat("Return", f"{sign}{pct:.2f}%",
                                            color="bull" if pct >= 0 else "bear"), unsafe_allow_html=True)
                with cols[4]:
                    new_exit = st.number_input(
                        "Close at $", min_value=0.0, value=float(current or trade["entry_price"]),
                        step=0.01, format="%.2f", key=f"close_price_{trade['id']}",
                        label_visibility="collapsed",
                    )
                with cols[5]:
                    if st.button("Close", key=f"close_btn_{trade['id']}", use_container_width=True):
                        close_trade(trade["id"], datetime.now().date().isoformat(), new_exit)
                        st.success(f"Closed {trade['ticker']} @ ${new_exit:.2f}")
                        st.rerun()

                if verdict is not None:
                    action_color = {"exit": BEAR, "trim": WARN, "hold": MUTED}.get(verdict.action, MUTED)
                    bg_alpha = {"high": 0.16, "medium": 0.10, "low": 0.05}.get(verdict.confidence, 0.05)
                    bg_rgb = {BEAR: "248,81,73", WARN: "217,153,34", MUTED: "139,148,158"}.get(action_color, "139,148,158")
                    levels_html = ""
                    if verdict.key_levels:
                        items = " · ".join(
                            f"<span style='color:{MUTED};'>{k.replace('_',' ')}:</span> "
                            f"<span style='color:{TEXT};font-family:JetBrains Mono,monospace;'>${v:.2f}</span>"
                            for k, v in verdict.key_levels.items()
                        )
                        levels_html = (
                            f"<div style='font-size:0.72rem;margin-top:6px;'>{items}</div>"
                        )
                    rules_html = ""
                    if verdict.rules_fired:
                        rules_html = "<ul style='margin:6px 0 0 18px;padding:0;font-size:0.82rem;'>" + "".join(
                            f"<li style='margin:2px 0;'>{r}</li>" for r in verdict.rules_fired
                        ) + "</ul>"
                    st.markdown(
                        f"<div style='background:rgba({bg_rgb},{bg_alpha});border-left:3px solid {action_color};"
                        f"padding:10px 14px;border-radius:6px;margin:6px 0 14px 0;'>"
                        f"<div style='font-family:JetBrains Mono,monospace;font-weight:700;color:{action_color};"
                        f"font-size:0.82rem;letter-spacing:0.06em;text-transform:uppercase;'>"
                        f"{verdict.context}</div>"
                        f"{rules_html}{levels_html}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.95rem;text-align:right;"
            f"margin-top:8px;color:{BULL if total_unrealized >= 0 else BEAR};font-weight:700;'>"
            f"Unrealized total: {'+' if total_unrealized >= 0 else ''}${total_unrealized:,.2f}</div>",
            unsafe_allow_html=True,
        )

    # ---- Closed trades + grading ----
    st.markdown('<div class="mp-section-label" style="margin-top:32px;">Closed Trades</div>',
                unsafe_allow_html=True)
    if closed_df.empty:
        st.markdown(
            '<div class="mp-empty"><div class="mp-empty-icon">○</div>'
            'No closed trades yet.</div>',
            unsafe_allow_html=True,
        )
    else:
        total_realized = 0.0
        grades_capture = []
        for _, t in closed_df.iterrows():
            trade = t.to_dict()
            pnl_info = compute_pnl(trade)
            pnl = pnl_info.get("pnl") or 0
            pct = pnl_info.get("pct") or 0
            total_realized += pnl
            grade_info = grade_closed_trade(trade) or {}
            if grade_info.get("capture_pct") is not None:
                grades_capture.append(grade_info["capture_pct"])

            grade = grade_info.get("grade", "—")
            grade_color = {"A": BULL, "B": "#a78bfa", "C": WARN, "D": "#fb923c", "F": BEAR}.get(grade, MUTED)

            with st.container():
                cols = st.columns([2, 1, 1, 1, 1])
                with cols[0]:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono,monospace;font-weight:700;color:{ACCENT};font-size:1.05rem;'>"
                        f"{trade['ticker']} <span style='color:{MUTED};font-weight:400;font-size:0.8rem;'>· "
                        f"{trade['side'].upper()} · {trade['shares']} sh</span></div>"
                        f"<div style='color:{MUTED};font-size:0.78rem;'>"
                        f"{trade['entry_date']} → {trade['exit_date']} · "
                        f"${trade['entry_price']:.2f} → ${trade['exit_price']:.2f}</div>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    st.markdown(detail_stat("P&L", f"{'+' if pnl >= 0 else ''}${pnl:,.2f}",
                                            color="bull" if pnl >= 0 else "bear"), unsafe_allow_html=True)
                with cols[2]:
                    st.markdown(detail_stat("Return", f"{'+' if pct >= 0 else ''}{pct:.2f}%",
                                            color="bull" if pct >= 0 else "bear"), unsafe_allow_html=True)
                with cols[3]:
                    capture = grade_info.get("capture_pct")
                    cap_text = f"{capture:.0f}%" if capture is not None else "—"
                    st.markdown(
                        f"<div class='mp-stat'><div class='mp-stat-label'>Grade</div>"
                        f"<div class='mp-stat-value' style='color:{grade_color};font-size:1.5rem;'>{grade}</div>"
                        f"<div class='mp-stat-label' style='font-size:0.65rem;'>capture {cap_text}</div></div>",
                        unsafe_allow_html=True,
                    )
                with cols[4]:
                    if st.button("Delete", key=f"del_trade_{trade['id']}", use_container_width=True):
                        remove_trade(trade["id"])
                        st.rerun()

                if grade_info.get("msg"):
                    bg = "rgba(63,185,80,0.06)" if grade in ("A", "B") else "rgba(248,81,73,0.06)"
                    border = BULL if grade in ("A", "B") else BEAR
                    st.markdown(
                        f"<div style='background:{bg};border-left:3px solid {border};"
                        f"padding:8px 12px;border-radius:6px;margin:6px 0 14px 0;font-size:0.82rem;color:{TEXT};'>"
                        f"📋 {grade_info['msg']}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.95rem;text-align:right;"
            f"margin-top:8px;color:{BULL if total_realized >= 0 else BEAR};font-weight:700;'>"
            f"Realized total: {'+' if total_realized >= 0 else ''}${total_realized:,.2f}"
            + (f" · avg capture {sum(grades_capture)/len(grades_capture):.0f}%" if grades_capture else "")
            + "</div>",
            unsafe_allow_html=True,
        )


def render_backtest_tab(tickers: list[str]):
    """Backtest UI: pick scanner + date range + run replay, show summary + per-trade table."""
    st.markdown(
        f'<div style="color:{MUTED};font-size:0.85rem;margin-bottom:14px;">'
        f'Replay a scanner over a historical window and check forward Close→Close returns. '
        f'<strong style="color:{WARN};">v1 caveat:</strong> indicators are approximated using current data, '
        f'and survivorship/transaction costs are not modeled. Treat as directional, not P&L.'
        f'</div>',
        unsafe_allow_html=True,
    )

    bc1, bc2, bc3 = st.columns([1, 1, 1])
    with bc1:
        scanner = st.selectbox("Scanner", ["Momentum", "Reversal", "Caution", "Fade"], key="bt_scanner")
    with bc2:
        end_date = st.date_input("End date", value=datetime.now().date(), key="bt_end")
    with bc3:
        days_back = st.number_input("Lookback (days)", min_value=20, max_value=365, value=120, step=10, key="bt_lookback")
    start_date = (end_date - timedelta(days=days_back)).isoformat()

    if st.button("▶ Run backtest", type="primary", key="bt_run"):
        progress_bar = st.progress(0.0)
        status_box = st.empty()

        def cb(done, total, ticker):
            progress_bar.progress(done / total)
            status_box.markdown(
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{MUTED};'>"
                f"Testing {ticker} ({done}/{total})</div>",
                unsafe_allow_html=True,
            )

        results = backtest_scanner(
            scanner=scanner, tickers=tickers,
            start_date=start_date, end_date=end_date.isoformat(),
            forward_days=(1, 5, 20), progress_callback=cb,
        )
        progress_bar.empty()
        status_box.empty()
        st.session_state["bt_results"] = results
        st.session_state["bt_meta"] = {"scanner": scanner, "start": start_date, "end": end_date.isoformat()}

    results = st.session_state.get("bt_results")
    meta = st.session_state.get("bt_meta", {})
    if results is None or results.empty:
        st.markdown(
            f'<div class="mp-empty"><div class="mp-empty-icon">○</div>'
            f'No backtest results yet. Pick parameters and run.</div>',
            unsafe_allow_html=True,
        )
        return

    summary = summarize_results(results, forward_days=(1, 5, 20))
    st.markdown(
        f'<div style="margin:14px 0;color:{MUTED};font-size:0.82rem;">'
        f'{meta.get("scanner")} · {meta.get("start")} → {meta.get("end")} · '
        f'<strong style="color:{TEXT};">{summary["count"]} signals</strong></div>',
        unsafe_allow_html=True,
    )

    # Summary tiles per horizon
    cols = st.columns(3)
    for i, n in enumerate((1, 5, 20)):
        s = summary.get(f"ret_{n}d")
        with cols[i]:
            if not s:
                st.markdown(detail_stat(f"{n}D", "—"), unsafe_allow_html=True)
                continue
            color = "bull" if s["mean"] > 0 else "bear"
            st.markdown(
                detail_stat(
                    f"{n}D Mean Return", f"{s['mean']:+.2f}%",
                    color=color,
                ) + detail_stat(
                    f"{n}D Hit Rate", f"{s['hit_rate']:.1f}%",
                    color="bull" if s["hit_rate"] >= 50 else "bear",
                ),
                unsafe_allow_html=True,
            )

    # Trades table
    st.markdown('<div class="mp-section-label" style="margin-top:24px;">Trades</div>', unsafe_allow_html=True)
    display_df = results.copy()
    for col in ("ret_1d", "ret_5d", "ret_20d"):
        if col in display_df.columns:
            display_df[col] = display_df[col].round(2)
    pct_fmt = JsCode("function(p){if(p.value==null)return '';const n=Number(p.value);return (n>=0?'+':'')+n.toFixed(2)+'%';}")
    pct_style = JsCode(
        "function(p){if(p.value==null)return null;const v=Number(p.value);"
        "const base={fontFamily:'JetBrains Mono,monospace',textAlign:'right',fontWeight:'600'};"
        "if(v > 0)return{...base,color:'#3fb950'};"
        "if(v < 0)return{...base,color:'#f85149'};"
        "return{...base,color:'#8b949e'};}"
    )
    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, filter=True, resizable=True, flex=1, unSortIcon=True)
    gb.configure_column("ticker", cellStyle={"fontFamily": "JetBrains Mono,monospace", "color": ACCENT, "fontWeight": "700"})
    for col in ("ret_1d", "ret_5d", "ret_20d"):
        if col in display_df.columns:
            gb.configure_column(col, type=["numericColumn"], valueFormatter=pct_fmt, cellStyle=pct_style)
    if "entry_close" in display_df.columns:
        gb.configure_column("entry_close", type=["numericColumn"],
                            valueFormatter=JsCode("function(p){return p.value==null?'':'$'+Number(p.value).toFixed(2);}"))
    AgGrid(display_df, gridOptions=gb.build(),
           height=min(450, 60 + 30 * len(display_df)),
           allow_unsafe_jscode=True, use_json_serialization=True,
           theme="balham-dark", custom_css=GRID_CSS, key="bt_table")

    csv = results.to_csv(index=False)
    st.download_button("📥 Download backtest CSV", data=csv, file_name="backtest_results.csv",
                       mime="text/csv", key="bt_download")


def all_signals_table(scans: dict, filter_text: str = "", watchlist_tickers: set = None):
    """Combined table showing every signal across all four scanners."""
    watchlist_tickers = watchlist_tickers or set()
    combined = []
    for scanner in ("Momentum", "Reversal", "Caution", "Fade"):
        for r in scans.get(scanner, []):
            t = r["ticker"]
            if filter_text and filter_text.upper() not in t.upper():
                continue
            sl = r.get("_sparkline", [])
            combined.append({
                "Ticker": t,
                "Scanner": scanner,
                "Close": r.get("close"),
                "RSI": r.get("rsi"),
                "From High": r.get("_pct_from_high"),
                "From Low": r.get("_pct_from_low"),
                "30d": to_sparkline_text(sl),
                "_direction": "up" if sl and sl[-1] >= sl[0] else "down",
                "_starred": t in watchlist_tickers,
            })

    if not combined:
        st.markdown(
            f'<div class="mp-empty"><div class="mp-empty-icon">○</div>'
            f'No signals across any scanner today.</div>',
            unsafe_allow_html=True,
        )
        return

    df = pd.DataFrame(combined)
    csv = df.drop(columns=["30d", "_direction", "_starred"], errors="ignore").to_csv(index=False)

    scanner_color = JsCode(
        "function(p){"
        "  const colors = {'Momentum':'#3fb950','Reversal':'#00d9ff','Caution':'#d29922','Fade':'#f85149'};"
        "  const c = colors[p.value] || '#8b949e';"
        "  return {color: c, fontWeight:'600', fontFamily:'Inter,sans-serif'};"
        "}"
    )
    ticker_style = JsCode(
        "function(p){return {fontFamily:'JetBrains Mono,monospace',fontWeight:'700',"
        "color:'#00d9ff',cursor:'pointer',letterSpacing:'0.04em'};}"
    )
    ticker_value_fmt = JsCode(
        "function(p){return p.data._starred ? '★ ' + p.value : p.value;}"
    )
    currency_fmt = JsCode("function(p){return p.value==null?'':'$'+Number(p.value).toFixed(2);}")
    rsi_fmt = JsCode("function(p){return p.value==null?'':Number(p.value).toFixed(2);}")
    pct_fmt = JsCode("function(p){if(p.value==null)return '';const n=Number(p.value);return (n>=0?'+':'')+n.toFixed(1)+'%';}")
    pct_style = JsCode(
        "function(p){if(p.value==null)return null;const v=Number(p.value);"
        "const base={fontFamily:'JetBrains Mono,monospace',textAlign:'right'};"
        "if(v>=0)return{...base,color:'#3fb950'};"
        "if(v>=-10)return{...base,color:'#8b949e'};"
        "if(v>=-25)return{...base,color:'#d29922'};"
        "return{...base,color:'#f85149'};}"
    )
    spark_style = JsCode(
        "function(p){const c = p.data._direction==='down'?'#f85149':'#3fb950';"
        "return {fontFamily:'JetBrains Mono,monospace',color:c,letterSpacing:'1px',fontSize:'1.05rem'};}"
    )
    numeric = {"fontFamily": "JetBrains Mono, monospace", "textAlign": "right", "color": TEXT}

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(sortable=True, filter=True, resizable=True, flex=1, unSortIcon=True)
    gb.configure_column("Ticker", cellStyle=ticker_style, valueFormatter=ticker_value_fmt, pinned="left", width=120)
    gb.configure_column("Scanner", cellStyle=scanner_color, width=120,
                        headerTooltip="Which scanner flagged this ticker.")
    gb.configure_column("Close", type=["numericColumn"], valueFormatter=currency_fmt, cellStyle=numeric)
    gb.configure_column("RSI", type=["numericColumn"], valueFormatter=rsi_fmt, cellStyle=numeric)
    gb.configure_column("From High", type=["numericColumn"], valueFormatter=pct_fmt, cellStyle=pct_style, width=110)
    gb.configure_column("From Low", type=["numericColumn"], valueFormatter=pct_fmt, cellStyle=pct_style, width=110)
    gb.configure_column("30d", cellStyle=spark_style, sortable=False, filter=False, width=110)
    for h in ("_direction", "_starred"):
        gb.configure_column(h, hide=True)
    gb.configure_selection(selection_mode="multiple", use_checkbox=False, rowMultiSelectWithClick=True)

    st.caption(f"All {len(df)} signals across scanners · click header to sort · ⌘/Ctrl+click for compare")
    grid_response = AgGrid(
        df, gridOptions=gb.build(),
        height=min(500, 60 + 35 * len(df)),
        allow_unsafe_jscode=True, use_json_serialization=True,
        theme="balham-dark", custom_css=GRID_CSS, key="aggrid_all_signals",
    )

    sel = grid_response.get("selected_rows")
    sel_tickers = []
    if isinstance(sel, pd.DataFrame) and not sel.empty:
        sel_tickers = sel["Ticker"].tolist()
    elif isinstance(sel, list):
        sel_tickers = [r["Ticker"] for r in sel]
    if len(sel_tickers) == 1:
        st.session_state.selected_ticker = sel_tickers[0]
        st.session_state.compare_tickers = []
    elif len(sel_tickers) > 1:
        st.session_state.compare_tickers = sel_tickers
        st.session_state.selected_ticker = None

    st.download_button(
        "📥 Download All Signals as CSV", data=csv,
        file_name="all_signals.csv", mime="text/csv", key="download_all_signals",
    )


def compare_view(tickers: list[str]):
    """Side-by-side stat comparison of multiple tickers."""
    st.markdown(
        f'<div class="mp-detail-header">'
        f'<div class="mp-detail-ticker">COMPARE</div>'
        f'<div class="mp-detail-tagline">{len(tickers)} tickers · '
        f'{" · ".join(tickers)}</div></div>',
        unsafe_allow_html=True,
    )

    cards = '<div class="mp-compare-grid">'
    for ticker in tickers:
        try:
            df = get_ohlcv(ticker)
            if df.empty:
                cards += f'<div class="mp-compare-card"><div class="mp-detail-ticker" style="font-size:1.3rem;">{ticker}</div><div class="mp-stat-label">No data</div></div>'
                continue
            df = enrich_ohlcv_with_indicators(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]["Close"] if len(df) >= 2 else latest["Close"]
            chg = latest["Close"] - prev
            pct = (chg / prev * 100) if prev else 0
            chg_color = BULL if chg >= 0 else BEAR
            sign = "+" if chg >= 0 else ""
            cards += (
                f'<div class="mp-compare-card">'
                f'<div class="mp-detail-ticker" style="font-size:1.3rem;">{ticker}</div>'
                f'<div class="mp-stat-value" style="margin-top:8px;">{format_currency(latest["Close"])}</div>'
                f'<div class="mp-stat-label" style="color:{chg_color};margin-top:2px;">{sign}{format_currency(chg)} · {sign}{pct:.2f}%</div>'
                f'<div style="margin-top:14px;"><span class="mp-stat-label">RSI(14)</span> '
                f'<span class="mp-stat-value" style="font-size:0.95rem;">{latest["RSI_14"]:.1f}</span></div>'
                f'<div><span class="mp-stat-label">Volume</span> '
                f'<span class="mp-stat-value" style="font-size:0.95rem;">{format_number(latest["Volume"])}</span></div>'
                f'<div><span class="mp-stat-label">52W High</span> '
                f'<span class="mp-stat-value" style="font-size:0.95rem;">{format_currency(df["High"].max())}</span></div>'
                f'<div><span class="mp-stat-label">52W Low</span> '
                f'<span class="mp-stat-value" style="font-size:0.95rem;">{format_currency(df["Low"].min())}</span></div>'
                f'</div>'
            )
        except Exception as e:
            cards += f'<div class="mp-compare-card"><div class="mp-detail-ticker">{ticker}</div><div>Error: {e}</div></div>'
    cards += "</div>"
    st.markdown(cards, unsafe_allow_html=True)

    # Overlaid normalized price chart for visual comparison
    fig = go.Figure()
    palette = [ACCENT, BULL, "#a78bfa", "#fb923c", "#fbbf24", BEAR, "#4ad8ff", "#3fb950"]
    for i, ticker in enumerate(tickers):
        try:
            df = get_ohlcv(ticker).tail(60)
            if df.empty:
                continue
            base = df["Close"].iloc[0]
            normalized = (df["Close"] / base - 1) * 100
            fig.add_trace(go.Scatter(
                x=df["Date"], y=normalized, mode="lines", name=ticker,
                line=dict(color=palette[i % len(palette)], width=2),
            ))
        except Exception:
            continue
    fig.update_layout(height=380, hovermode="x unified", yaxis_title="% Change (60d)")
    st.plotly_chart(style_plotly(fig, title="60-Day Normalized Performance"), width='stretch')


def detail_view(ticker: str, scans: dict = None):
    """Show detailed view for a stock with charts."""
    try:
        # Build signal annotations from cached scan results
        signals: list = []
        if scans:
            color_map = {"Momentum": BULL, "Reversal": ACCENT, "Caution": WARN, "Fade": BEAR}
            for scanner in ("Momentum", "Reversal", "Caution", "Fade"):
                for r in scans.get(scanner, []):
                    if r.get("ticker") == ticker and r.get("date"):
                        signals.append({
                            "date": str(r["date"]).split(" ")[0],
                            "label": scanner,
                            "color": color_map[scanner],
                        })
        df = get_ohlcv(ticker)
        if df.empty:
            st.markdown(
                f'<div class="mp-empty"><div class="mp-empty-icon">📭</div>'
                f'No data found for <strong>{ticker}</strong></div>',
                unsafe_allow_html=True,
            )
            return

        df = enrich_ohlcv_with_indicators(df)
        latest = df.iloc[-1]
        high_52w = df["High"].max()
        low_52w = df["Low"].min()

        # Day change vs prior close
        prev_close = df.iloc[-2]["Close"] if len(df) >= 2 else latest["Close"]
        day_change = latest["Close"] - prev_close
        day_pct = (day_change / prev_close * 100) if prev_close else 0
        change_color = "bull" if day_change >= 0 else "bear"
        change_sign = "+" if day_change >= 0 else ""

        # RSI color signal
        rsi = latest["RSI_14"]
        if pd.isna(rsi):
            rsi_color = "default"
        elif rsi < 30:
            rsi_color = "bear"
        elif rsi > 70:
            rsi_color = "warn"
        elif rsi >= 50:
            rsi_color = "bull"
        else:
            rsi_color = "default"

        # Header
        st.markdown(
            f'<div class="mp-detail-header">'
            f'<div class="mp-detail-ticker">{ticker}</div>'
            f'<div class="mp-detail-tagline">Detailed View · Last {len(df)} sessions</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Responsive stat grid — wraps automatically on narrow screens
        stats_html = '<div class="mp-stats-grid">'
        stats_html += detail_stat("Close", format_currency(latest["Close"]), color="accent")
        stats_html += detail_stat(
            "Day Change",
            f"{change_sign}{format_currency(day_change)} ({change_sign}{day_pct:.2f}%)",
            color=change_color,
        )
        stats_html += detail_stat("RSI(14)", f"{rsi:.2f}" if not pd.isna(rsi) else "—", color=rsi_color)
        stats_html += detail_stat("Volume", format_number(latest["Volume"]))
        stats_html += detail_stat("Avg Vol 30d", format_number(latest["Avg_Volume_30d"]))
        stats_html += detail_stat("52W High", format_currency(high_52w))
        stats_html += detail_stat("52W Low", format_currency(low_52w))
        stats_html += "</div>"
        st.markdown(stats_html, unsafe_allow_html=True)

        # Charts
        st.plotly_chart(create_price_chart(df.tail(120), signals=signals), width='stretch')
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(create_rsi_chart(df.tail(120)), width='stretch')
        with c2:
            st.plotly_chart(create_volume_chart(df.tail(120)), width='stretch')

        # Recent OHLCV via AG Grid (consistent w/ scanner tables)
        st.markdown('<div class="mp-section-label">Recent Sessions</div>', unsafe_allow_html=True)
        recent = df.tail(10)[["Date", "Open", "High", "Low", "Close", "Volume", "RSI_14"]].copy()
        recent = recent.iloc[::-1]  # newest first
        recent["Date"] = pd.to_datetime(recent["Date"]).dt.strftime("%Y-%m-%d")
        recent = recent.rename(columns={"RSI_14": "RSI"})

        currency_fmt = JsCode(
            "function(p){ if(p.value==null) return ''; return '$' + Number(p.value).toFixed(2); }"
        )
        volume_fmt = JsCode(
            "function(p){ if(p.value==null) return ''; return Number(p.value).toLocaleString(); }"
        )
        rsi_fmt = JsCode(
            "function(p){ if(p.value==null) return ''; return Number(p.value).toFixed(2); }"
        )
        numeric_style = {"fontFamily": "JetBrains Mono, monospace", "textAlign": "right", "color": TEXT}

        gb = GridOptionsBuilder.from_dataframe(recent)
        gb.configure_default_column(sortable=True, resizable=True, flex=1, unSortIcon=True)
        gb.configure_column("Date", cellStyle={"fontFamily": "JetBrains Mono, monospace", "color": MUTED})
        for c in ("Open", "High", "Low", "Close"):
            gb.configure_column(c, type=["numericColumn"], valueFormatter=currency_fmt, cellStyle=numeric_style)
        gb.configure_column("Volume", type=["numericColumn"], valueFormatter=volume_fmt, cellStyle=numeric_style)
        gb.configure_column("RSI", type=["numericColumn"], valueFormatter=rsi_fmt, cellStyle=numeric_style)

        AgGrid(
            recent,
            gridOptions=gb.build(),
            height=60 + 30 * len(recent),
            allow_unsafe_jscode=True,
            use_json_serialization=True,
            theme="balham-dark",
            custom_css=GRID_CSS,
            key=f"recent_{ticker}",
        )

    except Exception as e:
        st.error(f"Error loading details for {ticker}: {str(e)}")


def scanner_results_table(scanner_name: str, results: list, filter_text: str = "",
                          fixed_height: int | None = None, watchlist_tickers: set = None):
    """Display results table for a scanner."""
    watchlist_tickers = watchlist_tickers or set()
    # Apply global filter
    if filter_text:
        results = [r for r in results if filter_text.upper() in r.get("ticker", "").upper()]

    if not results:
        msg = (
            f"No signals matching <strong>{filter_text}</strong> in {scanner_name}."
            if filter_text else
            f"No signals from <strong>{scanner_name}</strong> today."
        )
        # Match the AG Grid's rendered iframe height (which is fixed_height + ~38px
        # of streamlit-aggrid wrapper overhead) so empty/populated cells line up.
        if fixed_height:
            target = fixed_height + 38
            height_style = (
                f'style="height:{target}px;box-sizing:border-box;display:flex;'
                f'flex-direction:column;align-items:center;justify-content:center;"'
            )
        else:
            height_style = ''
        st.markdown(
            f'<div class="mp-empty" {height_style}>'
            f'<div class="mp-empty-icon">○</div>{msg}</div>',
            unsafe_allow_html=True,
        )
        return

    rows = []
    for result in results:
        ticker = result["ticker"]
        row = {"Ticker": ticker}
        if "open" in result:
            row["Open"] = result.get("open")
            row["Close"] = result.get("close")
            row["Volume"] = result.get("volume")
        if "low" in result:
            row["52W Low"] = result.get("low")
        if "high" in result:
            row["52W High"] = result.get("high")
        if "rsi" in result:
            row["RSI"] = result.get("rsi")
        # Position context: how far from 52W extremes
        row["From High"] = result.get("_pct_from_high")
        row["From Low"] = result.get("_pct_from_low")
        sparkline = result.get("_sparkline", [])
        row["30d"] = to_sparkline_text(sparkline)
        row["_direction"] = "up" if sparkline and sparkline[-1] >= sparkline[0] else "down"
        row["_starred"] = ticker in watchlist_tickers
        rows.append(row)

    df = pd.DataFrame(rows)
    csv_df = df.drop(columns=["30d", "_direction", "_starred"], errors="ignore")
    csv = csv_df.to_csv(index=False)

    # Build AG Grid options: numeric columns sortable/filterable, currency/volume formatters,
    # single-row selection on cell click, ticker column emphasized.
    currency_fmt = JsCode(
        "function(params){ if(params.value==null) return ''; "
        "return '$' + Number(params.value).toFixed(2); }"
    )
    volume_fmt = JsCode(
        "function(params){ if(params.value==null) return ''; "
        "return Number(params.value).toLocaleString(); }"
    )
    rsi_fmt = JsCode(
        "function(params){ if(params.value==null) return ''; "
        "return Number(params.value).toFixed(2); }"
    )
    rsi_cell_style = JsCode(
        "function(params){ "
        "  if(params.value==null) return null;"
        "  const v = Number(params.value);"
        "  const base = {fontFamily:'JetBrains Mono, monospace', fontWeight:'600', textAlign:'right'};"
        "  if(v < 30) return {...base, color:'#f85149'};"
        "  if(v > 70) return {...base, color:'#d29922'};"
        "  if(v >= 50) return {...base, color:'#3fb950'};"
        "  return {...base, color:'#8b949e'};"
        "}"
    )
    numeric_cell_style = {
        "fontFamily": "JetBrains Mono, monospace",
        "textAlign": "right",
        "color": "#e6edf3",
    }
    ticker_cell_style = JsCode(
        "function(params){ return {"
        "  fontFamily: 'JetBrains Mono, monospace',"
        "  fontWeight: '700',"
        "  color: '#00d9ff',"
        "  cursor: 'pointer',"
        "  letterSpacing: '0.04em'"
        "}; }"
    )
    ticker_value_fmt = JsCode(
        "function(p){ if(!p.value) return ''; "
        "return p.data && p.data._starred ? '★ ' + p.value : p.value; }"
    )
    pct_fmt = JsCode(
        "function(p){ if(p.value==null) return ''; "
        "const n = Number(p.value); const sign = n >= 0 ? '+' : ''; "
        "return sign + n.toFixed(1) + '%'; }"
    )
    pct_cell_style = JsCode(
        "function(p){"
        "  if(p.value==null) return null;"
        "  const v = Number(p.value);"
        "  const base = {fontFamily:'JetBrains Mono, monospace', textAlign:'right'};"
        "  if(v >= 0) return {...base, color:'#3fb950'};"
        "  if(v >= -10) return {...base, color:'#8b949e'};"
        "  if(v >= -25) return {...base, color:'#d29922'};"
        "  return {...base, color:'#f85149'};"
        "}"
    )

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        sortable=True, filter=True, resizable=True, floatingFilter=False, flex=1,
        unSortIcon=True,
    )
    gb.configure_column(
        "Ticker",
        headerName="Ticker",
        headerTooltip="Stock ticker symbol. ★ = in a saved watchlist. Click row for detail · ⌘+click for compare.",
        cellStyle=ticker_cell_style,
        valueFormatter=ticker_value_fmt,
        pinned="left",
        width=120,
    )
    col_tooltips = {
        "Open": "Today's opening price",
        "Close": "Today's closing price",
        "Volume": "Today's trading volume (shares)",
        "52W Low": "Lowest low over the past 52 weeks",
        "52W High": "Highest high over the past 52 weeks",
        "RSI": "Relative Strength Index (14-day). <30 oversold · 30–50 weak · 50–70 strong · >70 overbought",
    }
    for col in ("Open", "Close", "52W Low", "52W High"):
        if col in df.columns:
            gb.configure_column(
                col, type=["numericColumn"], valueFormatter=currency_fmt,
                cellStyle=numeric_cell_style, headerTooltip=col_tooltips.get(col),
            )
    if "Volume" in df.columns:
        gb.configure_column(
            "Volume", type=["numericColumn"], valueFormatter=volume_fmt,
            cellStyle=numeric_cell_style, headerTooltip=col_tooltips["Volume"],
        )
    if "RSI" in df.columns:
        gb.configure_column(
            "RSI", type=["numericColumn"], valueFormatter=rsi_fmt,
            cellStyle=rsi_cell_style, headerTooltip=col_tooltips["RSI"],
        )
    if "From High" in df.columns:
        gb.configure_column(
            "From High", type=["numericColumn"], valueFormatter=pct_fmt,
            cellStyle=pct_cell_style, width=100,
            headerTooltip="% from 52-week high. Negative = below the high. Closer to 0% = near ATH.",
        )
    if "From Low" in df.columns:
        gb.configure_column(
            "From Low", type=["numericColumn"], valueFormatter=pct_fmt,
            cellStyle=pct_cell_style, width=100,
            headerTooltip="% from 52-week low. Positive = above the low. Closer to 0% = near 52w low.",
        )

    # Unicode sparkline column — colored by direction via cellStyle (renders reliably)
    sparkline_cell_style = JsCode(
        "function(params){"
        "  const dir = params.data && params.data._direction;"
        "  const color = dir === 'down' ? '#f85149' : '#3fb950';"
        "  return {fontFamily:'JetBrains Mono, monospace', color: color, "
        "          letterSpacing:'1px', fontSize:'1.05rem', textAlign:'left'};"
        "}"
    )
    if "30d" in df.columns:
        gb.configure_column(
            "30d", headerName="30D", cellStyle=sparkline_cell_style,
            sortable=False, filter=False, width=110, suppressSizeToFit=True,
            headerTooltip="30-day price trend (Unicode bar chart). Green = up over period, red = down.",
        )
    for hidden in ("_direction", "_starred"):
        if hidden in df.columns:
            gb.configure_column(hidden, hide=True)

    gb.configure_selection(selection_mode="multiple", use_checkbox=False, rowMultiSelectWithClick=True)
    grid_options = gb.build()

    compact = fixed_height is not None
    if not compact:
        st.caption("Click row for detail · ⌘/Ctrl+click for multi-select compare · click column headers to sort (▲▼)")
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        height=fixed_height if fixed_height else min(400, 60 + 35 * len(df)),
        allow_unsafe_jscode=True,
        use_json_serialization=True,
        theme="balham-dark",
        custom_css=GRID_CSS,
        key=f"aggrid_{scanner_name}",
    )

    selected = grid_response.get("selected_rows")
    selected_tickers: list[str] = []
    if isinstance(selected, pd.DataFrame) and not selected.empty:
        selected_tickers = selected["Ticker"].tolist()
    elif isinstance(selected, list):
        selected_tickers = [r["Ticker"] for r in selected]

    if len(selected_tickers) == 1:
        st.session_state.selected_ticker = selected_tickers[0]
        st.session_state.compare_tickers = []
    elif len(selected_tickers) > 1:
        st.session_state.compare_tickers = selected_tickers
        st.session_state.selected_ticker = None

    if not compact:
        st.download_button(
            label=f"📥 Download {scanner_name} Results as CSV",
            data=csv,
            file_name=f"{scanner_name.lower().replace(' ', '_')}_results.csv",
            mime="text/csv",
            key=f"download_{scanner_name}"
        )


def _enforce_auth():
    """If MP_AUTH_CONFIG is set, require login. No-op otherwise.

    Credentials come from the SQLite users table (status='approved'). The YAML
    only provides cookie config + bootstrap admin list; on first run it seeds
    the DB with the YAML's user entries.
    """
    cfg_path = os.getenv("MP_AUTH_CONFIG")
    if not cfg_path:
        return None
    try:
        import yaml
        import streamlit_authenticator as stauth
    except ImportError:
        st.error("Auth enabled but streamlit-authenticator/PyYAML not installed.")
        st.stop()

    try:
        with open(cfg_path) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        st.error(f"Failed to load auth config from {cfg_path}: {e}")
        st.stop()

    # Bootstrap: seed DB from YAML on first run (no-op if users table populated)
    init_db()
    seed_from_yaml(config.get("credentials", {}), config.get("admins", []) or [])

    creds = get_approved_credentials()
    # streamlit-authenticator requires at least one user — show signup-only mode if empty
    if not creds["usernames"]:
        st.warning("No approved users yet. Please sign up below — an admin will approve.")
        _render_signup_form()
        st.stop()

    authenticator = stauth.Authenticate(
        creds,
        config["cookie"]["name"],
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
    )
    authenticator.login(location="main")

    if st.session_state.get("authentication_status") is False:
        st.error("Username or password incorrect.")
        _render_signup_form()
        st.stop()
    if st.session_state.get("authentication_status") is None:
        st.info("Please log in below — or create an account for admin approval.")
        _render_signup_form()
        st.stop()
    return authenticator


def _render_signup_form():
    """Self-serve signup form shown alongside login. New accounts go to 'pending'."""
    with st.expander("✨ Create an account", expanded=False):
        with st.form("signup_form", clear_on_submit=True):
            su_username = st.text_input("Username", help="alphanumeric, lowercased")
            su_name = st.text_input("Display name")
            su_email = st.text_input("Email")
            su_password = st.text_input("Password", type="password",
                                        help="min 8 characters")
            submitted = st.form_submit_button("Request access")
            if submitted:
                ok, msg = user_signup(su_username, su_email, su_name, su_password)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)


def _render_admin_panel():
    """Visible only to admins — approve/reject pending users + manage existing."""
    pending = list_users(status="pending")
    approved = list_users(status="approved")

    label = f"🛡 Admin · {len(pending)} pending" if pending else "🛡 Admin"
    with st.expander(label, expanded=bool(pending)):
        if pending:
            st.markdown(f"<div class='mp-section-label'>Pending</div>", unsafe_allow_html=True)
            for u in pending:
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.85rem;'>"
                        f"<strong>{u['username']}</strong> · "
                        f"<span style='color:{MUTED};'>{u['email'] or '—'}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    if st.button("✓", key=f"approve_{u['username']}", use_container_width=True):
                        set_status(u["username"], "approved")
                        st.rerun()
                with cols[2]:
                    if st.button("✕", key=f"reject_{u['username']}", use_container_width=True):
                        set_status(u["username"], "rejected")
                        st.rerun()
        else:
            st.caption("No pending requests.")

        st.markdown(f"<div class='mp-section-label' style='margin-top:14px;'>Approved</div>",
                    unsafe_allow_html=True)
        for u in approved:
            cols = st.columns([3, 1])
            with cols[0]:
                badge = " 🛡" if u["is_admin"] else ""
                st.markdown(
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;'>"
                    f"{u['username']}{badge} · <span style='color:{MUTED};'>{u['email'] or '—'}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if not u["is_admin"]:
                    if st.button("Remove", key=f"del_{u['username']}", use_container_width=True):
                        delete_user(u["username"])
                        st.rerun()


def main():
    st.set_page_config(page_title="Market Pulse", layout="wide", initial_sidebar_state="expanded")
    inject_css()

    # Auth gate (only active if MP_AUTH_CONFIG env var points to a YAML config)
    authenticator = _enforce_auth()

    st.markdown(
        '<div class="mp-title">MARKET PULSE</div>'
        '<div class="mp-subtitle">Daily Technical Screener · Bloomberg-style</div>',
        unsafe_allow_html=True,
    )

    # Initialize session state — seed defaults from URL params if present
    qp = st.query_params
    if 'selected_ticker' not in st.session_state:
        st.session_state.selected_ticker = qp.get("ticker")
    if 'compare_tickers' not in st.session_state:
        cmp_param = qp.get("compare", "")
        st.session_state.compare_tickers = cmp_param.split(",") if cmp_param else []

    # Initialize database
    init_db()

    # Sidebar: data refresh
    with st.sidebar:
        st.markdown(
            '<div class="mp-title" style="font-size:1.4rem;margin-bottom:0;">MARKET PULSE</div>'
            '<div class="mp-subtitle" style="font-size:0.7rem;margin-bottom:1rem;">Control Panel</div>',
            unsafe_allow_html=True,
        )

        # Show logged-in user + logout button when auth is enabled
        if authenticator is not None and st.session_state.get("authentication_status"):
            user_name = st.session_state.get("name", "user")
            current_username = st.session_state.get("username", "")
            admin_badge = " 🛡" if is_admin(current_username) else ""
            st.markdown(
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;"
                f"color:{MUTED};margin-bottom:8px;'>👤 {user_name}{admin_badge}</div>",
                unsafe_allow_html=True,
            )
            authenticator.logout("Logout", location="sidebar")
            if is_admin(current_username):
                _render_admin_panel()

        # Last-refreshed indicator
        last_refresh = get_last_refresh_time()
        if last_refresh:
            st.markdown(
                f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:8px;'
                f'padding:8px 12px;margin-bottom:1rem;">'
                f'<div class="mp-stat-label" style="margin-bottom:2px;">Data Through</div>'
                f'<div style="font-family:JetBrains Mono,monospace;font-size:0.85rem;color:{ACCENT};">{last_refresh}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div class="mp-section-label">Data</div>', unsafe_allow_html=True)
        if st.button("🔄 Update Universe", use_container_width=True):
            with st.spinner("Updating universe..."):
                update_universe()
            run_all_scanners.clear()
            get_last_refresh_time.clear()
            st.success("Universe updated!")

        if st.button("📊 Refresh Prices", use_container_width=True):
            with st.status("Refreshing OHLCV data...", expanded=True) as status:
                progress_bar = st.progress(0.0)
                live_text = st.empty()

                def on_progress(done, total, ticker, st_):
                    progress_bar.progress(done / total)
                    icon = "✓" if st_ == "ok" else ("∅" if st_ == "empty" else "✗")
                    live_text.markdown(
                        f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;"
                        f"color:{MUTED};'>{icon} {ticker} ({done}/{total})</div>",
                        unsafe_allow_html=True,
                    )

                refresh_all_ohlcv(days_back=365, progress_callback=on_progress)
                status.update(label="Refresh complete", state="complete", expanded=False)
            run_all_scanners.clear()
            get_last_refresh_time.clear()
            st.success("Data refreshed!")

        if st.button("📅 Refresh Earnings", use_container_width=True,
                     help="Pulls earnings dates via Finnhub (set FINNHUB_API_KEY) or yfinance fallback."):
            with st.spinner("Fetching earnings calendar..."):
                update_earnings_calendar(tickers=get_universe()["ticker"].tolist())
            run_all_scanners.clear()
            st.success("Earnings refreshed!")

        # Universe management
        with st.expander("🗂 Universe", expanded=False):
            current_universe = get_universe()
            st.markdown(
                f"<div style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:{MUTED};margin-bottom:6px;'>"
                f"{len(current_universe)} tickers tracked</div>",
                unsafe_allow_html=True,
            )
            new_ticker = st.text_input(
                "Add ticker", placeholder="e.g. NVDA", key="universe_add_input",
                label_visibility="collapsed",
            ).strip().upper()
            cu1, cu2 = st.columns(2)
            with cu1:
                if st.button("➕ Add", use_container_width=True, key="universe_add_btn") and new_ticker:
                    if add_to_universe(new_ticker):
                        run_all_scanners.clear()
                        st.success(f"Added {new_ticker}")
                        st.rerun()
            with cu2:
                tickers_list = current_universe["ticker"].tolist() if not current_universe.empty else []
                to_remove = st.selectbox("Remove", ["—"] + tickers_list,
                                         label_visibility="collapsed", key="universe_remove_pick")
                if to_remove and to_remove != "—":
                    if st.button(f"✕ Remove {to_remove}", use_container_width=True, key="universe_remove_btn"):
                        remove_from_universe(to_remove)
                        run_all_scanners.clear()
                        st.success(f"Removed {to_remove}")
                        st.rerun()

        st.markdown('<div class="mp-section-label">View</div>', unsafe_allow_html=True)
        # Initialize from URL param if present
        if "layout_mode" not in st.session_state:
            st.session_state.layout_mode = qp.get("layout", "Tabs")
        layout_mode = st.radio(
            "Layout", ["Tabs", "2×2 Grid"], horizontal=True, label_visibility="collapsed",
            key="layout_mode",
        )

        st.markdown('<div class="mp-section-label">Filter</div>', unsafe_allow_html=True)
        if "filter_text" not in st.session_state:
            st.session_state.filter_text = qp.get("filter", "")
        filter_text = st.text_input(
            "Filter tickers", placeholder="e.g. AAPL  (press / to focus)",
            label_visibility="collapsed", key="filter_text",
        ).strip()

        st.markdown('<div class="mp-section-label">Lookup</div>', unsafe_allow_html=True)
        ticker_input = st.text_input(
            "Ticker", placeholder="AAPL (Enter)", label_visibility="collapsed",
            key="ticker_input",
        ).upper()

        # Clear selection
        if st.session_state.selected_ticker or st.session_state.compare_tickers:
            if st.button("✕ Clear selection", use_container_width=True, key="clear_sel"):
                st.session_state.selected_ticker = None
                st.session_state.compare_tickers = []
                st.rerun()

        # Watchlists
        st.markdown('<div class="mp-section-label">Watchlists</div>', unsafe_allow_html=True)
        watchlists = load_watchlists()
        wl_names = ["—"] + sorted(watchlists.keys())
        chosen = st.selectbox("Watchlist", wl_names, label_visibility="collapsed", key="wl_pick")
        if chosen and chosen != "—":
            wl_tickers = watchlists[chosen]
            st.markdown(
                f'<div style="font-size:0.78rem;color:{MUTED};margin:4px 0 6px 0;">'
                f'{len(wl_tickers)} tickers · {", ".join(wl_tickers[:4])}{"…" if len(wl_tickers) > 4 else ""}</div>',
                unsafe_allow_html=True,
            )
            cw1, cw2 = st.columns(2)
            with cw1:
                if st.button("Compare", use_container_width=True, key="wl_compare"):
                    st.session_state.compare_tickers = wl_tickers
                    st.session_state.selected_ticker = None
                    st.rerun()
            with cw2:
                if st.button("Delete", use_container_width=True, key="wl_delete"):
                    del watchlists[chosen]
                    save_watchlists(watchlists)
                    st.rerun()

        # Save current selection as a watchlist
        active = st.session_state.compare_tickers or (
            [st.session_state.selected_ticker] if st.session_state.selected_ticker else []
        )
        if active:
            new_name = st.text_input(
                "Save as", placeholder="my-watchlist", key="wl_save_name",
                label_visibility="collapsed",
            ).strip()
            if new_name and st.button("💾 Save current", use_container_width=True, key="wl_save_btn"):
                watchlists[new_name] = active
                save_watchlists(watchlists)
                st.success(f"Saved {new_name}")
                st.rerun()

    universe = get_universe()
    tickers = universe["ticker"].tolist()

    # Run all scans once (cached 5 min) — all tabs read from this
    with st.spinner("Running scanners..."):
        scans = run_all_scanners(tuple(tickers))

    runaway_results = scans["Momentum"]
    bullish_results = scans["Reversal"]
    bearish_results = scans["Caution"]
    gap_normal_results = scans["Fade"]
    scanned_at_label = time_ago(scans.get("_scanned_at", ""))

    # Build set of all tickers in any saved watchlist (used for ★ markers)
    all_watchlists = load_watchlists()
    watchlist_tickers_set: set = set()
    for wl in all_watchlists.values():
        watchlist_tickers_set.update(wl)

    # Persist current selection to URL so it survives reload / can be shared
    new_params = {}
    if st.session_state.selected_ticker:
        new_params["ticker"] = st.session_state.selected_ticker
    if st.session_state.compare_tickers:
        new_params["compare"] = ",".join(st.session_state.compare_tickers)
    if layout_mode != "Tabs":
        new_params["layout"] = layout_mode
    if filter_text:
        new_params["filter"] = filter_text
    # Update query params if changed (avoids unnecessary reruns)
    if dict(st.query_params) != new_params:
        st.query_params.clear()
        for k, v in new_params.items():
            st.query_params[k] = v

    # Strategy details — used in both layout modes
    SCANNER_INFO = {
        "Momentum": "Gap up + volume ≥ 1.3× 30d avg",
        "Reversal": "New 52w low + RSI higher than at prior low",
        "Caution": "New 52w high + RSI lower than at prior high",
        "Fade": "Gap up + light volume + open below 50/100/200 MA",
    }

    def render_scanner_section(name, results, fixed_height=None):
        ts_chip = (
            f"<div style='color:{MUTED};font-size:0.72rem;font-family:JetBrains Mono,monospace;"
            f"margin-left:auto;'>scanned {scanned_at_label}</div>"
            if scanned_at_label and not fixed_height else ""
        )
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:14px;margin:6px 0 14px 0;'>"
            f"<div style='display:inline-block;padding:6px 14px;background:rgba(0,217,255,0.08);"
            f"border:1px solid rgba(0,217,255,0.3);border-radius:20px;color:{ACCENT};"
            f"font-family:JetBrains Mono,monospace;font-weight:600;font-size:0.85rem;'>"
            f"{len(results)} signals</div>"
            f"<div style='color:{MUTED};font-size:0.82rem;'>{SCANNER_INFO[name]}</div>"
            f"{ts_chip}"
            f"</div>",
            unsafe_allow_html=True,
        )
        scanner_results_table(
            name, results, filter_text=filter_text, fixed_height=fixed_height,
            watchlist_tickers=watchlist_tickers_set,
        )

    if layout_mode == "2×2 Grid":
        # Compact 2x2 grid — fixed cell height so all four scanners line up
        # regardless of which ones have signals.
        GRID_CELL_HEIGHT = 320
        st.markdown('<div class="mp-section-label">All Scanners</div>', unsafe_allow_html=True)
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("#### Momentum")
            render_scanner_section("Momentum", runaway_results, fixed_height=GRID_CELL_HEIGHT)
            st.markdown("#### Caution")
            render_scanner_section("Caution", bearish_results, fixed_height=GRID_CELL_HEIGHT)
        with g2:
            st.markdown("#### Reversal")
            render_scanner_section("Reversal", bullish_results, fixed_height=GRID_CELL_HEIGHT)
            st.markdown("#### Fade")
            render_scanner_section("Fade", gap_normal_results, fixed_height=GRID_CELL_HEIGHT)
    else:
        # Tabs view (default)
        tab_overview, tab_all, tab_momentum, tab_reversal, tab_caution, tab_fade, tab_trades, tab_backtest = st.tabs([
            "📊 Overview", "All Signals", "Momentum", "Reversal", "Caution", "Fade",
            "💼 Trades", "🧪 Backtest",
        ])

        with tab_overview:
            total_flags = len(runaway_results) + len(bullish_results) + len(bearish_results) + len(gap_normal_results)
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.markdown(metric_tile("Universe", f"{len(tickers):,}", "tickers tracked"), unsafe_allow_html=True)
            with c2:
                st.markdown(metric_tile("Total Signals", f"{total_flags}", "across all scanners",
                                        color="bull" if total_flags > 0 else "default"), unsafe_allow_html=True)
            with c3:
                st.markdown(metric_tile("Bullish", f"{len(runaway_results) + len(bullish_results)}",
                                        "momentum + reversal", color="bull"), unsafe_allow_html=True)
            with c4:
                st.markdown(metric_tile("Bearish", f"{len(bearish_results) + len(gap_normal_results)}",
                                        "caution + fade", color="bear"), unsafe_allow_html=True)

            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='color:{MUTED};font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:10px;'>Scanner Breakdown</div>", unsafe_allow_html=True)

            sc1, sc2 = st.columns(2)
            with sc1:
                st.markdown(scanner_card("Momentum", len(runaway_results), "Gap up · heavy volume"), unsafe_allow_html=True)
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                st.markdown(scanner_card("Reversal", len(bullish_results), "52w low · higher RSI"), unsafe_allow_html=True)
            with sc2:
                st.markdown(scanner_card("Caution", len(bearish_results), "52w high · lower RSI"), unsafe_allow_html=True)
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                st.markdown(scanner_card("Fade", len(gap_normal_results), "Gap up · light volume · below MAs"), unsafe_allow_html=True)

            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            ovh1, ovh2 = st.columns([1, 1])
            with ovh1:
                render_sector_heatmap(universe, scans)
            with ovh2:
                render_signal_density(days=90)

        with tab_all:
            all_signals_table(scans, filter_text=filter_text, watchlist_tickers=watchlist_tickers_set)

        with tab_momentum:
            render_scanner_section("Momentum", runaway_results)
        with tab_reversal:
            render_scanner_section("Reversal", bullish_results)
        with tab_caution:
            render_scanner_section("Caution", bearish_results)
        with tab_fade:
            render_scanner_section("Fade", gap_normal_results)
        with tab_trades:
            render_trades_tab(owner=st.session_state.get("username", "anon"))
        with tab_backtest:
            render_backtest_tab(tickers)

    # Compare view (multi-select from scanner tables, or watchlist "Compare" button)
    if st.session_state.compare_tickers:
        st.markdown(
            f'<div style="height:1px;background:{BORDER};margin:32px 0 20px 0;"></div>',
            unsafe_allow_html=True,
        )
        compare_view(st.session_state.compare_tickers)
    # Detail view (single selection from table or typed in sidebar)
    elif ticker_input or st.session_state.selected_ticker:
        ticker_to_show = st.session_state.selected_ticker or ticker_input
        st.markdown(
            f'<div style="height:1px;background:{BORDER};margin:32px 0 20px 0;"></div>',
            unsafe_allow_html=True,
        )
        detail_view(ticker_to_show, scans=scans)


if __name__ == "__main__":
    main()
