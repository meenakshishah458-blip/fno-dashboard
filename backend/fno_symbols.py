"""
NSE's F&O list changes every few months (stocks get added/removed based on SEBI
criteria), so instead of hardcoding symbols we pull the current list from
Upstox's own instrument master.
"""
import gzip
import json
import io
import time
import requests

INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

_cache = {"data": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 6 * 60 * 60  # refresh twice a day is plenty, this file is large


def _download_instrument_master() -> list:
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as f:
        return json.loads(f.read())


def get_fno_universe(force_refresh: bool = False) -> dict:
    """
    Returns {symbol: {"equity_key": "NSE_EQ|...", "underlying_key": "..."}}
    for every stock that currently has NSE_FO derivative contracts.
    """
    now = time.time()
    if not force_refresh and _cache["data"] and (now - _cache["fetched_at"] < CACHE_TTL_SECONDS):
        return _cache["data"]

    instruments = _download_instrument_master()

    fo_underlyings = set()
    equity_keys = {}

    for inst in instruments:
        segment = inst.get("segment", "")
        if segment == "NSE_FO" and inst.get("instrument_type") in ("FUT", "FUTSTK"):
            underlying = inst.get("underlying_symbol") or inst.get("underlying_key")
            if underlying:
                fo_underlyings.add(underlying)
        if segment == "NSE_EQ" and inst.get("instrument_type") == "EQ":
            equity_keys[inst.get("trading_symbol")] = inst.get("instrument_key")

    universe = {}
    for symbol in sorted(fo_underlyings):
        eq_key = equity_keys.get(symbol)
        if eq_key:
            universe[symbol] = {"equity_key": eq_key}

    _cache["data"] = universe
    _cache["fetched_at"] = now
    return universe
