[README.md](https://github.com/user-attachments/files/31115522/README.md)
# NSE F&O Live Dashboard (Upstox API)

Live table of all current NSE F&O stocks with Monthly/Weekly/Daily Pivot, EMA 21,
26-candle High, VWAP, RSI 14, EMA21-on-RSI (all on 15-min candles), plus a
NIFTY / BANK NIFTY / FIN NIFTY / INDIA VIX strip at the top.

## 1. Create an Upstox developer app

1. Go to https://developer.upstox.com/ and log in with your Upstox account.
2. Create a new app. Set the **Redirect URI** to `http://localhost:8000/api/auth/callback`
   (must match exactly what's in your `.env`).
3. Copy the **API Key** and **API Secret**.

## 2. Backend setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in `UPSTOX_API_KEY` and `UPSTOX_API_SECRET`.

```bash
python main.py
```

Backend runs at `http://localhost:8000`.

## 3. Add your Analytics Token

Upstox now offers an **Analytics Token** (Developer Apps → Analytics tab →
Generate Token) — read-only, valid for a full year, no daily refresh needed.
This is the easiest option and what these instructions assume.

Put it in `.env` as `UPSTOX_ACCESS_TOKEN` and set a `DASHBOARD_ADMIN_KEY` (any
long random string — protects the manual-refresh endpoint below, kept as a
fallback). That's it — no daily login required for up to a year.

*(If your token ever gets revoked or expires, generate a fresh one from the
same Developer Apps page and update it without redeploying:)*

```bash
curl -X POST https://YOUR-BACKEND-URL/api/set-token \
  -H "X-Admin-Key: your_dashboard_admin_key" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "PASTE_NEW_TOKEN_HERE"}'
```

## 4. Open the dashboard

Just open `frontend/index.html` in a browser (double-click it, or serve it with
any static file server). It polls `http://localhost:8000/api/dashboard` every 30s.

## How it works

- `fno_symbols.py` — pulls the *current* NSE F&O stock list from Upstox's
  instrument master (so it stays correct as the F&O list changes every few months).
- `upstox_api.py` — thin wrapper over Upstox V2 (auth, daily candles, quotes)
  and V3 (15-min candles) REST endpoints.
- `indicators.py` — all the math: floor pivots (monthly/weekly/daily), EMA,
  RSI, session VWAP, 26-candle breakout level, and a simple BUY/SELL/WATCH signal.
- `main.py` — FastAPI app. A background job refreshes every 15 minutes
  (matching your timeframe) and caches results in memory; the frontend just polls it.

## Deploying so it runs 24/7 (not just on your laptop)

The backend needs to run continuously (it's a background scheduler, not a
one-shot script), so it needs a host that keeps a process alive — Railway or
Render both have generous free/cheap tiers and are the simplest for FastAPI.

### Backend → Railway

1. Push this whole `fno-dashboard` folder to a GitHub repo (a new one, or a
   folder inside an existing repo — Railway can deploy from a subdirectory).
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo** →
   pick the repo → set **Root Directory** to `backend`.
3. Railway auto-detects the `Procfile` and `requirements.txt`. Deploy.
4. Under **Variables**, add:
   - `UPSTOX_ACCESS_TOKEN` = your Analytics Token (valid 1 year, no daily refresh)
   - `DASHBOARD_ADMIN_KEY` = a long random string you make up (fallback refresh only)
   - `MAX_SYMBOLS` = `60` (raise later)
5. Railway gives you a public URL like `https://your-app.up.railway.app`.
   Visit it — you should see `{"status": "ok", ...}`.
6. Each morning, refresh the token by calling `/api/set-token` on that URL
   (see step 3 above) — for example with a saved Postman request, or a one-line
   shortcut on your phone/desktop.

*(Render works the same way — New → Web Service → connect repo → root dir
`backend` → build command `pip install -r requirements.txt` → start command
`uvicorn main:app --host 0.0.0.0 --port $PORT`.)*

### Frontend → Netlify (or GitHub Pages, like you already use for SocialFlow)

1. In `frontend/index.html`, change this line to your Railway URL:
   ```js
   const API_BASE = "https://your-app.up.railway.app";
   ```
2. Also update `CORS` in `backend/main.py` — change `allow_origins=["*"]` to
   your actual frontend URL once you know it, so random sites can't hit your API.
3. Deploy `frontend/index.html` as a static site — easiest options:
   - **Netlify**: drag-and-drop the `frontend` folder at https://app.netlify.com/drop
   - **GitHub Pages**: same setup as your SocialFlow repo — push `frontend/`
     to a repo and enable Pages on it.

Once both are deployed, your dashboard is live at your Netlify/Pages URL and
pulls fresh data from your always-on Railway backend, refreshing every 15 minutes.

## Tuning

- `MAX_SYMBOLS` in `.env` controls how many F&O stocks are tracked at once
  (start at 60, raise once you've confirmed you're within Upstox's API rate limits).
- The BUY/SELL/WATCH logic in `indicators.py::compute_signal()` is a starting
  point — tell me your exact entry/exit rules and I'll tighten it.

## Known gaps to close before relying on this live

- Daily OAuth login is manual right now (see step 3) — can be automated with TOTP.
- Index instrument keys (`upstox_api.py::INDEX_KEYS`) should be double-checked
  against Upstox's instrument master the first time you run this, since exact
  index naming has changed before.
- No persistence yet (in-memory cache only) — fine for a live dashboard, but if
  you want historical signal logs, that needs a database (SocialFlow already
  uses Supabase, could reuse that).
- Analytics Token API coverage has had some rollout inconsistencies reported
  by other developers (a few endpoints listed as "supported" in docs return
  errors in practice). Historical candle data works reliably; if the index
  strip (`/api/dashboard` → `indices`) comes back empty once deployed, that's
  the most likely spot — tell me and I'll adjust `get_full_market_quotes()`.
