"""Build and manage the universe of stocks eligible for screening.

Fetches the full NYSE+NASDAQ ticker list from NASDAQ Trader's free symbol files,
filters out non-common-stock instruments, and applies price/volume qualification.
"""

import io
from datetime import datetime
from typing import List

import pandas as pd
import requests
import yfinance as yf

from stock_screener.data.db import get_connection


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"


def _fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "stock-screener/0.1"})
    resp.raise_for_status()
    return resp.text


def fetch_listed_symbols() -> pd.DataFrame:
    """Fetch the NASDAQ + NYSE/AMEX symbol files. Returns a DataFrame with
    columns: ticker, exchange, name, etf, test_issue."""
    nas_raw = _fetch_text(NASDAQ_LISTED_URL)
    oth_raw = _fetch_text(OTHER_LISTED_URL)

    # NASDAQ file columns: Symbol|Security Name|Market Category|Test Issue|...|ETF|Round Lot Size|NextShares
    nas = pd.read_csv(io.StringIO(nas_raw), sep="|").iloc[:-1]  # drop "File Creation Time" trailer
    nas = nas.rename(columns={"Symbol": "ticker", "Security Name": "name",
                              "Test Issue": "test_issue", "ETF": "etf"})
    nas["exchange"] = "NASDAQ"

    # otherlisted columns: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
    oth = pd.read_csv(io.StringIO(oth_raw), sep="|").iloc[:-1]
    oth = oth.rename(columns={"ACT Symbol": "ticker", "Security Name": "name",
                              "Exchange": "exchange_code", "Test Issue": "test_issue", "ETF": "etf"})
    # exchange_code: A=AMEX, N=NYSE, P=NYSE Arca, Z=BATS
    exchange_map = {"A": "AMEX", "N": "NYSE", "P": "NYSEARCA", "Z": "BATS"}
    oth["exchange"] = oth["exchange_code"].map(exchange_map).fillna("OTHER")

    cols = ["ticker", "exchange", "name", "etf", "test_issue"]
    df = pd.concat([nas[cols], oth[cols]], ignore_index=True)

    # Drop test issues, ETFs, and instruments with non-common-stock indicators
    df = df[df["test_issue"] != "Y"]
    df = df[df["etf"] != "Y"]
    # Drop preferred shares, warrants, units, rights — common stock heuristic
    name_blacklist = (
        df["name"].str.contains(
            r"\b(?:Warrant|Right|Unit|Preferred|Note|Trust|Fund|Bond|Depositary)\b",
            case=False, regex=True, na=False,
        )
    )
    df = df[~name_blacklist]
    # Tickers with $ usually preferred series; ^ are special; / are warrants
    df = df[~df["ticker"].astype(str).str.contains(r"[\$\^/]", regex=True, na=False)]
    # Restrict to NYSE + NASDAQ only (skip BATS / NYSE Arca which are mostly ETFs)
    df = df[df["exchange"].isin(["NYSE", "NASDAQ"])]
    df = df.drop_duplicates(subset=["ticker"]).reset_index(drop=True)

    return df[["ticker", "exchange", "name"]]


def get_all_nyse_nasdaq_tickers() -> List[str]:
    """Return the full universe of common-stock tickers from NYSE + NASDAQ."""
    try:
        return fetch_listed_symbols()["ticker"].tolist()
    except Exception as e:
        print(f"Failed to fetch NASDAQ Trader symbol files: {e}")
        print("Falling back to a small static list.")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]


def _fetch_quick_quote(ticker: str) -> tuple[float | None, int | None]:
    """Fetch latest close and 30d avg volume cheaply via yfinance."""
    try:
        data = yf.download(ticker, period="60d", progress=False, auto_adjust=False)
        if data.empty or len(data) < 30:
            return None, None
        close_col = data["Close"][ticker] if isinstance(data.columns, pd.MultiIndex) else data["Close"]
        vol_col = data["Volume"][ticker] if isinstance(data.columns, pd.MultiIndex) else data["Volume"]
        return float(close_col.iloc[-1]), int(vol_col.tail(30).mean())
    except Exception:
        return None, None


def get_qualified_stocks(min_price: float = 5.0, min_avg_volume: int = 500000,
                        progress_callback=None) -> pd.DataFrame:
    """Filter stocks meeting price/volume criteria. Optional progress callback."""
    listed = fetch_listed_symbols()
    print(f"Screening {len(listed)} listed symbols (filter: ${min_price}+, {min_avg_volume:,}+ avg vol)...")

    qualified = []
    for i, row in enumerate(listed.itertuples(index=False)):
        ticker = row.ticker
        if (i + 1) % 100 == 0:
            print(f"  Progress: {i + 1}/{len(listed)}  (qualified so far: {len(qualified)})")
        if progress_callback:
            try:
                progress_callback(i + 1, len(listed), ticker)
            except Exception:
                pass

        price, avg_vol = _fetch_quick_quote(ticker)
        if price is None or avg_vol is None:
            continue
        if price >= min_price and avg_vol >= min_avg_volume:
            qualified.append({
                "ticker": ticker,
                "name": row.name,
                "exchange": row.exchange,
                "price": price,
                "avg_volume_30d": avg_vol,
                "timestamp": datetime.now().isoformat(),
            })

    df = pd.DataFrame(qualified)
    print(f"Qualified: {len(df)} stocks")
    return df


def fetch_sector(ticker: str) -> str | None:
    """Fetch sector via yfinance Ticker.info (slow). Returns None on failure."""
    try:
        info = yf.Ticker(ticker).info
        return info.get("sector") or None
    except Exception:
        return None


def update_universe(min_price: float = 5.0, min_avg_volume: int = 500000,
                    fetch_sectors: bool = False, progress_callback=None) -> None:
    """Recompute and store the universe. Optionally fetches sector tags (slow)."""
    df = get_qualified_stocks(min_price, min_avg_volume, progress_callback=progress_callback)
    if df.empty:
        print("No stocks qualified. Universe not updated.")
        return

    sectors: dict[str, str] = {}
    if fetch_sectors:
        print(f"Fetching sectors for {len(df)} stocks (slow)...")
        for i, t in enumerate(df["ticker"]):
            if (i + 1) % 25 == 0:
                print(f"  Sector progress: {i + 1}/{len(df)}")
            sectors[t] = fetch_sector(t)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM universe")
    for _, row in df.iterrows():
        cursor.execute(
            """INSERT INTO universe
               (ticker, last_updated, price, avg_volume_30d, sector, company_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["ticker"], row["timestamp"], row["price"], row["avg_volume_30d"],
             sectors.get(row["ticker"]), row.get("name")),
        )
    conn.commit()
    conn.close()
    print(f"Universe updated: {len(df)} stocks stored")


def add_to_universe(ticker: str) -> bool:
    """Add a single ticker to the universe (skips price/volume filter, used for manual add)."""
    ticker = ticker.upper().strip()
    if not ticker:
        return False
    price, avg_vol = _fetch_quick_quote(ticker)
    sector = fetch_sector(ticker)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO universe
           (ticker, last_updated, price, avg_volume_30d, sector, company_name)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ticker, datetime.now().isoformat(), price or 0, avg_vol or 0, sector, ticker),
    )
    conn.commit()
    conn.close()
    return True


def remove_from_universe(ticker: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM universe WHERE ticker = ?", (ticker.upper(),))
    conn.commit()
    conn.close()


def get_universe() -> pd.DataFrame:
    """Retrieve the current universe of qualified stocks from SQLite."""
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM universe ORDER BY ticker ASC", conn)
    conn.close()
    return df
