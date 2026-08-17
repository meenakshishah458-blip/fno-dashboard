"""
All indicator math lives here, kept independent of the Upstox client so it's
easy to unit-test with sample DataFrames.

Candle rows coming from Upstox look like:
["2023-10-19T15:15:00+05:30", open, high, low, close, volume, oi]
"""
import pandas as pd
import numpy as np

CANDLE_COLS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]


def candles_to_df(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=CANDLE_COLS)
    df = pd.DataFrame(candles, columns=CANDLE_COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def floor_pivot(prev_high: float, prev_low: float, prev_close: float) -> float:
    return round((prev_high + prev_low + prev_close) / 3, 2)


def daily_pivot(daily_df: pd.DataFrame) -> float | None:
    """Uses the last fully completed trading day (row -1, since today isn't closed yet)."""
    if len(daily_df) < 2:
        return None
    prev = daily_df.iloc[-2]  # second-to-last row = yesterday, if today's partial day is included
    return floor_pivot(prev["high"], prev["low"], prev["close"])


def weekly_pivot(daily_df: pd.DataFrame) -> float | None:
    if daily_df.empty:
        return None
    df = daily_df.set_index("timestamp")
    weekly = df.resample("W-FRI").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    if len(weekly) < 2:
        return None
    prev = weekly.iloc[-2]
    return floor_pivot(prev["high"], prev["low"], prev["close"])


def monthly_pivot(daily_df: pd.DataFrame) -> float | None:
    if daily_df.empty:
        return None
    df = daily_df.set_index("timestamp")
    monthly = df.resample("ME").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    if len(monthly) < 2:
        return None
    prev = monthly.iloc[-2]
    return floor_pivot(prev["high"], prev["low"], prev["close"])


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)  # neutral when no data yet


def session_vwap(intraday_15m_df: pd.DataFrame) -> float | None:
    """Resets every session - only uses today's candles, which is what the intraday
    endpoint already returns."""
    if intraday_15m_df.empty:
        return None
    df = intraday_15m_df.copy()
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    vwap_series = cum_tp_vol / cum_vol
    return round(vwap_series.iloc[-1], 2)


def candle_high_lookback(df_15m: pd.DataFrame, lookback: int = 26) -> float | None:
    """Highest High of the previous N *completed* 15-min candles, excluding the
    current in-progress one."""
    if len(df_15m) < 2:
        return None
    completed = df_15m.iloc[:-1]  # drop the live/incomplete last candle
    window = completed.tail(lookback)
    if window.empty:
        return None
    return round(window["high"].max(), 2)


def build_stock_row(symbol: str, daily_df: pd.DataFrame, intraday_15m_df: pd.DataFrame,
                     full_15m_df: pd.DataFrame) -> dict:
    """
    daily_df        - daily candles, enough history for monthly pivot (~13 months)
    intraday_15m_df - today's 15-min candles only (for VWAP, which resets daily)
    full_15m_df     - intraday_15m_df stitched onto recent historical 15-min candles
                       (needed so EMA21/RSI14/EMA21-of-RSI have enough lookback,
                       since those shouldn't reset at 9:15am every day)
    """
    row = {
        "symbol": symbol,
        "monthly_pivot": monthly_pivot(daily_df),
        "weekly_pivot": weekly_pivot(daily_df),
        "daily_pivot": daily_pivot(daily_df),
        "vwap": session_vwap(intraday_15m_df),
        "high_26_candle": candle_high_lookback(intraday_15m_df, 26),
    }

    if not full_15m_df.empty and len(full_15m_df) >= 21:
        close = full_15m_df["close"]
        row["ema21"] = round(ema(close, 21).iloc[-1], 2)
        rsi_series = rsi(close, 14)
        row["rsi14"] = round(rsi_series.iloc[-1], 2)
        row["ema21_on_rsi"] = round(ema(rsi_series, 21).iloc[-1], 2)
        row["ltp"] = round(close.iloc[-1], 2)
    else:
        row.update({"ema21": None, "rsi14": None, "ema21_on_rsi": None, "ltp": None})

    # % change vs previous session's close (for the CHANGE % column / sort)
    if row["ltp"] is not None and len(daily_df) >= 2:
        prev_close = daily_df.iloc[-2]["close"]
        if prev_close:
            row["change_pct"] = round((row["ltp"] - prev_close) / prev_close * 100, 2)
        else:
            row["change_pct"] = None
    else:
        row["change_pct"] = None

    row["signal"] = compute_signal(row)
    return row


def compute_signal(row: dict) -> str:
    required = ["ltp", "daily_pivot", "vwap", "ema21", "rsi14", "ema21_on_rsi", "high_26_candle"]
    if any(row.get(k) is None for k in required):
        return "NO SIGNAL"

    bullish = (
        row["ltp"] > row["daily_pivot"]
        and row["ltp"] > row["vwap"]
        and row["ltp"] > row["ema21"]
        and row["rsi14"] > row["ema21_on_rsi"]
    )
    bearish = (
        row["ltp"] < row["daily_pivot"]
        and row["ltp"] < row["vwap"]
        and row["ltp"] < row["ema21"]
        and row["rsi14"] < row["ema21_on_rsi"]
    )
    breakout = row["ltp"] > row["high_26_candle"]

    if bullish and breakout:
        return "BUY"
    if bearish:
        return "SELL"
    if bullish:
        return "WATCH"
    return "NO SIGNAL"
