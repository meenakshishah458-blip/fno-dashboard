"""
Thin wrapper around Upstox V2/V3 REST APIs.
Docs: https://upstox.com/developer/api-documentation/
"""
import os
import requests
from datetime import datetime, timedelta

BASE_V2 = "https://api.upstox.com/v2"
BASE_V3 = "https://api.upstox.com/v3"

ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")


def _headers():
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {ACCESS_TOKEN}",
    }


def set_access_token(token: str):
    """Called after the daily OAuth login exchange to refresh the in-memory token."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = token


def get_login_url(api_key: str, redirect_uri: str) -> str:
    return (
        f"{BASE_V2}/login/authorization/dialog"
        f"?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    )


def exchange_code_for_token(api_key: str, api_secret: str, redirect_uri: str, code: str) -> str:
    """Step 2 of Upstox OAuth. Run once a day (tokens expire ~3:30am IST daily)."""
    url = f"{BASE_V2}/login/authorization/token"
    payload = {
        "code": code,
        "client_id": api_key,
        "client_secret": api_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    headers = {"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(url, data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    set_access_token(token)
    return token


def get_intraday_candles_15m(instrument_key: str):
    """Today's 15-min candles so far (V3 intraday endpoint)."""
    url = f"{BASE_V3}/historical-candle/intraday/{instrument_key}/minutes/15"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("candles", [])


def get_historical_candles_15m(instrument_key: str, from_date: str, to_date: str):
    """
    Past 15-min candles between from_date and to_date (YYYY-MM-DD).
    V3 keeps ~1 month of minute-level history.
    """
    url = f"{BASE_V3}/historical-candle/{instrument_key}/minutes/15/{to_date}/{from_date}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("candles", [])


def get_daily_candles(instrument_key: str, from_date: str, to_date: str):
    """Used to build monthly/weekly/daily pivots."""
    url = f"{BASE_V2}/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("candles", [])


def get_full_market_quotes(instrument_keys: list[str]):
    """Live LTP + OHLC snapshot for up to ~500 keys per call (pipe-separated)."""
    url = f"{BASE_V2}/market-quote/quotes"
    joined = ",".join(instrument_keys)
    resp = requests.get(url, headers=_headers(), params={"instrument_key": joined}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {})


# Known index instrument keys (verify against the Upstox instrument master if these
# ever stop resolving - index naming has changed before, e.g. Nifty Fin Service).
INDEX_KEYS = {
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "BANK NIFTY": "NSE_INDEX|Nifty Bank",
    "FIN NIFTY": "NSE_INDEX|Nifty Fin Service",
    "INDIA VIX": "NSE_INDEX|India VIX",
}
