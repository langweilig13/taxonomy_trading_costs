"""Перебор параметров adaptive buy/hold для Momentum.

Запуск:
    python3 adaptive_sweep.py          # построить все точки сетки
    python3 adaptive_sweep.py --report # пересобрать таблицу из готовых results

Результат: output/momentum_adaptive_buy_hold_comparison.csv
"""

from __future__ import annotations

import csv
from pathlib import Path

from backtest import run_backtest
from build import OUTPUT, connect, write_transactions
from strategies import resolve_strategy

# Сначала проверяем понятную диагональ lambda_b = lambda_h.
# После выбора лучшей зоны можно добавить более плотную двумерную сетку.
LAMBDA_PAIRS = [(0.0, 0.0), (2.5, 2.5), (5.0, 5.0), (10.0, 10.0), (20.0, 20.0)]
PAPER_START = "1973-07-01"
PAPER_END = "2012-12-31"


def mean(rows: list[dict[str, str]], column: str) -> float:
    return sum(float(row[column]) for row in rows) / len(rows)


def summarize(path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8") as file:
        rows = [
            row for row in csv.DictReader(file)
            if PAPER_START <= row["date"] <= PAPER_END
        ]
    gross = mean(rows, "gross_return")
    costs = mean(rows, "direct_cost")
    return {
        "gross_return_pct_month": 100 * gross,
        "turnover_pct_month": 100 * mean(rows, "turnover"),
        "direct_cost_pct_month": 100 * costs,
        # Это диагностическое сравнение до реализации matching для missing c.
        "gross_minus_priced_cost_pct_month": 100 * (gross - costs),
    }


def write_comparison(comparison: list[dict[str, float]]) -> None:
    benchmark = summarize(OUTPUT / "momentum_buy_hold_results.csv")
    for row in comparison:
        for metric in (
            "gross_return_pct_month",
            "turnover_pct_month",
            "direct_cost_pct_month",
            "gross_minus_priced_cost_pct_month",
        ):
            row[f"delta_vs_buy_hold_{metric}"] = row[metric] - benchmark[metric]

    path = OUTPUT / "momentum_adaptive_buy_hold_comparison.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    print(f"Сравнение сохранено: {path}")


def read_existing_comparison() -> list[dict[str, float]]:
    comparison = []
    for lambda_buy, lambda_hold in LAMBDA_PAIRS:
        strategy = resolve_strategy("momentum", "adaptive_buy_hold", lambda_buy, lambda_hold)
        comparison.append({
            "lambda_buy": lambda_buy,
            "lambda_hold": lambda_hold,
            **summarize(OUTPUT / f"{strategy['output_name']}_results.csv"),
        })
    return comparison


def main() -> None:
    import sys
    if sys.argv[1:] == ["--report"]:
        write_comparison(read_existing_comparison())
        return
    if sys.argv[1:]:
        raise SystemExit("Используйте python3 adaptive_sweep.py или python3 adaptive_sweep.py --report")

    comparison: list[dict[str, float]] = []
    for lambda_buy, lambda_hold in LAMBDA_PAIRS:
        with connect() as db:
            write_transactions(
                "momentum",
                db,
                "adaptive_buy_hold",
                lambda_buy=lambda_buy,
                lambda_hold=lambda_hold,
            )
        run_backtest(
            "momentum",
            "adaptive_buy_hold",
            lambda_buy=lambda_buy,
            lambda_hold=lambda_hold,
        )
        strategy = resolve_strategy("momentum", "adaptive_buy_hold", lambda_buy, lambda_hold)
        comparison.append({
            "lambda_buy": lambda_buy,
            "lambda_hold": lambda_hold,
            **summarize(OUTPUT / f"{strategy['output_name']}_results.csv"),
        })

    write_comparison(comparison)


if __name__ == "__main__":
    main()
