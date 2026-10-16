# Taxonomy — минимальный бэктестер

Проект строит long/short портфель, сохраняет целевые веса и журнал сделок, а затем считает историческую gross/net доходность.

## Первый запуск

Из папки проекта:

```bash
python3 run.py prepare
python3 run.py momentum --execution basic
python3 run.py momentum --execution buy_hold
python3 run.py momentum --execution staggered
python3 run.py momentum --execution low_cost
python3 adaptive_sweep.py
```

`prepare` читается один раз. Он оставляет исходный архив в `Downloads` нетронутым и создаёт `data/cache/taxonomy.sqlite`.

После запуска `momentum --execution basic` появятся:

- `output/momentum_basic_targets.csv` — целевые веса после каждой ребалансировки;
- `output/momentum_basic_transactions.csv` — журнал `BUY`, `SELL`, `SHORT`, `COVER`;
- `output/momentum_basic_results.csv` — gross return, turnover, costs, net return и equity curve.

`buy_hold` и `staggered` — execution policies, а не новые alpha-стратегии.
Первая использует правило 10%/20%, вторая обновляет один из трёх слоёв капитала
в каждый месяц.

`low_cost` оставляет нижнюю треть lagged transaction costs в каждом NYSE size
decile до построения Momentum. Пока у акции нет доступного lagged `c`, она не
может попасть в этот universe; matching-процедура статьи для таких пропусков
ещё не реализована.

## Adaptive buy/hold spread

`adaptive_buy_hold` делает buy/hold границы индивидуальными для каждой акции.
Он использует доступный lagged Hasbrouck half-spread `c` и рассчитывает:

```text
b_i,t = clip(0.10 - lambda_b * c_i,t-1, 0.01, 0.10)
h_i,t = clip(0.20 + lambda_h * c_i,t-1, 0.20, 0.50)
```

Здесь `b` и `h` — доли хвоста рангов, где 0 — лучшая акция по signal.
Новая long-позиция открывается при `rank <= b`, а уже открытая закрывается
только при `rank > h`. Short-сторона симметрична. Для отсутствующего lagged
`c` применяется обычное правило 10%/20%, а не нулевая стоимость сделки.

```bash
python3 adaptive_sweep.py
```

Скрипт проверяет пять начальных пар `lambda_b = lambda_h` (0, 2.5, 5, 10, 20)
на периоде статьи, июль 1973 — декабрь 2012, и сохраняет сравнение в
`output/momentum_adaptive_buy_hold_comparison.csv`.

## Важная договорённость MVP

Портфель формируется в конце месяца и получает доходность следующего месяца. Momentum — доходность за месяцы `t-12 ... t-2`, то есть последний месяц намеренно пропущен.

Издержки считаются по `c` из `crspgibbs.csv`: это оценённый log half-spread каждой акции в конкретном году. Стоимость конкретной сделки равна `|изменение веса| × (exp(c)-1)`. Если для акции нет `c`, скрипт показывает неоценённый turnover и не маскирует пропуск нулевой стоимостью. Полное воспроизведение статьи дополнительно потребует их matching-процедуры по size и idiosyncratic volatility.

## Добавление новой стратегии

1. Добавить базовую стратегию в `BASE_STRATEGIES`.
2. При необходимости добавить execution policy в `EXECUTION_POLICIES`.
3. Не менять `backtest.py`: он одинаков для любой комбинации.

Следующие стратегии: Value, Gross Profitability, Investment и их комбинации. Они потребуют присоединить annual Compustat к CRSP через CCM link table.
