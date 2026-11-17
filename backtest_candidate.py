#!/usr/bin/env python3
"""Candidate reconstruction of the MNQ Opening Range strategy.

Reads the MNQ 5m and 1m CSVs in the repository folder and writes:
  output/trade_ledger_candidate.csv
  output/summary_candidate.txt

This is NOT asserted to reproduce the earlier/original backtest. See README.
Requires: pandas
"""
from pathlib import Path
import glob
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
TZ = "America/New_York"
POINT_VALUE = 2.0  # USD per MNQ point, one contract
START = "2026-09-01"
END = "2026-11-11"


def load_files(pattern):
    paths = sorted(glob.glob(str(ROOT / pattern)))
    if not paths:
        raise FileNotFoundError(f"No files match: {pattern}")
    frames = []
    for path in paths:
        df = pd.read_csv(path)
        required = {"ts", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{Path(path).name}: missing columns {sorted(missing)}")
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def add_indicators(m5):
    m5 = m5.copy()
    local_time = m5["ts"].dt.time
    rth = m5[(local_time >= pd.Timestamp("09:30").time()) &
             (local_time < pd.Timestamp("16:00").time())].copy()
    rth["date"] = rth["ts"].dt.date
    rth["typical"] = (rth["high"] + rth["low"] + rth["close"]) / 3.0
    rth["pv"] = rth["typical"] * rth["volume"]
    rth["cum_pv"] = rth.groupby("date")["pv"].cumsum()
    rth["cum_vol"] = rth.groupby("date")["volume"].cumsum()
    rth["vwap"] = rth["cum_pv"] / rth["cum_vol"].replace(0, pd.NA)

    # EMAs are continuous across RTH bars only; overnight bars are excluded.
    rth["ema9"] = rth["close"].ewm(span=9, adjust=False).mean()
    rth["ema21"] = rth["close"].ewm(span=21, adjust=False).mean()
    return rth


def make_m15(day):
    """Build 15m candles from RTH 5m bars, anchored at 09:30 New York."""
    x = day.copy()
    mins = x["ts"].dt.hour * 60 + x["ts"].dt.minute
    x["bucket"] = ((mins - (9 * 60 + 30)) // 15).astype(int)
    agg = x.groupby("bucket").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last")
    ).reset_index()
    agg["close_ts"] = [
        pd.Timestamp(day["ts"].iloc[0].date(), tz=TZ) +
        pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(minutes=15 * (b + 1))
        for b in agg["bucket"]
    ]
    return agg


def run():
    m5_all = load_files("cme.mnq.bars.5m.*.csv")
    m1_all = load_files("cme.mnq.bars.1m.*.csv")
    m5 = add_indicators(m5_all)
    m1_all["date"] = m1_all["ts"].dt.date
    m1_all = m1_all[(m1_all["ts"].dt.date >= pd.Timestamp(START).date()) &
                    (m1_all["ts"].dt.date <= pd.Timestamp(END).date())]

    trades = []
    for day_date, day in m5.groupby("date", sort=True):
        if not (pd.Timestamp(START).date() <= day_date <= pd.Timestamp(END).date()):
            continue
        day = day.sort_values("ts").copy()
        day_start = pd.Timestamp(str(day_date), tz=TZ) + pd.Timedelta(hours=9, minutes=30)
        or_end = day_start + pd.Timedelta(minutes=30)
        or_bars = day[(day["ts"] >= day_start) & (day["ts"] < or_end)]
        if len(or_bars) < 6:
            continue
        or_high, or_low = or_bars["high"].max(), or_bars["low"].min()

        # First M5 close outside the OR from 10:00 through 11:30 (bar close).
        window = day[(day["ts"] >= or_end) &
                     (day["ts"] + pd.Timedelta(minutes=5) <= day_start + pd.Timedelta(hours=2))]
        signal = None
        direction = None
        for _, bar in window.iterrows():
            if bar["close"] > or_high:
                signal, direction = bar, "LONG"
                break
            if bar["close"] < or_low:
                signal, direction = bar, "SHORT"
                break
        if signal is None:
            continue

        signal_close_ts = signal["ts"] + pd.Timedelta(minutes=5)
        # If the first break fails any filter, do not take a later break that day.
        aligned = (
            signal["close"] > signal["vwap"] and signal["ema9"] > signal["ema21"]
            if direction == "LONG" else
            signal["close"] < signal["vwap"] and signal["ema9"] < signal["ema21"]
        )
        m15 = make_m15(day)
        completed = m15[m15["close_ts"] <= signal_close_ts]
        if not aligned or completed.empty:
            continue
        confirm = completed.iloc[-1]
        m15_ok = (confirm["close"] > confirm["open"] if direction == "LONG"
                  else confirm["close"] < confirm["open"])
        if not m15_ok:
            continue

        # Fill at the first 1m bar open at/after the M5 signal close.
        mins = m1_all[m1_all["date"] == day_date].sort_values("ts")
        entry_candidates = mins[mins["ts"] >= signal_close_ts]
        if entry_candidates.empty:
            continue
        entry_bar = entry_candidates.iloc[0]
        entry_ts, entry = entry_bar["ts"], float(entry_bar["open"])
        stop = float(signal["low"] if direction == "LONG" else signal["high"])
        risk_pts = abs(entry - stop)
        if risk_pts <= 0:
            continue
        target = entry + risk_pts if direction == "LONG" else entry - risk_pts

        after = mins[mins["ts"] >= entry_ts]
        after = after[after["ts"].dt.time < pd.Timestamp("16:00").time()]
        exit_price, exit_ts, outcome = None, None, None
        for _, bar in after.iterrows():
            if direction == "LONG":
                stop_hit, target_hit = bar["low"] <= stop, bar["high"] >= target
            else:
                stop_hit, target_hit = bar["high"] >= stop, bar["low"] <= target
            # Conservative rule when both are touched inside one 1m bar.
            if stop_hit:
                exit_price, exit_ts, outcome = stop, bar["ts"], "LOSS"
                break
            if target_hit:
                exit_price, exit_ts, outcome = target, bar["ts"], "WIN"
                break

        if exit_price is None:
            if after.empty:
                continue
            last = after.iloc[-1]
            exit_price, exit_ts = float(last["close"]), last["ts"]
            outcome = "TIME_EXIT"

        pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        r_multiple = pnl_pts / risk_pts
        trades.append({
            "date": str(day_date), "direction": direction,
            "signal_bar_open_ny": signal["ts"].isoformat(),
            "signal_bar_close_ny": signal_close_ts.isoformat(),
            "entry_time_ny": entry_ts.isoformat(), "entry_price": entry,
            "stop_price": stop, "target_price": target, "risk_points": risk_pts,
            "exit_time_ny": exit_ts.isoformat(), "exit_price": exit_price,
            "outcome": outcome, "pnl_points": pnl_pts, "R": r_multiple,
            "pnl_usd_1_mnq_gross": pnl_pts * POINT_VALUE,
            "or_high": or_high, "or_low": or_low,
            "vwap_at_signal": float(signal["vwap"]),
            "ema9_at_signal": float(signal["ema9"]),
            "ema21_at_signal": float(signal["ema21"]),
            "m15_confirmation_close": float(confirm["close"]),
        })

    OUT.mkdir(exist_ok=True)
    ledger = pd.DataFrame(trades)
    ledger.to_csv(OUT / "trade_ledger_candidate.csv", index=False)
    if ledger.empty:
        summary = "No trades generated. Check data coverage and assumptions.\n"
    else:
        wins = int((ledger["R"] > 0).sum())
        losses = int((ledger["R"] < 0).sum())
        summary = (
            f"Candidate reconstruction | {START} to {END}\n"
            f"Trades: {len(ledger)}\nWins: {wins} | Losses: {losses}\n"
            f"Win rate: {wins / len(ledger):.2%}\n"
            f"Gross R: {ledger['R'].sum():.2f}\n"
            f"Longs: {(ledger['direction'] == 'LONG').sum()} | "
            f"Shorts: {(ledger['direction'] == 'SHORT').sum()}\n"
            f"Average stop: {ledger['risk_points'].mean():.2f} points\n"
            f"Median stop: {ledger['risk_points'].median():.2f} points\n"
            f"Max stop: {ledger['risk_points'].max():.2f} points\n"
            "Gross results exclude commissions, fees and slippage.\n"
        )
    (OUT / "summary_candidate.txt").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Ledger: {OUT / 'trade_ledger_candidate.csv'}")


if __name__ == "__main__":
    run()
