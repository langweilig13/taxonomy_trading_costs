# Taxonomy — Minimal Backtester

This project builds long/short portfolios, saves target weights and trade records, and then calculates historical gross and net returns.

## First run

From the project folder:

```bash
python3 run.py prepare
python3 run.py momentum --execution basic
python3 run.py momentum --execution buy_hold
python3 run.py momentum --execution staggered
python3 run.py momentum --execution low_cost
python3 adaptive_sweep.py
