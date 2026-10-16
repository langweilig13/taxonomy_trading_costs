"""LightGBM-регрессор ожидаемой относительной месячной доходности.

Первый эксперимент: Momentum. Target для акции i в t равен её return в
t+1 минус equal-weighted return всей инвестиционной вселенной в t+1.

Запуск:
  python3 ml_regressor.py momentum
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np

from build import CACHE_DB

ROOT = Path(__file__).parent
MODEL_DIR = ROOT / "models"


def dataset_for_momentum() -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    """Собрать только information set, доступный на конец месяца t."""
    with sqlite3.connect(CACHE_DB) as db:
        rows = db.execute(
            """
            WITH calendar AS (
                SELECT date, LEAD(date) OVER (ORDER BY date) AS next_date
                FROM (SELECT DISTINCT date FROM monthly_stock)
            ), benchmark AS (
                SELECT date, AVG(ret) AS ew_return FROM monthly_stock GROUP BY date
            )
            SELECT s.date, s.signal, m.market_cap, m.ret, c.c_log,
                   n.ret - benchmark.ew_return AS target
            FROM momentum_signal s
            JOIN monthly_stock m ON m.date = s.date AND m.permno = s.permno
            JOIN calendar ON calendar.date = s.date
            JOIN monthly_stock n ON n.date = calendar.next_date AND n.permno = s.permno
            JOIN benchmark ON benchmark.date = calendar.next_date
            LEFT JOIN effective_cost c ON c.permno = s.permno
              AND c.year = CAST(substr(s.date, 1, 4) AS INTEGER) - 1
            WHERE s.date >= '2004-01-01' AND s.date <= '2011-12-31'
            ORDER BY s.date
            """
        )
        features, targets, dates = [], [], []
        for date, momentum, market_cap, current_return, lagged_c, target in rows:
            features.append([
                momentum,
                math.log(market_cap),
                current_return,
                lagged_c if lagged_c is not None else np.nan,
                int(date[5:7]),
            ])
            targets.append(target)
            dates.append(date)
    return np.asarray(features), np.asarray(targets), dates, np.asarray(["momentum", "log_market_cap", "current_return", "lagged_half_spread", "month"])


def train_momentum() -> None:
    started = time.perf_counter()
    x, y, dates, names = dataset_for_momentum()
    train = np.asarray([date <= "2008-12-31" for date in dates])
    validation = np.asarray(["2009-01-01" <= date <= "2011-12-31" for date in dates])
    model = lgb.LGBMRegressor(
        objective="regression", loss="squared_error", n_estimators=300,
        learning_rate=0.05, num_leaves=31, min_child_samples=200,
        subsample=0.8, colsample_bytree=0.9, random_state=7, n_jobs=-1,
        verbosity=-1,
    )
    fit_started = time.perf_counter()
    model.fit(x[train], y[train], eval_set=[(x[validation], y[validation])], callbacks=[lgb.early_stopping(30, verbose=False)])
    prediction = model.predict(x[validation])
    mse = float(np.mean((y[validation] - prediction) ** 2))
    correlation = float(np.corrcoef(y[validation], prediction)[0, 1])
    MODEL_DIR.mkdir(exist_ok=True)
    model.booster_.save_model(str(MODEL_DIR / "momentum_lgbm.txt"))
    report = {
        "target": "next_month_stock_return_minus_equal_weight_benchmark",
        "features": names.tolist(), "train_observations": int(train.sum()),
        "validation_observations": int(validation.sum()), "best_iteration": model.best_iteration_,
        "validation_mse": mse, "validation_correlation": correlation,
        "fit_seconds": round(time.perf_counter() - fit_started, 2),
        "total_seconds": round(time.perf_counter() - started, 2),
    }
    (MODEL_DIR / "momentum_lgbm_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.argv[1:] != ["momentum"]:
        raise SystemExit("Запуск: python3 ml_regressor.py momentum")
    train_momentum()
