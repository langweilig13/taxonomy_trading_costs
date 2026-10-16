"""Перебор исходных buy/hold границ при фиксированном lambda = 20.

Запуск: python3 adaptive_boundary_sweep.py
"""

from __future__ import annotations

import csv

from adaptive_sweep import PAPER_END, PAPER_START, summarize
from backtest import run_backtest
from build import OUTPUT, connect, write_transactions
from strategies import resolve_strategy

LAMBDA = 20.0
ENTRY_TAILS = (0.05, 0.075, 0.10)
HOLD_TAILS = (0.20, 0.25, 0.30)


def main() -> None:
    benchmark = summarize(OUTPUT / "momentum_buy_hold_results.csv")
    comparison: list[dict[str, float]] = []
    for entry_tail in ENTRY_TAILS:
        for hold_tail in HOLD_TAILS:
            with connect() as db:
                write_transactions(
                    "momentum", db, "adaptive_buy_hold", LAMBDA, LAMBDA, entry_tail, hold_tail
                )
            run_backtest(
                "momentum", "adaptive_buy_hold", LAMBDA, LAMBDA, entry_tail, hold_tail
            )
            strategy = resolve_strategy(
                "momentum", "adaptive_buy_hold", LAMBDA, LAMBDA, entry_tail, hold_tail
            )
            summary = summarize(OUTPUT / f"{strategy['output_name']}_results.csv")
            comparison.append({
                "lambda_buy": LAMBDA,
                "lambda_hold": LAMBDA,
                "entry_tail_pct": 100 * entry_tail,
                "hold_tail_pct": 100 * hold_tail,
                **summary,
                "delta_vs_buy_hold_gross_pct_month": (
                    summary["gross_return_pct_month"] - benchmark["gross_return_pct_month"]
                ),
                "delta_vs_buy_hold_turnover_pct_month": (
                    summary["turnover_pct_month"] - benchmark["turnover_pct_month"]
                ),
                "delta_vs_buy_hold_cost_pct_month": (
                    summary["direct_cost_pct_month"] - benchmark["direct_cost_pct_month"]
                ),
                "delta_vs_buy_hold_gross_minus_cost_pct_month": (
                    summary["gross_minus_priced_cost_pct_month"]
                    - benchmark["gross_minus_priced_cost_pct_month"]
                ),
            })

    path = OUTPUT / "momentum_adaptive_boundaries_lambda20.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    print(f"Сравнение сохранено: {path}")


if __name__ == "__main__":
    main()
