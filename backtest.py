
from __future__ import annotations

import csv
import math
import sqlite3
from pathlib import Path

from build import CACHE_DB, OUTPUT
from strategies import resolve_strategy

def run_backtest(strategy_name: str, execution: str = "basic") -> None:
    strategy = resolve_strategy(strategy_name, execution)
    output_name = strategy["output_name"]
    targets_path = OUTPUT / f"{output_name}_targets.csv"
    if not targets_path.exists():
        raise FileNotFoundError(f"Сначала выполните: python run.py build {strategy_name} --execution {execution}")

    targets = _read_targets(targets_path)
    with sqlite3.connect(CACHE_DB) as db:
        dates = [row[0] for row in db.execute("SELECT DISTINCT date FROM monthly_stock ORDER BY date")]
        returns = _load_returns(db)
        half_spreads = _load_effective_costs(db)

    weights: dict[str, float] = {}
    result = []
    gross_equity = 1.0
    net_equity = 1.0
    complete_net_history = True

    for date in dates:
        # targets хранят физический портфель: +50% long и -50% short.
        # Table 3 статьи показывает spread return = long return - short return,
        # поэтому при выводе доходности и costs масштабируем этот портфель в 2 раза.
        physical_return = sum(weight * returns.get((date, permno), 0.0) for permno, weight in weights.items())
        long_turnover = short_turnover = physical_cost = 0.0
        priced_turnover = unpriced_turnover = 0.0


        if date in targets:
            new_weights = targets[date]
            for permno in set(weights) | set(new_weights):
                old_weight = weights.get(permno, 0.0)
                new_weight = new_weights.get(permno, 0.0)

                long_trade = abs(max(new_weight, 0.0) - max(old_weight, 0.0))
                short_trade = abs(max(-new_weight, 0.0) - max(-old_weight, 0.0))
                traded_weight = long_trade + short_trade
                long_turnover += long_trade
                short_turnover += short_trade
                c_log = half_spreads.get((permno, int(date[:4])))
                if c_log is None:
                    unpriced_turnover += traded_weight
                else:
                    # c — log half-spread из статьи: one-way cost = exp(c) - 1.
                    physical_cost += traded_weight * math.expm1(c_log)
                    priced_turnover += traded_weight
            weights = new_weights

        # TO в Table 3: среднее turnover long- и short-сторон.
        turnover = (long_turnover + short_turnover) / 2
        gross_return = 2 * physical_return
        direct_cost = 2 * physical_cost
        # Пропуски не считаем бесплатными: полноценный net return в таком месяце unavailable.
        net_return = gross_return - direct_cost if unpriced_turnover == 0 else None
        gross_equity *= 1.0 + gross_return
        if complete_net_history and net_return is not None:
            net_equity *= 1.0 + net_return
        else:
            complete_net_history = False
        result.append({
            "date": date,
            "gross_return": gross_return,
            "turnover": turnover,
            "long_turnover": long_turnover,
            "short_turnover": short_turnover,
            "priced_turnover": priced_turnover,
            "unpriced_turnover": unpriced_turnover,
            "direct_cost": direct_cost,
            "net_return": net_return,
            "gross_equity": gross_equity,
            "net_equity": net_equity if complete_net_history else None,
        })

    _write_results(output_name, result)
    _print_summary(output_name, result)


def _read_targets(path: Path) -> dict[str, dict[str, float]]:
    targets: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            targets.setdefault(row["date"], {})[row["permno"]] = float(row["target_weight"])
    return targets


def _load_returns(db: sqlite3.Connection) -> dict[tuple[str, str], float]:
    return {(date, permno): ret for date, permno, ret in db.execute("SELECT date, permno, ret FROM monthly_stock")}


def _load_effective_costs(db: sqlite3.Connection) -> dict[tuple[str, int], float]:
    return {(permno, year): c_log for permno, year, c_log in db.execute(
        "SELECT permno, year, c_log FROM effective_cost"
    )}


def _write_results(strategy_name: str, rows: list[dict]) -> None:
    path = OUTPUT / f"{strategy_name}_results.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(strategy_name: str, rows: list[dict]) -> None:
    unpriced = sum(row["unpriced_turnover"] for row in rows)
    total_traded = sum(row["priced_turnover"] + row["unpriced_turnover"] for row in rows)
    print(f"{strategy_name}: gross equity = ${rows[-1]['gross_equity']:.4f} на $1 начального капитала")
    if unpriced == 0:
        print(f"net equity = ${rows[-1]['net_equity']:.4f}")
    else:
        coverage = 1 - unpriced / total_traded if total_traded else 0
        print(f"Net equity не показан: effective spreads покрывают {coverage:.2%} turnover.")
        print("Нужно заполнить пропуски matching-процедурой статьи (size + idiosyncratic volatility).")
    print(f"Результат: output/{strategy_name}_results.csv")
