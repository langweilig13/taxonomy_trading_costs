"""Что предсказываем отдельно от того, как исполняем сделки."""

BASE_STRATEGIES = {
    "momentum": {
        "title": "Momentum 12-2",
        "signal": "momentum_12_2",
        "rebalance": "monthly",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
    },
}

# Это не alpha-стратегии. Это политики, которые превращают свежий target
# portfolio в реально торгуемый portfolio.
EXECUTION_POLICIES = {
    "basic": {
        "title": "Full monthly rebalancing",
        "method": "full_rebalance",
    },
    "buy_hold": {
        "title": "Buy/hold 10%/20%",
        "method": "buy_hold",
        "entry_long": 0.90,
        "hold_long": 0.80,
        "entry_short": 0.10,
        "hold_short": 0.20,
    },
    "staggered": {
        "title": "Staggered quarterly rebalancing",
        "method": "staggered",
        "sleeves": 3,
    },
    "low_cost": {
        "title": "Low-cost universe",
        "method": "low_cost_universe",
        "size_deciles": 10,
        "keep_cost_quantile": 1 / 3,
        "cost_lag_years": 1,
    },
}


def resolve_strategy(name: str, execution: str = "basic") -> dict:
    """Собрать настройки одной комбинации, например Momentum + buy/hold."""
    try:
        base = BASE_STRATEGIES[name]
    except KeyError as error:
        raise ValueError(f"Неизвестная стратегия '{name}'. Доступно: {', '.join(BASE_STRATEGIES)}") from error
    try:
        policy = EXECUTION_POLICIES[execution]
    except KeyError as error:
        raise ValueError(f"Неизвестная execution policy '{execution}'. Доступно: {', '.join(EXECUTION_POLICIES)}") from error

    return {
        **base,
        **policy,
        "strategy_name": name,
        "execution_name": execution,
        "output_name": f"{name}_{execution}",
    }
