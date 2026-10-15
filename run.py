"""Запуск проекта.

Примеры:
    python3 run.py prepare
    python3 run.py momentum --execution basic
    python3 run.py momentum --execution buy_hold
    python3 run.py build momentum --execution staggered
    python3 run.py backtest momentum --execution staggered
"""

from __future__ import annotations

import sys

from backtest import run_backtest
from build import connect, prepare_monthly_data, write_transactions
from strategies import BASE_STRATEGIES, EXECUTION_POLICIES


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help", "help"}:
        print(__doc__)
        print("Стратегии:", ", ".join(BASE_STRATEGIES))
        print("Execution policies:", ", ".join(EXECUTION_POLICIES))
        return
    if args == ["prepare"]:
        prepare_monthly_data()
        return

    action = "run"
    if args[0] in {"build", "backtest"}:
        action, args = args[0], args[1:]
    if not args:
        raise SystemExit("Укажите стратегию, например: python3 run.py momentum --execution buy_hold")

    strategy = args[0]
    execution = _execution_from(args[1:])
    if action in {"run", "build"}:
        with connect() as db:
            write_transactions(strategy, db, execution)
    if action in {"run", "backtest"}:
        run_backtest(strategy, execution)


def _execution_from(args: list[str]) -> str:
    if not args:
        return "basic"
    if len(args) == 2 and args[0] == "--execution":
        return args[1]
    raise SystemExit("Используйте флаг так: --execution basic | buy_hold | staggered | low_cost")


if __name__ == "__main__":
    main()
