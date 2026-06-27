#!/usr/bin/env python3.11
"""
VerstigeOS Unified P&L Dashboard — Data Aggregator
==================================================
Reads all 9 engine signal tables + canonical trade_outcomes + active trades
from src/lib/trading_bot/verstige_trades.db and writes a single
data/dashboard.json that dashboard.html renders.

Per-engine $ P&L is computed assuming:
  - $1,000 account per engine
  - Risk per trade = 1% of account = $10
  - Pip value scaled so that SL distance = $10 risk
  - $ P&L = (pips / |SL - entry|) * 10

Refresh: cron every 5 minutes. Browser fetches dashboard.json every 30s.
"""
import sqlite3
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────
import os
ROOT = Path(__file__).resolve().parent.parent
# Source DB lives on the host machine — default to the VerstigeOS-Official path.
# Override via VERSTIGETECH_DB env var if needed.
DB = Path(os.environ.get("VERSTIGETECH_DB", str(Path.home() / "Projects/VerstigeOS-Official/src/lib/trading_bot/verstige_trades.db")))
OUT = ROOT / "data" / "dashboard.json"
ACCOUNT_SIZE = 1000.0          # $ per engine
RISK_PCT = 0.01                # 1% risk per trade
RISK_USD = ACCOUNT_SIZE * RISK_PCT  # $10 per trade per engine
DAYS_BACK = 14                 # daily chart window

# ── Engine registry ──────────────────────────────────────────────────────
# (name, signal_table, active_table, symbol, strategy_label, accent_color)
ENGINES = [
    ("NITRO",     "gold_signals",  "gold_active_trades",     "XAUUSD",  "Gold SMC + EMA",       "#f59e0b", "-1003882720756", "OS Gold"),
    ("SURGE",     None,            None,                     "US30",    "US30 Momentum",        "#3b82f6", "-1003977021472", "US30 OS"),
    ("DRAGON",    None,            "forex_dragon_active",    "GBPJPY",  "GBP/JPY Reversal",     "#ef4444", "-1003766608078", "Forex OS"),
    ("TITAN",     None,            "forex_titan_active",     "EURUSD",  "EUR/USD Trend",        "#10b981", "-1003766608078", "Forex OS"),
    ("CIPHER",    "btc_signals",   "btc_active_trades",      "BTCUSD",  "BTC 4H SMC",           "#f97316", "-1004294035586", "Crypto OS"),
    ("PHANTOM",   "sol_signals",   "sol_active_trades",      "SOLUSD",  "SOL Weighted Score",   "#a855f7", "-1003882720756", "Crypto OS"),
    ("NEXUS",     "nexus_signals", None,                      "NAS100",  "NAS100 Prime Zone",    "#00d4ff", "-1003957008363", "NAS100 OS"),
    ("VOLTALITY", "vs_signals",    None,                      "US30",    "US30 Prime Zone",      "#7c3aed", "-1003639156197", "US30 OS"),
    ("FLUENCE",   "us30_signals",  "us30_active_trades",     "US30",    "US30 SMC",             "#ec4899", "-1003639156197", "US30 OS"),
]

# Per-engine canonical → strategy mapping for the shared `signals` table
# SURGE writes to canonical `signals` with symbol=XAUUSD but is the US30 engine.
# We differentiate by querying signals where created_at falls within engine active ranges.
# Simpler approach: SURGE = canonical signals with symbol='US30' (none today, see analysis)
# Strategy label uses the engine's own table where possible.


def table_has_column(cur, table, col):
    cur.execute(f'PRAGMA table_info("{table}")')
    return any(r[1] == col for r in cur.fetchall())


def compute_dollar_pnl(net_pips, entry, sl, symbol):
    """Convert pips to $ assuming $10 risk per trade.

    Pip unit per symbol:
      XAUUSD, NAS100, US30, BTCUSD, SOLUSD → 1.0
      EURUSD, GBPJPY                       → 0.0001 (FX 4-dec)
    SL distance is in price units; we convert to pips by dividing by pip_unit.
    """
    if entry is None or sl is None or sl == entry:
        return 0.0
    pip_unit_map = {
        "XAUUSD": 1.0, "NAS100": 1.0, "US30": 1.0,
        "BTCUSD": 1.0, "SOLUSD": 1.0,
        "EURUSD": 0.0001, "GBPJPY": 0.0001,
    }
    pip_unit = pip_unit_map.get(symbol, 1.0)
    sl_distance_pips = abs(float(entry) - float(sl)) / pip_unit
    if sl_distance_pips == 0:
        return 0.0
    return (float(net_pips) / sl_distance_pips) * RISK_USD


def engine_from_signal_id(signal_id):
    """Map signal_id prefix to engine name."""
    if not signal_id:
        return "unknown"
    s = str(signal_id).upper()
    if s.startswith("VS_") or s.startswith("VST_"):
        return "VOLTALITY" if s.startswith("VS_") else "NITRO"
    if s.startswith("CIPH_") or s.startswith("BTC"):
        return "CIPHER"
    if s.startswith("GBPJPY"):
        return "DRAGON"
    if s.startswith("EURUSD") or s.startswith("EUR"):
        return "TITAN"
    if s.startswith("SOL"):
        return "PHANTOM"
    if s.startswith("NAS") or s.startswith("NDX"):
        return "NEXUS"
    if s.startswith("US30"):
        return "FLUENCE"  # canonical signals are FLUENCE; SURGE US30 doesn't write outcomes
    if s.startswith("XAU") or s.startswith("GOLD"):
        return "NITRO"
    return "unknown"


def fetch_engine_stats(cur, name, sig_table, act_table, symbol):
    """Return dict of stats for one engine. Pulls closed trades by signal_id prefix
    so that outcomes living in trade_outcomes get attributed correctly even when
    the engine's signal table is sparse (canonical `signals` is shared)."""
    stats = {
        "name": name,
        "symbol": symbol,
        "total_signals": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "net_pips": 0.0,
        "win_rate": 0.0,
        "avg_pips": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "net_dollars": 0.0,
        "last_signal_ts": None,
        "last_signal_dir": None,
        "last_signal_entry": None,
        "open_list": [],
    }

    # ── Signals: from engine's own table if present, else from canonical `signals`
    # SURGE / DRAGON / TITAN share canonical `signals` table — use strategy/active trades
    # to differentiate. Total signals = number of signal_ids in our prefix map.
    if name in ("SURGE", "DRAGON", "TITAN"):
        prefix_map_st = {
            "SURGE":   [],  # SURGE doesn't write outcomes, falls back to canonical count
            "DRAGON":  ["GBPJPY"],
            "TITAN":   ["EURUSD", "EUR_"],
        }
        prefs = prefix_map_st.get(name, [])
        if prefs:
            where = " OR ".join([f"id LIKE '{p}%'" for p in prefs])
            cur.execute(f'SELECT COUNT(*) FROM signals WHERE {where}')
            stats["total_signals"] = cur.fetchone()[0] or 0
            cur.execute(f'SELECT MAX(timestamp) FROM signals WHERE {where}')
            stats["last_signal_ts"] = cur.fetchone()[0]
        else:
            # SURGE — count canonical but tag differently
            cur.execute('SELECT COUNT(*) FROM signals')
            stats["total_signals"] = cur.fetchone()[0] or 0
            cur.execute('SELECT MAX(timestamp) FROM signals')
            stats["last_signal_ts"] = cur.fetchone()[0]
    else:
        sig_count_table = sig_table or "signals"
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{sig_count_table}"')
            stats["total_signals"] = cur.fetchone()[0] or 0
            cur.execute(f'SELECT MAX(timestamp) FROM "{sig_count_table}"')
            stats["last_signal_ts"] = cur.fetchone()[0]
        except Exception:
            pass

    # ── Active trades ─────────────────────────────────────────────
    if act_table:
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{act_table}"')
            stats["open_trades"] = cur.fetchone()[0] or 0
            cur.execute(f'''
                SELECT trade_id, direction, entry, sl, opened_at
                FROM "{act_table}"
                ORDER BY opened_at DESC
                LIMIT 5
            ''')
            for r in cur.fetchall():
                stats["open_list"].append({
                    "trade_id": r[0],
                    "direction": r[1],
                    "entry": r[2],
                    "sl": r[3],
                    "opened_at": r[4],
                })
        except Exception:
            pass

    # ── Closed trades: pull ALL outcomes and route by signal_id prefix ──
    # Build the engine's signal_id prefix list. NOTE: VST_ (NITRO, length 4) and
    # VS_ (VOLTALITY, length 3) are DISTINCT prefixes — don't mix them up.
    prefix_map = {
        "NITRO":     ["VST_"],
        "SURGE":     [],   # SURGE doesn't write to trade_outcomes (canonical conflict)
        "DRAGON":    ["GBPJPY"],
        "TITAN":     ["EURUSD"],
        "CIPHER":    ["CIPH_", "BTCUSD_"],
        "PHANTOM":   ["PHNTM_", "SOLUSD_"],
        "NEXUS":     ["NAS", "NDX", "NEXUS"],
        "VOLTALITY": ["VS_"],
        "FLUENCE":   ["US30"],
    }
    prefixes = prefix_map.get(name, [])

    # Note: per-engine stats filter out pre-fix trades (before 2026-06-12) since
    # those had a known same-candle bug. The full feed still includes them so
    # the tradelog shows complete history — but per-engine stats stay clean.
    cutoff = '2026-06-12'

    try:
        if prefixes:
            where_parts = " OR ".join([f"o.signal_id LIKE '{p}%'" for p in prefixes])
            cur.execute(f'''
                SELECT o.signal_id, o.outcome, o.net_pips, o.exit_price,
                       o.closed_at, o.tp_hit, o.sl_hit
                FROM trade_outcomes o
                WHERE ({where_parts})
                  AND o.closed_at > '{cutoff}'
                ORDER BY o.closed_at DESC
            ''')
        else:
            cur.execute('SELECT 1 WHERE 0')  # no rows
        rows = cur.fetchall()

        # Now resolve entry/SL from whichever signal table has the row
        def lookup_entry_sl(signal_id):
            for t in [sig_table, "signals", "gold_signals", "btc_signals",
                      "sol_signals", "nexus_signals", "vs_signals",
                      "us30_signals"]:
                if not t:
                    continue
                try:
                    cur.execute(f'SELECT entry, sl, direction FROM "{t}" WHERE id = ? LIMIT 1', (signal_id,))
                    row = cur.fetchone()
                    if row:
                        return row
                except Exception:
                    continue
            # active trades fallback
            for t in ["gold_active_trades", "btc_active_trades", "sol_active_trades",
                      "us30_active_trades", "forex_dragon_active", "forex_titan_active"]:
                try:
                    cur.execute(f'SELECT entry, sl, direction FROM "{t}" WHERE trade_id = ? LIMIT 1', (signal_id,))
                    row = cur.fetchone()
                    if row:
                        return row
                except Exception:
                    continue
            return (None, None, None)

        stats["closed_trades"] = len(rows)
        if rows:
            pips = [float(r[2] or 0) for r in rows]
            stats["net_pips"] = sum(pips)
            stats["avg_pips"] = sum(pips) / len(pips)
            stats["best_trade"] = max(pips)
            stats["worst_trade"] = min(pips)
            stats["wins"] = sum(1 for p in pips if p > 0)
            stats["losses"] = sum(1 for p in pips if p <= 0)
            stats["win_rate"] = (stats["wins"] / len(pips)) * 100

            total_dollars = 0.0
            for r in rows:
                sig_id, outcome, net_pips, exit_px, closed_at, tp_hit, sl_hit = r
                entry, sl, direction = lookup_entry_sl(sig_id)
                total_dollars += compute_dollar_pnl(net_pips, entry, sl, symbol)
            stats["net_dollars"] = total_dollars

            last = rows[0]
            stats["last_close"] = {
                "signal_id": last[0],
                "outcome": last[1],
                "pips": last[2],
                "closed_at": last[4],
                "tp_hit": last[5],
                "sl_hit": last[6],
            }
    except Exception as e:
        stats["error"] = str(e)

    return stats


def build_daily_pnl(cur, days=14):
    """Per-engine per-day P&L from trade_outcomes."""
    cur.execute(f'''
        SELECT date(o.closed_at) AS d, s.strategy, SUM(o.net_pips)
        FROM trade_outcomes o
        LEFT JOIN signals s ON s.id = o.signal_id
        WHERE o.closed_at > date('now', '-{days} days')
        GROUP BY d, s.strategy
        ORDER BY d
    ''')
    rows = cur.fetchall()
    # Strategy in signals is 'unknown' — fall back to inferring from signal_id prefix
    daily = {}
    for d, strategy, pips in rows:
        if d not in daily:
            daily[d] = {}
        daily[d][strategy or "unknown"] = pips
    return daily


def build_feed(cur, limit=1000):
    """Complete trade ledger — all closed + all open trades across engines.

    Three categories combined:
      1. Closed trades from trade_outcomes (98 rows, May → Jun)
      2. Currently OPEN trades from each engine's *_active_trades table
      3. Pending signals (signal sent to Telegram, no outcome yet)

    Result is sorted newest-first by timestamp. No artificial cutoff.
    Per-engine prefix attribution is done client-side (ENGINE_PREFIX map).

    Args:
        limit: safety cap (1000) so a DB explosion can't OOM the JSON.
               The real ledger is ~150 rows today.
    """
    feed = []

    # 1. Closed trades — full history
    try:
        cur.execute('''
            SELECT o.signal_id, o.outcome, o.net_pips, o.closed_at,
                   o.tp_hit, o.sl_hit, o.duration_minutes, o.exit_price
            FROM trade_outcomes o
            ORDER BY o.closed_at DESC
        ''')
        for r in cur.fetchall():
            feed.append({
                "signal_id":   r[0],
                "status":      "closed",
                "outcome":     r[1],
                "net_pips":    r[2] or 0,
                "ts":          r[3],
                "closed_at":   r[3],   # alias for dashboard pages
                "tp_hit":      r[4],
                "sl_hit":      r[5],
                "duration_min": r[6],
                "exit_price":  r[7],
                "sort_key":    r[3] or "",
            })
    except Exception:
        pass

    # 2. Currently open trades (every engine's *_active_trades table OR signal table)
    # NOTE: NITRO stores open trades in `signals` table with is_active=1
    # (gold_active_trades is empty — NITRO doesn't use it for active tracking).
    # SURGE also writes to signals table with is_active=1.
    # NEXUS uses nexus_signals; VOLTALITY uses vs_signals (status varies).
    # Each engine uses a slightly different schema — we adapt per engine.
    open_sources = [
        # (engine, table, symbol, status_filter, sl_col, score_col, id_col, ts_col)
        ("NITRO",     "gold_active_trades",   "XAUUSD",  None,  "sl",  None,        "trade_id", "opened_at"),
        ("FLUENCE",   "us30_active_trades",   "US30",    None,  "sl",  "score",     "trade_id", "opened_at"),
        ("CIPHER",    "btc_active_trades",    "BTCUSD",  None,  "sl",  "score",     "trade_id", "opened_at"),
        ("PHANTOM",   "sol_active_trades",    "SOLUSD",  None,  "sl",  "score",     "trade_id", "opened_at"),
        ("DRAGON",    "forex_dragon_active",  "GBPJPY",  None,  "sl",  "score",     "trade_id", "opened_at"),
        ("TITAN",     "forex_titan_active",   "EURUSD",  None,  "sl",  "score",     "trade_id", "opened_at"),
        # Signal-table-based engines
        # NITRO + SURGE both write to signals; use is_active=1 for NITRO, sent_to_telegram=0 for SURGE.
        # NEXUS uses nexus_signals.status (string, not enum).
        # VOLTALITY uses vs_signals.status.
    ]
    for eng_name, table, sym, status_filter, sl_col, score_col, id_col, ts_col in open_sources:
        try:
            score_select = f", {score_col} AS score" if score_col else ", NULL AS score"
            cur.execute(f'''
                SELECT {id_col} AS sig_id, direction, entry, {sl_col} AS sl, {ts_col} AS ts{score_select}
                FROM "{table}"
                ORDER BY {ts_col} DESC
            ''')
            for r in cur.fetchall():
                sig_id, direction, entry, sl, ts, score = r
                feed.append({
                    "signal_id":   sig_id,
                    "status":      "open",
                    "engine_hint": eng_name,
                    "symbol_hint": sym,
                    "outcome":     None,
                    "net_pips":    None,
                    "ts":          ts,
                    "closed_at":   ts,
                    "tp_hit":      0,
                    "sl_hit":      0,
                    "duration_min": None,
                    "direction":   direction,
                    "entry":       entry,
                    "sl":          sl,
                    "score":       score,
                    "sort_key":    ts or "",
                })
        except Exception:
            continue

    # NITRO + SURGE share the `signals` table. NITRO uses id prefix VST_, SURGE different.
    try:
        cur.execute('''
            SELECT id, direction, entry, stop_loss, created_at, confluence_score, id
            FROM signals
            WHERE is_active = 1
              AND id LIKE 'VST_%'
            ORDER BY created_at DESC
        ''')
        for r in cur.fetchall():
            sig_id, direction, entry, sl, ts, score, _id = r
            feed.append({
                "signal_id":   sig_id,
                "status":      "open",
                "engine_hint": "NITRO",
                "symbol_hint": "XAUUSD",
                "outcome":     None,
                "net_pips":    None,
                "ts":          ts,
                "closed_at":   ts,
                "tp_hit":      0,
                "sl_hit":      0,
                "duration_min": None,
                "direction":   direction,
                "entry":       entry,
                "sl":          sl,
                "score":       score,
                "sort_key":    ts or "",
            })
    except Exception:
        pass

    # NEXUS: any signal not in a terminal status
    try:
        cur.execute('''
            SELECT id, direction, entry, sl, created_at, confluence_score, status
            FROM nexus_signals
            WHERE status NOT IN ('SL HIT','TP1 HIT','TP2 HIT','TP3 HIT','TP1+TP2 BE HIT',
                                 'PRICE REVERSED TO TP1','PRICE REVERSED TO BE',
                                 'closed','closed_stale','VOIDED')
            ORDER BY created_at DESC
        ''')
        for r in cur.fetchall():
            sig_id, direction, entry, sl, ts, score, _status = r
            feed.append({
                "signal_id":   sig_id,
                "status":      "open",
                "engine_hint": "NEXUS",
                "symbol_hint": "NAS",
                "outcome":     None,
                "net_pips":    None,
                "ts":          ts,
                "closed_at":   ts,
                "tp_hit":      0,
                "sl_hit":      0,
                "duration_min": None,
                "direction":   direction,
                "entry":       entry,
                "sl":          sl,
                "score":       score,
                "sort_key":    ts or "",
            })
    except Exception:
        pass

    # VOLTALITY: vs_signals in non-terminal status
    try:
        cur.execute('''
            SELECT id, direction, entry, sl, created_at, confluence_score, status
            FROM vs_signals
            WHERE status NOT IN ('SL HIT','TP1 HIT','TP2 HIT','TP3 HIT','TP1+TP2 BE HIT',
                                 'PRICE REVERSED TO TP1','PRICE REVERSED TO BE',
                                 'closed','closed_stale','VOIDED')
            ORDER BY created_at DESC
        ''')
        for r in cur.fetchall():
            sig_id, direction, entry, sl, ts, score, _status = r
            feed.append({
                "signal_id":   sig_id,
                "status":      "open",
                "engine_hint": "VOLTALITY",
                "symbol_hint": "US30",
                "outcome":     None,
                "net_pips":    None,
                "ts":          ts,
                "closed_at":   ts,
                "tp_hit":      0,
                "sl_hit":      0,
                "duration_min": None,
                "direction":   direction,
                "entry":       entry,
                "sl":          sl,
                "score":       score,
                "sort_key":    ts or "",
            })
    except Exception:
        pass

    # Sort newest-first by sort_key (string-comparable ISO timestamps)
    feed.sort(key=lambda x: x.get("sort_key") or "", reverse=True)

    # Apply safety cap (keep newest)
    if len(feed) > limit:
        feed = feed[:limit]

    # Strip sort_key from output (internal only)
    for f in feed:
        f.pop("sort_key", None)

    return feed


def build_open_trades(cur):
    """All currently open trades across engines."""
    open_list = []
    for name, sig_t, act_t, sym, label, color, _chat, _chan in ENGINES:
        if not act_t:
            continue
        try:
            cur.execute(f'''
                SELECT trade_id, direction, entry, sl, tp1, tp2, tp3, tp4,
                       tp1_hit, tp2_hit, tp3_hit, tp4_hit, opened_at, score, strategy
                FROM "{act_t}"
                ORDER BY opened_at DESC
            ''')
            for r in cur.fetchall():
                open_list.append({
                    "engine": name,
                    "trade_id": r[0],
                    "direction": r[1],
                    "entry": r[2],
                    "sl": r[3],
                    "tp1": r[4], "tp2": r[5], "tp3": r[6], "tp4": r[7],
                    "tp1_hit": r[8], "tp2_hit": r[9], "tp3_hit": r[10], "tp4_hit": r[11],
                    "opened_at": r[12],
                    "score": r[13],
                    "strategy": r[14],
                    "color": color,
                    "symbol": sym,
                })
        except Exception:
            continue
    # Sort by opened_at desc
    open_list.sort(key=lambda x: x["opened_at"] or "", reverse=True)
    return open_list


def build_telegram_health(cur):
    """Per-engine Telegram delivery health.
    Uses signal_id prefix → trade_updates.telegram_message_id, and signals.telegram_msg_id.
    """
    health = []
    # Map engine → signal_id prefix list (same as prefix_map in fetch_engine_stats)
    prefix_map = {
        "NITRO":     ["VST_"],
        "DRAGON":    ["GBPJPY"],
        "TITAN":     ["EURUSD"],
        "CIPHER":    ["CIPH_", "BTCUSD_"],
        "PHANTOM":   ["PHNTM_", "SOLUSD_"],
        "NEXUS":     ["NAS", "NDX", "NEXUS"],
        "VOLTALITY": ["VS_"],
        "FLUENCE":   ["US30"],
    }

    for name, sig_t, act_t, sym, label, color, chat_id, chan_name in ENGINES:
        prefixes = prefix_map.get(name, [])
        last_delivery_ts = None
        last_delivery_signal = None
        last_update_ts = None
        total_updates = 0
        success_count = 0

        # Last signal delivery (has telegram_msg_id)
        if sig_t:
            try:
                cur.execute(f'SELECT MAX(timestamp) FROM "{sig_t}" WHERE telegram_msg_id IS NOT NULL')
                row = cur.fetchone()
                if row and row[0]:
                    last_delivery_ts = row[0]
                    cur.execute(f'SELECT id FROM "{sig_t}" WHERE timestamp = ? AND telegram_msg_id IS NOT NULL ORDER BY telegram_msg_id DESC LIMIT 1', (last_delivery_ts,))
                    r = cur.fetchone()
                    if r:
                        last_delivery_signal = r[0]
            except Exception:
                pass

        # Trade updates for this engine's signals
        if prefixes:
            where_parts = " OR ".join([f"signal_id LIKE '{p}%'" for p in prefixes])
            try:
                cur.execute(f'SELECT MAX(timestamp), COUNT(*), SUM(CASE WHEN telegram_message_id IS NOT NULL THEN 1 ELSE 0 END) FROM trade_updates WHERE ({where_parts})')
                last_update_ts, total_updates, success_count = cur.fetchone()
                success_count = success_count or 0
                total_updates = total_updates or 0
            except Exception:
                pass

        success_rate = (success_count / total_updates * 100) if total_updates else None

        # Pick the more recent of the two
        last_activity = max([t for t in [last_delivery_ts, last_update_ts] if t] or [None])

        # Channel routing info
        health.append({
            "engine": name,
            "color": color,
            "chat_id": chat_id,
            "channel_name": chan_name,
            "last_signal_delivery": last_delivery_ts,
            "last_signal_id": last_delivery_signal,
            "last_trade_update": last_update_ts,
            "total_updates": int(total_updates),
            "successful_replies": int(success_count),
            "success_rate": round(success_rate, 1) if success_rate is not None else None,
            "last_activity": last_activity,
        })

    return health


def build_process_health():
    """Check if each engine's runner process is alive via ps grep."""
    import subprocess
    runner_map = {
        "NITRO":     "nitro_runner",
        "SURGE":     "surge_runner",
        "DRAGON":    "dragon_runner",
        "TITAN":     "titan_runner",
        "CIPHER":    "cipher_runner",
        "PHANTOM":   "phantom_runner",
        "NEXUS":     "nexus_runner",
        "VOLTALITY": "voltality_runner",
        "FLUENCE":   "fluence_runner",
    }
    health = {}
    for engine, runner in runner_map.items():
        try:
            r = subprocess.run(
                ["pgrep", "-f", runner],
                capture_output=True, text=True, timeout=5
            )
            pids = [p for p in r.stdout.strip().split('\n') if p.strip()]
            health[engine] = {
                "running": len(pids) > 0,
                "pids": pids,
                "runner": runner + ".py",
            }
        except Exception:
            health[engine] = {"running": False, "pids": [], "runner": runner + ".py"}
    return health


def main():
    if not DB.exists():
        print(f"DB not found: {DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = None
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()

    # Per-engine stats
    engines = []
    for name, sig_t, act_t, sym, label, color, _chat, _chan in ENGINES:
        s = fetch_engine_stats(cur, name, sig_t, act_t, sym)
        s["strategy_label"] = label
        s["color"] = color
        engines.append(s)

    # Totals
    total_signals = sum(e["total_signals"] for e in engines)
    total_open = sum(e["open_trades"] for e in engines)
    total_closed = sum(e["closed_trades"] for e in engines)
    total_wins = sum(e["wins"] for e in engines)
    total_losses = sum(e["losses"] for e in engines)
    total_pips = sum(e["net_pips"] for e in engines)
    total_dollars = sum(e["net_dollars"] for e in engines)
    overall_wr = (total_wins / total_closed * 100) if total_closed else 0

    # Daily P&L (last 14d) — grouped by engine via signal_id prefix or signal table
    daily = {}
    for name, sig_t, act_t, sym, label, color, _chat, _chan in ENGINES:
        if not sig_t:
            continue
        try:
            cur.execute(f'''
                SELECT date(o.closed_at) AS d, SUM(o.net_pips), COUNT(*)
                FROM trade_outcomes o
                WHERE o.signal_id IN (SELECT id FROM "{sig_t}")
                  AND o.closed_at > date('now', '-{DAYS_BACK} days')
                GROUP BY d
                ORDER BY d
            ''')
            for d, pips, n in cur.fetchall():
                if d not in daily:
                    daily[d] = {}
                daily[d][name] = {"pips": pips, "trades": n, "color": color}
        except Exception:
            continue

    # Feed of complete trade ledger (all closed + all open, no cutoff)
    feed = build_feed(cur, limit=1000)

    # Feed totals — for tradelog header display
    feed_closed = [f for f in feed if f.get("status") == "closed"]
    feed_open   = [f for f in feed if f.get("status") == "open"]
    feed_dates = [f["ts"] for f in feed if f.get("ts")]
    feed_first_ts = min(feed_dates) if feed_dates else None
    feed_last_ts  = max(feed_dates) if feed_dates else None

    # Open trades
    open_trades = build_open_trades(cur)

    # Telegram delivery health (per-engine)
    telegram_health = build_telegram_health(cur)

    # Process health (is each runner alive?)
    process_health = build_process_health()

    # Active filter (set by client)
    active_filter = None

    # Process / engine health (basic — read PIDs if running)
    health = []
    for name, sig_t, act_t, sym, label, color, _chat, _chan in ENGINES:
        health.append({
            "name": name,
            "symbol": sym,
            "color": color,
            "label": label,
            "last_signal_ts": engines[next(i for i, e in enumerate(engines) if e["name"] == name)]["last_signal_ts"],
            "total_signals": engines[next(i for i, e in enumerate(engines) if e["name"] == name)]["total_signals"],
            "open_trades": engines[next(i for i, e in enumerate(engines) if e["name"] == name)]["open_trades"],
        })

    payload = {
        "generated_at": now,
        "totals": {
            "engines": len(ENGINES),
            "total_signals": total_signals,
            "total_open": total_open,
            "total_closed": total_closed,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "win_rate": round(overall_wr, 1),
            "net_pips": round(total_pips, 1),
            "net_dollars": round(total_dollars, 2),
            "account_size": ACCOUNT_SIZE,
            "risk_per_trade": RISK_USD,
        },
        "engines": engines,
        "daily_pnl": daily,
        "feed": feed,
        "feed_totals": {
            "total":       len(feed),
            "closed":      len(feed_closed),
            "open":        len(feed_open),
            "first_ts":    feed_first_ts,
            "last_ts":     feed_last_ts,
        },
        "open_trades": open_trades,
        "health": health,
        "telegram_health": telegram_health,
        "process_health": process_health,
        "active_filter": active_filter,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"✅ Wrote {OUT}")
    print(f"   {len(ENGINES)} engines, {total_closed} closed trades, {total_pips:+.1f} pips total, ${total_dollars:+.2f}")
    print(f"   {total_open} open trades, {overall_wr:.1f}% WR")


if __name__ == "__main__":
    main()