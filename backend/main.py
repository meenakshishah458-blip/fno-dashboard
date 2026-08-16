import os
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

import upstox_api
from fno_symbols import get_fno_universe
from indicators import candles_to_df, build_stock_row

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fno-dashboard")

API_KEY = os.getenv("UPSTOX_API_KEY", "")
API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
ADMIN_KEY = os.getenv("DASHBOARD_ADMIN_KEY", "")  # required to call /api/set-token

# If you only have a manually-generated access token (no API key/secret for the
# OAuth flow), it loads once here at startup. Since Upstox tokens expire daily
# around 3:30am IST, you'll refresh it each morning via POST /api/set-token
# instead of the /api/login-url flow.
upstox_api.set_access_token(os.getenv("UPSTOX_ACCESS_TOKEN", ""))

# How many F&O stocks to track at once. Upstox market-quote/quotes allows ~500
# instrument_keys per call, but historical-candle calls are 1-per-symbol, so keep
# this reasonable to stay comfortably inside API rate limits.
MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "60"))

app = FastAPI(title="NSE F&O Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your frontend's origin before going live
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE = {
    "rows": [],
    "indices": {},
    "last_updated": None,
    "error": None,
}


def fetch_and_compute():
    if not upstox_api.ACCESS_TOKEN:
        STATE["error"] = "Not logged in yet - visit /api/login-url and complete the Upstox OAuth flow."
        log.warning(STATE["error"])
        return

    try:
        universe = get_fno_universe()
        symbols = list(universe.items())[:MAX_SYMBOLS]

        today = datetime.now().date()
        daily_from = (today - timedelta(days=400)).isoformat()  # >12mo for monthly pivot
        hist_15m_from = (today - timedelta(days=5)).isoformat()  # a few days for EMA/RSI warmup

        rows = []
        for symbol, keys in symbols:
            eq_key = keys["equity_key"]
            try:
                daily_candles = upstox_api.get_daily_candles(eq_key, daily_from, today.isoformat())
                hist_15m = upstox_api.get_historical_candles_15m(eq_key, hist_15m_from, today.isoformat())
                intraday_15m = upstox_api.get_intraday_candles_15m(eq_key)

                daily_df = candles_to_df(daily_candles)
                intraday_df = candles_to_df(intraday_15m)
                # stitch recent history + today so EMA/RSI have a proper lookback window
                full_df = candles_to_df(list(reversed(hist_15m)) + list(reversed(intraday_15m)))

                row = build_stock_row(symbol, daily_df, intraday_df, full_df)
                rows.append(row)
            except Exception as e:
                log.warning(f"Skipping {symbol}: {e}")
                continue

        STATE["rows"] = rows

        # indices strip
        index_quotes = upstox_api.get_full_market_quotes(list(upstox_api.INDEX_KEYS.values()))
        indices = {}
        for name, key in upstox_api.INDEX_KEYS.items():
            q = index_quotes.get(key.replace("|", ":"), {}) or index_quotes.get(key, {})
            indices[name] = {
                "ltp": q.get("last_price"),
                "change": q.get("net_change"),
            }
        STATE["indices"] = indices

        STATE["last_updated"] = datetime.now().isoformat()
        STATE["error"] = None
        log.info(f"Dashboard refreshed: {len(rows)} symbols at {STATE['last_updated']}")

    except Exception as e:
        STATE["error"] = str(e)
        log.exception("Refresh failed")


scheduler = BackgroundScheduler()
scheduler.add_job(fetch_and_compute, "interval", minutes=15, id="refresh", next_run_time=datetime.now())
scheduler.start()


@app.get("/")
def health():
    return {"status": "ok", "logged_in": bool(upstox_api.ACCESS_TOKEN), "last_updated": STATE["last_updated"]}


class TokenUpdate(BaseModel):
    access_token: str


@app.post("/api/set-token")
def set_token(payload: TokenUpdate, x_admin_key: str = Header(default="")):
    """
    Manual daily refresh: paste today's Upstox access token here each morning.
    Protected by DASHBOARD_ADMIN_KEY so randoms can't overwrite your token.
    """
    if not ADMIN_KEY or x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Missing or wrong X-Admin-Key header")
    upstox_api.set_access_token(payload.access_token)
    fetch_and_compute()
    return {"status": "token updated", "last_updated": STATE["last_updated"], "error": STATE["error"]}


@app.get("/api/login-url")
def login_url():
    if not API_KEY:
        raise HTTPException(500, "Set UPSTOX_API_KEY in your .env first")
    return {"url": upstox_api.get_login_url(API_KEY, REDIRECT_URI)}


@app.get("/api/auth/callback")
def auth_callback(code: str):
    """Upstox redirects here with ?code=... after you approve login in the browser."""
    token = upstox_api.exchange_code_for_token(API_KEY, API_SECRET, REDIRECT_URI, code)
    fetch_and_compute()  # kick off an immediate refresh now that we're authenticated
    return {"status": "logged in", "token_preview": token[:12] + "..."}


@app.get("/api/dashboard")
def dashboard():
    return {
        "rows": STATE["rows"],
        "indices": STATE["indices"],
        "last_updated": STATE["last_updated"],
        "error": STATE["error"],
    }


@app.post("/api/refresh")
def manual_refresh():
    fetch_and_compute()
    return {"status": "refreshed", "last_updated": STATE["last_updated"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
