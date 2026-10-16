"""Единая матрица execution methods для реализованных стратегий.

Запуск: python3 experiments.py
Выход: output/strategy_method_comparison.csv
"""

from __future__ import annotations

import csv
from collections import defaultdict

from backtest import run_backtest
from build import OUTPUT, connect, write_transactions
from strategies import BASE_STRATEGIES, resolve_strategy

STRATEGIES = ("valprof", "investment", "valmom", "momentum")
METHODS = (
    ("basic", {}),
    ("buy_hold", {}),
    ("adaptive_buy_hold", {"lambda_buy": 20.0, "lambda_hold": 20.0,
                             "entry_tail_base": 0.05, "hold_tail_base": 0.25}),
    ("staggered", {}),
    ("low_cost", {}),
)


def mean(rows: list[dict[str, str]], column: str) -> float:
    return sum(float(row[column]) for row in rows) / len(rows)


def holdings(path) -> tuple[float, float]:
    by_date: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with path.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            by_date[row["date"]][0 if float(row["target_weight"]) > 0 else 1] += 1
    return (
        sum(x[0] for x in by_date.values()) / len(by_date),
        sum(x[1] for x in by_date.values()) / len(by_date),
    )


def summarize(name: str, strategy_name: str, method: str, settings: dict) -> dict:
    with (OUTPUT / f"{name}_results.csv").open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    traded = sum(float(row["priced_turnover"]) + float(row["unpriced_turnover"]) for row in rows)
    priced = sum(float(row["priced_turnover"]) for row in rows)
    long_names, short_names = holdings(OUTPUT / f"{name}_targets.csv")
    gross = 100 * mean(rows, "gross_return")
    costs = 100 * mean(rows, "direct_cost")
    return {
        "strategy": strategy_name,
        "turnover_group": BASE_STRATEGIES[strategy_name]["turnover_group"],
        "method": method,
        "gross_pct_month": gross,
        "turnover_pct_month": 100 * mean(rows, "turnover"),
        "cost_pct_month": costs,
        "gross_minus_priced_cost_pct_month": gross - costs,
        "cost_coverage_pct": 100 * priced / traded if traded else 0.0,
        "avg_long_names": long_names,
        "avg_short_names": short_names,
        "lambda_buy": settings.get("lambda_buy", ""),
        "lambda_hold": settings.get("lambda_hold", ""),
        "entry_tail_pct": 100 * settings["entry_tail_base"] if "entry_tail_base" in settings else "",
        "hold_tail_pct": 100 * settings["hold_tail_base"] if "hold_tail_base" in settings else "",
    }


def main() -> None:
    output = []
    for strategy_name in STRATEGIES:
        for method, settings in METHODS:
            with connect() as db:
                write_transactions(strategy_name, db, method, **settings)
            run_backtest(strategy_name, method, **settings)
            strategy = resolve_strategy(strategy_name, method, **settings)
            output.append(summarize(strategy["output_name"], strategy_name, method, settings))
    path = OUTPUT / "strategy_method_comparison.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    print(f"Готово: {path}")


if __name__ == "__main__":
    main()
