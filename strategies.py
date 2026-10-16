"""Что предсказываем отдельно от того, как исполняем сделки."""

BASE_STRATEGIES = {
    "high_frequency_combo": {
        "title": "High-Frequency Combo",
        "signal": "high_frequency_combo",
        "rebalance": "monthly",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
        "turnover_group": "high",
    },
    "valprof": {
        "title": "Value + Gross Profitability",
        "signal": "valprof",
        "rebalance": "annual",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
        "turnover_group": "low",
    },
    "investment": {
        "title": "Investment",
        "signal": "investment",
        "rebalance": "annual",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
        "turnover_group": "low",
    },
    "valmom": {
        "title": "Value + Momentum",
        "signal": "valmom",
        "rebalance": "monthly",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
        "turnover_group": "mid",
    },
    "momentum": {
        "title": "Momentum 12-2",
        "signal": "momentum_12_2",
        "rebalance": "monthly",
        "breakpoints": "nyse",
        "long_quantile": 0.90,
        "short_quantile": 0.10,
        "weighting": "value_weighted",
        "start": "1963-07-01",
        "turnover_group": "mid",
    },
}


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
    "adaptive_buy_hold": {
        "title": "Adaptive buy/hold spread",
        "method": "adaptive_buy_hold",
        # c в исходном наборе годовой, поэтому используем доступную в t
        # оценку за прошлый календарный год как c_(i,t-1).
        "cost_lag_years": 1,
        "lambda_buy": 0.0,
        "lambda_hold": 0.0,
        "entry_tail_base": 0.10,
        "hold_tail_base": 0.20,
        "entry_tail_min": 0.01,
        "entry_tail_max": 0.10,
        "hold_tail_min": 0.20,
        "hold_tail_max": 0.50,
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


def resolve_strategy(
    name: str,
    execution: str = "basic",
    lambda_buy: float | None = None,
    lambda_hold: float | None = None,
    entry_tail_base: float | None = None,
    hold_tail_base: float | None = None,
) -> dict:
    """Собрать настройки одной комбинации, например Momentum + buy/hold."""
    try:
        base = BASE_STRATEGIES[name]
    except KeyError as error:
        raise ValueError(f"Неизвестная стратегия '{name}'. Доступно: {', '.join(BASE_STRATEGIES)}") from error
    try:
        policy = EXECUTION_POLICIES[execution]
    except KeyError as error:
        raise ValueError(f"Неизвестная execution policy '{execution}'. Доступно: {', '.join(EXECUTION_POLICIES)}") from error

    settings = {
        **base,
        **policy,
        "strategy_name": name,
        "execution_name": execution,
    }
    if lambda_buy is not None:
        settings["lambda_buy"] = lambda_buy
    if lambda_hold is not None:
        settings["lambda_hold"] = lambda_hold
    if entry_tail_base is not None:
        settings["entry_tail_base"] = entry_tail_base
    if hold_tail_base is not None:
        settings["hold_tail_base"] = hold_tail_base

    output_name = f"{name}_{execution}"
    if settings["method"] == "adaptive_buy_hold":
        output_name += f"_lb{_slug(settings['lambda_buy'])}_lh{_slug(settings['lambda_hold'])}"
        if entry_tail_base is not None or hold_tail_base is not None:
            output_name += f"_b{_slug(settings['entry_tail_base'])}_h{_slug(settings['hold_tail_base'])}"
    settings["output_name"] = output_name
    return settings


def _slug(value: float) -> str:
    """Безопасная часть имени файла: 2.5 превращается в 2_5."""
    return f"{value:g}".replace("-", "m").replace(".", "_")
