# VerstigeTech — Architecture

## System overview

VerstigeTech is a **read-only analytics layer** on top of the trading engine
fleet. It never writes to the trading database, never sends Telegram
messages, and never executes trades.

```
┌──────────────────────────────────────────────────────────────┐
│  Trading Engines (9, on Mac)                                │
│  ─────────────────────────────────────────────────────────   │
│  NITRO, SURGE, DRAGON, TITAN, CIPHER,                       │
│  PHANTOM, NEXUS, VOLTALITY, FLUENCE                         │
│                                                              │
│  Each writes to verstige_trades.db:                          │
│   - per-engine <symbol>_signals tables                       │
│   - per-engine <symbol>_active_trades tables                │
│   - shared trade_outcomes table                              │
│   - shared trade_updates table (TP/SL/ENTRY events)         │
└──────────────────────────────────────────────────────────────┘
                          │
                          │ SQLite (read)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  scripts/build_dashboard.py (runs every 5 min)              │
│  ─────────────────────────────────────────────────────────   │
│  - Aggregates per-engine stats via signal_id prefix routing  │
│  - Computes $ P&L: $1k acct · 1% risk per trade · 10$ risk  │
│  - Builds daily P&L timeseries per engine                   │
│  - Fetches Telegram health from trade_updates               │
│  - Checks process health via pgrep                          │
│  - Writes data/dashboard.json (~50 KB)                       │
└──────────────────────────────────────────────────────────────┘
                          │
                          │ cron pushes via git worktree
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  GitHub Pages (gh-pages branch)                             │
│  ─────────────────────────────────────────────────────────   │
│  index.html  strategies.html  dashboard.html                │
│  analytics.html  backtests.html  performance.html           │
│  risk.html  tradelog.html                                   │
│                                                              │
│  All served at https://verstige.github.io/VerstigeTech/     │
└──────────────────────────────────────────────────────────────┘
                          │
                          │ browser fetches data/dashboard.json
                          │ every 30-60s
                          ▼
                   [ User viewing the site ]
```

## Data flow detail

### Dashboard JSON schema

```typescript
{
  generated_at: string (ISO),
  totals: {
    engines: number,
    total_signals: number,
    total_open: number,
    total_closed: number,
    total_wins: number,
    total_losses: number,
    win_rate: number,        // %
    net_pips: number,
    net_dollars: number,     // $ at $1k acct
    account_size: number,
    risk_per_trade: number,
  },
  engines: Array<{
    name, symbol, strategy_label, color,
    total_signals, open_trades, closed_trades,
    wins, losses, net_pips, win_rate, avg_pips,
    best_trade, worst_trade, net_dollars,
    last_signal_ts, last_signal_dir, last_signal_entry,
    open_list: Array<{trade_id, direction, entry, sl, opened_at}>,
  }>,
  daily_pnl: { [date: string]: { [engine: string]: {pips, trades, color} } },
  feed: Array<{           // Last 50 closed trades
    signal_id, outcome, net_pips, closed_at,
    tp_hit, sl_hit, duration_min,
  }>,
  open_trades: Array<{    // All open trades
    engine, trade_id, direction, entry, sl,
    tp1, tp2, tp3, tp4,
    tp1_hit, tp2_hit, tp3_hit, tp4_hit,
    opened_at, score, strategy, color, symbol,
  }>,
  health: Array<{...}>,
  telegram_health: Array<{
    engine, color, chat_id, channel_name,
    last_signal_delivery, last_signal_id,
    last_trade_update, total_updates,
    successful_replies, success_rate, last_activity,
  }>,
  process_health: { [engine: string]: {running, pids, runner} },
  active_filter: null,
}
```

### Signal_id prefix routing

The shared `signals` table contains signals from SURGE/DRAGON/TITAN (all share
canonical). To attribute correctly:

| Engine | Prefix | Symbol |
|---|---|---|
| NITRO | `VST_`, `XAU`, `GOLD` | XAUUSD |
| DRAGON | `GBPJPY` | GBPJPY |
| TITAN | `EURUSD`, `EUR_` | EURUSD |
| CIPHER | `CIPH_`, `BTC` | BTCUSD |
| PHANTOM | `PHNTM`, `SOL` | SOLUSD |
| NEXUS | `NAS`, `NDX`, `NEXUS` | NAS100 |
| VOLTALITY | `VS_` | US30 |
| FLUENCE | `US30` | US30 |

SURGE is excluded (it doesn't write to `trade_outcomes`).

## Pip unit normalization

| Symbol | Pip unit | Used for |
|---|---|---|
| XAUUSD | 1.0 ($/oz) | Gold |
| US30 | 1.0 (points) | Dow futures |
| NAS100 | 1.0 (points) | Nasdaq futures |
| BTCUSD | 1.0 ($) | Bitcoin |
| SOLUSD | 1.0 ($) | Solana |
| EURUSD | 0.0001 (FX) | Euro/USD |
| GBPJPY | 0.0001 (FX) | Pound/Yen |

The `$ P&L` formula uses pip units to normalize:

```
$ P&L per trade = (net_pips / SL_distance_pips) × $10 risk
```

## Refresh cadence

- **Dashboard JSON**: every 5 min via cron (`scripts/refresh.sh`)
- **Browser**: 30-60s auto-refresh via `<meta http-equiv="refresh">` + JS `setInterval`
- **Backtest JSON**: static (regenerated manually when backtest runs)

## Future enhancements

1. **Real-time WebSocket** instead of 30s polling
2. **Auth layer** if exposing to non-internal users
3. **Equity curve annotations** (mark major wins/losses)
4. **Per-trade backtest replay** (click any trade → see what the engine was seeing at that moment)
5. **Strategy parameter sweeps** — find optimal parameters from backtest data
6. **MT5 integration verification** — ensure each engine is actually sending orders