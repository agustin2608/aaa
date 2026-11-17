# MNQ Opening Range — candidate reconstruction

This repository contains market data and a reproducible *candidate* backtest.

## Important status

`backtest_candidate.py` is a reconstruction of the strategy rules discussed in chat. It is **not claimed to reproduce the earlier/original backtest exactly**. The prior reported fingerprint was 27 trades, 63% wins, 16 longs / 11 shorts, average stop 53.8 points, and +5.89R. A nearby reconstruction produced 27 trades and ~63% wins but did not match all those details. Do not treat the candidate output as proof of the original result.

## Rules implemented

- Instrument: MNQ continuous contract data supplied in this repository.
- Time zone: America/New_York, with daylight-saving conversion from UTC timestamps.
- Opening range: 09:30–10:00 New York time.
- Signal: first 5-minute candle closing outside the opening range, from 10:00 through 11:30.
- Direction filters at signal close: close above/below session VWAP and EMA 9 above/below EMA 21.
- EMA 9/21: calculated continuously across regular-session 5-minute bars only; overnight bars excluded.
- VWAP: reset each RTH day at 09:30, using 5-minute typical price weighted by volume.
- M15 confirmation: last completed 15-minute candle, built from the 5-minute RTH bars and anchored at 09:30, must close in the signal direction.
- If the first OR break does not pass filters, no later break is considered that day.
- Entry: open of first 1-minute candle at or after the signal candle close.
- Stop: low of the breakout candle for longs; high for shorts.
- Target: 1R (same distance as stop).
- Intrabar tie: if stop and target are both touched in one 1-minute candle, stop is assumed first.
- One trade maximum per day.
- Results are gross; commissions, exchange/broker fees and slippage are not included.

## Run

Requires Python 3 and pandas:

```bash
pip install pandas
python backtest_candidate.py
```

The script expects the CSV market-data files in the repository root and writes:

- `output/trade_ledger_candidate.csv`
- `output/summary_candidate.txt`

## Data coverage caveat

The available 1-minute data in this dataset ends before a completed regular session after 2026-11-11. Therefore, this repository snapshot cannot yet provide a genuine out-of-sample test after November 11. Add later data before making any out-of-sample claim.

## Validation still required

Before treating this as a tradable system, reconcile the original calculation's exact indicator session settings, M15 construction/alignment, entry fill convention, and transaction-cost assumptions. Then test untouched later dates and report net results, drawdown, expectancy, and sensitivity to slippage. A 27-trade sample is small and is not sufficient evidence of a durable edge.
