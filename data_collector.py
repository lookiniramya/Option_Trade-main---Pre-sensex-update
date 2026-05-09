#!/usr/bin/env python3
"""
data_collector.py — OptionsIQ Background Data Collector
========================================================
Run this ONCE at market open. It runs independently of your browser and
collects option chain, live price, PCR, and computed indicators every 30
seconds into a local SQLite database.

When you open/close/reopen the browser app, it loads from this database
so no data is ever lost between browser sessions.

Usage:
    python data_collector.py --token YOUR_ACCESS_TOKEN --symbol NIFTY --expiry 08-05-2026

Or set environment variables:
    set PAYTM_TOKEN=your_token
    set OPTIONSIQ_SYMBOL=NIFTY
    set OPTIONSIQ_EXPIRY=08-05-2026
    python data_collector.py
"""

import os
import sys
import time

# Load .env file if present (Oracle VM deployment)
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()
import json
import sqlite3
import logging
import argparse
import signal
from datetime import datetime, timedelta
from pathlib import Path

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("collector.log", mode="a"),
    ],
)
log = logging.getLogger("collector")

# ── Database path ─────────────────────────────────────────────────────────────
DB_PATH = Path("optionsiq_data.db")

# ── Collection intervals ──────────────────────────────────────────────────────
CHAIN_INTERVAL_SEC  = 30   # Full option chain fetch every 30s
PRICE_INTERVAL_SEC  = 5    # Live price fetch every 5s
HISTORY_DAYS        = 2    # Keep 2 days of data (auto-purge older)

# ── Market hours (IST) ────────────────────────────────────────────────────────
MARKET_OPEN_H,  MARKET_OPEN_M  = 9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30


# ═════════════════════════════════════════════════════════════════════════════
# Database setup
# ═════════════════════════════════════════════════════════════════════════════

def init_db(db_path: Path) -> sqlite3.Connection:
    """Create tables if they don't exist."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # allows concurrent reads
    conn.execute("PRAGMA synchronous=NORMAL") # faster writes

    conn.executescript("""
    -- Live price snapshots (every 5s)
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        ltp         REAL,
        open        REAL,
        high        REAL,
        low         REAL,
        prev_close  REAL,
        change      REAL,
        change_pct  REAL,
        source      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_price_ts ON price_snapshots(ts);

    -- Option chain snapshots (every 30s) — stored as JSON
    CREATE TABLE IF NOT EXISTS chain_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        expiry      TEXT NOT NULL,
        calls_json  TEXT,
        puts_json   TEXT,
        spot_price  REAL
    );
    CREATE INDEX IF NOT EXISTS idx_chain_ts ON chain_snapshots(ts);

    -- PCR history (every 30s) — for reversal detector
    CREATE TABLE IF NOT EXISTS pcr_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        pcr_oi      REAL,
        pcr_vol     REAL,
        sentiment   REAL,
        ltp         REAL
    );
    CREATE INDEX IF NOT EXISTS idx_pcr_ts ON pcr_history(ts);

    -- Indicator snapshots (every 30s)
    CREATE TABLE IF NOT EXISTS indicator_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        sentiment_score REAL,
        sentiment_label TEXT,
        pcr_oi          REAL,
        pcr_vol         REAL,
        support         REAL,
        resistance      REAL,
        max_pain        REAL,
        oi_momentum     TEXT,
        iv_skew         REAL,
        scalp_action    TEXT,
        scalp_confidence INTEGER,
        indicators_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ind_ts ON indicator_snapshots(ts);

    -- Tick history for candle building (from WebSocket)
    CREATE TABLE IF NOT EXISTS tick_history (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      TEXT NOT NULL,
        symbol  TEXT NOT NULL,
        ltp     REAL,
        volume  REAL
    );
    CREATE INDEX IF NOT EXISTS idx_tick_ts ON tick_history(ts);

    -- Session metadata
    CREATE TABLE IF NOT EXISTS collector_sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts    TEXT,
        end_ts      TEXT,
        symbol      TEXT,
        expiry      TEXT,
        total_chain_fetches INTEGER DEFAULT 0,
        total_price_fetches INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    log.info(f"Database initialised at {db_path.absolute()}")
    return conn


def purge_old_data(conn: sqlite3.Connection):
    """Remove data older than HISTORY_DAYS to keep DB size manageable."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).isoformat()
    tables = ["price_snapshots", "chain_snapshots", "pcr_history",
              "indicator_snapshots", "tick_history"]
    for table in tables:
        cur = conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
        if cur.rowcount > 0:
            log.info(f"Purged {cur.rowcount} rows from {table}")
    conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
# Data collection
# ═════════════════════════════════════════════════════════════════════════════

def is_market_hours() -> bool:
    now = datetime.now()
    open_t  = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0)
    close_t = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0)
    return open_t <= now <= close_t


def fetch_and_store_price(conn, token, symbol):
    """Fetch live price and store in DB."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from market_data import fetch_live_price
        live = fetch_live_price(token, symbol, {})
        ltp = float(live.get("ltp", 0) or 0)
        if ltp <= 0:
            return None

        ts = datetime.now().isoformat()
        conn.execute("""
            INSERT INTO price_snapshots
            (ts, symbol, ltp, open, high, low, prev_close, change, change_pct, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, symbol, ltp,
            float(live.get("open", 0) or 0),
            float(live.get("high", 0) or 0),
            float(live.get("low", 0) or 0),
            float(live.get("prev_close", 0) or 0),
            float(live.get("change", 0) or 0),
            float(live.get("change_pct", 0) or 0),
            str(live.get("source", "")),
        ))
        conn.commit()
        return live
    except Exception as e:
        log.warning(f"Price fetch error: {e}")
        return None


def fetch_and_store_chain(conn, token, symbol, expiry):
    """Fetch full option chain and store in DB."""
    try:
        from api_client import fetch_option_chain_both
        from analytics import compute_pcr, compute_market_snapshot
        from market_data import compute_technical_indicators, compute_market_sentiment
        import pandas as pd

        data, err = fetch_option_chain_both(token, symbol, expiry)
        if err or not data:
            log.warning(f"Chain fetch error: {err}")
            return None

        df_calls = pd.DataFrame(data.get("calls", []))
        df_puts  = pd.DataFrame(data.get("puts",  []))

        for df in [df_calls, df_puts]:
            for col in ["strike_price","price","oi","oi_perc_chg","net_chg","volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        spot = float(data.get("spot_price", 0) or 0)
        if spot == 0 and not df_calls.empty and "strike_price" in df_calls.columns:
            strikes = sorted(df_calls["strike_price"].dropna().tolist())
            spot = float(strikes[len(strikes)//2]) if strikes else 0

        ts = datetime.now().isoformat()

        # Store chain snapshot
        conn.execute("""
            INSERT INTO chain_snapshots
            (ts, symbol, expiry, calls_json, puts_json, spot_price)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ts, symbol, expiry,
            df_calls.to_json(orient="records"),
            df_puts.to_json(orient="records"),
            spot,
        ))

        # Compute and store indicators
        pcr_data = compute_pcr(df_calls, df_puts) if not df_calls.empty else {}
        snapshot = compute_market_snapshot(df_calls, df_puts) if not df_calls.empty else {}
        live_stub = {"ltp": spot, "change_pct": 0, "source": "collector"}
        ind = compute_technical_indicators(
            data.get("calls", []), data.get("puts", []), spot, symbol
        ) if spot > 0 else {}
        sent = compute_market_sentiment(
            float(pcr_data.get("pcr_oi", 1.0) or 1.0), snapshot, ind, live_stub
        ) if pcr_data else {"score": 0, "sentiment": "NEUTRAL"}

        # PCR history entry
        conn.execute("""
            INSERT INTO pcr_history (ts, symbol, pcr_oi, pcr_vol, sentiment, ltp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ts, symbol,
            float(pcr_data.get("pcr_oi", 0) or 0),
            float(pcr_data.get("pcr_volume", 0) or 0),
            float(sent.get("score", 0) or 0),
            spot,
        ))

        # Compute support/resistance
        sup_strike = 0
        res_strike = 0
        max_pain   = float(snapshot.get("max_pain", 0) or 0)
        if not df_puts.empty and "oi" in df_puts.columns:
            max_put_row = df_puts.loc[df_puts["oi"].idxmax()]
            sup_strike = float(max_put_row.get("strike_price", 0))
        if not df_calls.empty and "oi" in df_calls.columns:
            max_call_row = df_calls.loc[df_calls["oi"].idxmax()]
            res_strike = float(max_call_row.get("strike_price", 0))

        conn.execute("""
            INSERT INTO indicator_snapshots
            (ts, symbol, sentiment_score, sentiment_label, pcr_oi, pcr_vol,
             support, resistance, max_pain, oi_momentum, iv_skew, indicators_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, symbol,
            float(sent.get("score", 0) or 0),
            str(sent.get("sentiment", "NEUTRAL")),
            float(pcr_data.get("pcr_oi", 0) or 0),
            float(pcr_data.get("pcr_volume", 0) or 0),
            sup_strike, res_strike, max_pain,
            str(ind.get("oi_momentum", "NEUTRAL")),
            float(ind.get("iv_skew", 0) or 0),
            json.dumps(ind),
        ))

        conn.commit()
        log.info(
            f"[{ts[11:19]}] Chain stored | "
            f"Spot: {spot:,.0f} | "
            f"PCR OI: {pcr_data.get('pcr_oi',0):.3f} | "
            f"Sentiment: {sent.get('score',0):+.0f} {sent.get('sentiment','')}"
        )
        return data

    except Exception as e:
        log.error(f"Chain fetch/store error: {e}", exc_info=True)
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Main loop
# ═════════════════════════════════════════════════════════════════════════════

def run_collector(token: str, symbol: str, expiry: str):
    """Main collection loop. Runs until killed."""
    conn = init_db(DB_PATH)
    purge_old_data(conn)

    # Register session
    session_id = conn.execute("""
        INSERT INTO collector_sessions (start_ts, symbol, expiry)
        VALUES (?, ?, ?)
    """, (datetime.now().isoformat(), symbol, expiry)).lastrowid
    conn.commit()

    chain_fetches = 0
    price_fetches = 0
    last_chain_ts = 0.0
    last_price_ts = 0.0

    log.info(f"Collector started | Symbol: {symbol} | Expiry: {expiry}")
    log.info(f"Database: {DB_PATH.absolute()}")
    log.info(f"Chain interval: {CHAIN_INTERVAL_SEC}s | Price interval: {PRICE_INTERVAL_SEC}s")
    log.info("Press Ctrl+C to stop")

    # Graceful shutdown handler
    running = [True]
    def _stop(sig, frame):
        log.info("Stopping collector...")
        running[0] = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running[0]:
        now = time.time()
        in_market = is_market_hours()

        # Price fetch (every 5s during market hours)
        if in_market and (now - last_price_ts) >= PRICE_INTERVAL_SEC:
            fetch_and_store_price(conn, token, symbol)
            last_price_ts = now
            price_fetches += 1

        # Chain fetch (every 30s during market hours)
        if in_market and (now - last_chain_ts) >= CHAIN_INTERVAL_SEC:
            fetch_and_store_chain(conn, token, symbol, expiry)
            last_chain_ts = now
            chain_fetches += 1

            # Update session stats
            conn.execute("""
                UPDATE collector_sessions
                SET total_chain_fetches=?, total_price_fetches=?, end_ts=?
                WHERE id=?
            """, (chain_fetches, price_fetches, datetime.now().isoformat(), session_id))
            conn.commit()

        # Outside market hours — just wait, no fetching
        if not in_market:
            log.debug("Outside market hours — waiting...")
            time.sleep(60)
            continue

        time.sleep(1)

    # Update session end time
    conn.execute("UPDATE collector_sessions SET end_ts=? WHERE id=?",
                 (datetime.now().isoformat(), session_id))
    conn.commit()
    conn.close()
    log.info(f"Collector stopped. Chain fetches: {chain_fetches}, Price fetches: {price_fetches}")


def main():
    parser = argparse.ArgumentParser(description="OptionsIQ background data collector")
    parser.add_argument("--token",   default=os.environ.get("PAYTM_TOKEN", ""),    help="Paytm access token")
    parser.add_argument("--symbol",  default=os.environ.get("OPTIONSIQ_SYMBOL", "NIFTY"), help="Symbol (NIFTY/BANKNIFTY)")
    parser.add_argument("--expiry",  default=os.environ.get("OPTIONSIQ_EXPIRY", ""), help="Expiry date DD-MM-YYYY")
    args = parser.parse_args()

    if not args.token:
        print("ERROR: Paytm access token required. Use --token or PAYTM_TOKEN env var.")
        sys.exit(1)
    if not args.expiry:
        print("ERROR: Expiry date required. Use --expiry DD-MM-YYYY or OPTIONSIQ_EXPIRY env var.")
        sys.exit(1)

    run_collector(args.token, args.symbol, args.expiry)


if __name__ == "__main__":
    main()
