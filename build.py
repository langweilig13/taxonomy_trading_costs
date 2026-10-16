from __future__ import annotations

import csv
import gzip
import io
import sqlite3
import zipfile
from collections import defaultdict, deque
from pathlib import Path

from sources import (
    ANNUAL_FUNDAMENTALS_MEMBER,
    CCM_LINK_MEMBER,
    FUNDAMENTALS_ZIP,
    GIBBS_COST_MEMBER,
    MONTHLY_STOCK_MEMBER,
    MONTHLY_STOCK_ZIP,
)
from strategies import resolve_strategy
from ff49 import industry as ff49_industry, load_ranges as load_ff49_ranges

ROOT = Path(__file__).parent
CACHE_DB = ROOT / "data" / "cache" / "taxonomy.sqlite"
OUTPUT = ROOT / "output"


def number(value: str | None) -> float | None:
    """Безопасно преобразовать поле CRSP в число."""
    try:
        return float(value) if value not in (None, "", "NA") else None
    except ValueError:
        return None


def connect() -> sqlite3.Connection:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(CACHE_DB)


def prepare_monthly_data() -> None:
    """Сохранить нужные для первого MVP поля из CRSP Monthly Stock File.

    Оставляем активные обыкновенные акции США на NYSE/AMEX/Nasdaq. Исходный
    файл не копируется: в кэш попадает только нужный набор колонок.
    """
    if not MONTHLY_STOCK_ZIP.exists():
        raise FileNotFoundError(f"Не найден архив: {MONTHLY_STOCK_ZIP}")

    with connect() as db:
        db.executescript(
            """
            DROP TABLE IF EXISTS monthly_stock;
            CREATE TABLE monthly_stock (
                date TEXT NOT NULL,
                permno TEXT NOT NULL,
                ret REAL NOT NULL,
                market_cap REAL NOT NULL,
                exchange TEXT NOT NULL,
                sic INTEGER,
                PRIMARY KEY (date, permno)
            );
            """
        )

        inserted = 0
        with zipfile.ZipFile(MONTHLY_STOCK_ZIP) as archive:
            with archive.open(MONTHLY_STOCK_MEMBER) as compressed:
                with gzip.GzipFile(fileobj=compressed) as unzipped:
                    reader = csv.DictReader(io.TextIOWrapper(unzipped, encoding="utf-8"))
                    batch = []
                    for row in reader:
                        # Стандартный CRSP universe для простого первого варианта.
                        if row["TradingStatusFlg"] != "A":
                            continue
                        if row["SecurityType"] != "EQTY" or row["SecuritySubType"] != "COM":
                            continue
                        if row["ShareType"] != "NS" or row["USIncFlg"] != "Y":
                            continue
                        if row["PrimaryExch"] not in {"N", "A", "Q"}:
                            continue

                        ret = number(row["MthRet"])
                        market_cap = number(row["MthCap"])
                        if ret is None or market_cap is None or market_cap <= 0:
                            continue

                        batch.append((
                            row["MthCalDt"], row["PERMNO"], ret, market_cap,
                            row["PrimaryExch"], number(row["SICCD"]),
                        ))
                        if len(batch) == 10_000:
                            db.executemany("INSERT OR REPLACE INTO monthly_stock VALUES (?, ?, ?, ?, ?, ?)", batch)
                            inserted += len(batch)
                            batch.clear()

                    if batch:
                        db.executemany("INSERT OR REPLACE INTO monthly_stock VALUES (?, ?, ?, ?, ?, ?)", batch)
                        inserted += len(batch)

        db.execute("CREATE INDEX IF NOT EXISTS idx_monthly_permno_date ON monthly_stock (permno, date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_monthly_date ON monthly_stock (date)")
        _prepare_effective_costs(db)
        _prepare_accounting_data(db)
        db.commit()

    print(f"Готово: {inserted:,} строк сохранено в {CACHE_DB}")


def _prepare_effective_costs(db: sqlite3.Connection) -> None:
    """Загрузить c из Hasbrouck/Roll Gibbs file.

    c — log half-spread: стоимость одной покупки или продажи относительно
    efficient midpoint. В обычных процентах one-way cost = exp(c) - 1.
    """
    db.executescript(
        """
        DROP TABLE IF EXISTS effective_cost;
        CREATE TABLE effective_cost (
            permno TEXT NOT NULL,
            year INTEGER NOT NULL,
            c_log REAL NOT NULL,
            PRIMARY KEY (permno, year)
        );
        """
    )
    inserted = 0
    with zipfile.ZipFile(MONTHLY_STOCK_ZIP) as archive:
        with archive.open(GIBBS_COST_MEMBER) as raw_file:
            reader = csv.DictReader(io.TextIOWrapper(raw_file, encoding="utf-8"))
            batch = []
            for row in reader:
                c_log = number(row["c"])
                if c_log is None or c_log < 0:
                    continue
                batch.append((row["permno"], int(row["year"]), c_log))
                if len(batch) == 10_000:
                    db.executemany("INSERT OR REPLACE INTO effective_cost VALUES (?, ?, ?)", batch)
                    inserted += len(batch)
                    batch.clear()
            if batch:
                db.executemany("INSERT OR REPLACE INTO effective_cost VALUES (?, ?, ?)", batch)
                inserted += len(batch)
    db.execute("CREATE INDEX IF NOT EXISTS idx_cost_year ON effective_cost (year)")
    print(f"Effective costs: {inserted:,} PERMNO-year наблюдений загружено")


def _prepare_accounting_data(db: sqlite3.Connection) -> None:
    """Загрузить только поля Compustat и CCM, нужные пяти выбранным стратегиям."""
    db.executescript(
        """
        DROP TABLE IF EXISTS annual_fundamental;
        CREATE TABLE annual_fundamental (
            gvkey TEXT NOT NULL,
            datadate TEXT NOT NULL,
            sic INTEGER,
            at REAL, revt REAL, cogs REAL, ppegt REAL, invt REAL,
            seq REAL, ceq REAL, lt REAL, txditc REAL,
            pstkrv REAL, pstkl REAL, pstk REAL,
            PRIMARY KEY (gvkey, datadate)
        );
        DROP TABLE IF EXISTS ccm_link;
        CREATE TABLE ccm_link (
            gvkey TEXT NOT NULL,
            permno TEXT NOT NULL,
            link_start TEXT NOT NULL,
            link_end TEXT NOT NULL,
            PRIMARY KEY (gvkey, permno, link_start)
        );
        """
    )
    with zipfile.ZipFile(FUNDAMENTALS_ZIP) as archive:
        with archive.open(ANNUAL_FUNDAMENTALS_MEMBER) as compressed:
            with gzip.GzipFile(fileobj=compressed) as unzipped:
                reader = csv.DictReader(io.TextIOWrapper(unzipped, encoding="utf-8"))
                batch = []
                for row in reader:
                    if not (row["datafmt"] == "STD" and row["indfmt"] == "INDL" and row["consol"] == "C"):
                        continue
                    if row["costat"] not in {"A", "I"} or not row["datadate"]:
                        continue
                    batch.append((
                        row["gvkey"], row["datadate"], number(row["sic"]), number(row["at"]),
                        number(row["revt"]), number(row["cogs"]), number(row["ppegt"]), number(row["invt"]),
                        number(row["seq"]), number(row["ceq"]), number(row["lt"]), number(row["txditc"]),
                        number(row["pstkrv"]), number(row["pstkl"]), number(row["pstk"]),
                    ))
                    if len(batch) >= 10_000:
                        db.executemany("INSERT OR REPLACE INTO annual_fundamental VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                        batch.clear()
                if batch:
                    db.executemany("INSERT OR REPLACE INTO annual_fundamental VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
        with archive.open(CCM_LINK_MEMBER) as compressed:
            with gzip.GzipFile(fileobj=compressed) as raw_file:
                reader = csv.DictReader(io.TextIOWrapper(raw_file, encoding="utf-8"))
                batch = []
                for row in reader:
                    if row["LINKTYPE"] not in {"LC", "LU", "LS"} or not row["LPERMNO"]:
                        continue
                    batch.append((row["gvkey"], row["LPERMNO"], row["LINKDT"], row["LINKENDDT"] or "9999-12-31"))
                    if len(batch) >= 10_000:
                        db.executemany("INSERT OR REPLACE INTO ccm_link VALUES (?, ?, ?, ?)", batch)
                        batch.clear()
                if batch:
                    db.executemany("INSERT OR REPLACE INTO ccm_link VALUES (?, ?, ?, ?)", batch)
    db.execute("CREATE INDEX idx_annual_gvkey_date ON annual_fundamental (gvkey, datadate)")
    db.execute("CREATE INDEX idx_ccm_permno_dates ON ccm_link (permno, link_start, link_end)")


def calculate_momentum_signals(db: sqlite3.Connection) -> None:
    """Посчитать доходность месяцев t-12 ... t-2 для каждого PERMNO.

    """
    db.executescript(
        """
        DROP TABLE IF EXISTS momentum_signal;
        CREATE TABLE momentum_signal (
            date TEXT NOT NULL,
            permno TEXT NOT NULL,
            signal REAL NOT NULL,
            PRIMARY KEY (date, permno)
        );
        """
    )

    cursor = db.execute("SELECT permno, date, ret FROM monthly_stock ORDER BY permno, date")
    current_permno = None
    returns: deque[float] = deque(maxlen=12)
    batch = []

    for permno, date, ret in cursor:
        if permno != current_permno:
            current_permno = permno
            returns.clear()



        if len(returns) == 12:
            signal = 1.0
            for old_return in list(returns)[:-1]:
                signal *= 1.0 + old_return
            batch.append((date, permno, signal - 1.0))

        returns.append(ret)
        if len(batch) == 10_000:
            db.executemany("INSERT INTO momentum_signal VALUES (?, ?, ?)", batch)
            batch.clear()

    if batch:
        db.executemany("INSERT INTO momentum_signal VALUES (?, ?, ?)", batch)
    db.execute("CREATE INDEX idx_momentum_date ON momentum_signal (date)")
    db.commit()


def calculate_high_frequency_combo_signals(db: sqlite3.Connection) -> None:
    """Industry momentum + industry-relative reversal по FF49 industries."""
    db.executescript(
        """
        DROP TABLE IF EXISTS high_frequency_combo_signal;
        CREATE TABLE high_frequency_combo_signal (
            date TEXT NOT NULL,
            permno TEXT NOT NULL,
            signal REAL NOT NULL,
            PRIMARY KEY (date, permno)
        );
        """
    )
    ranges = load_ff49_ranges()
    dates = [row[0] for row in db.execute("SELECT DISTINCT date FROM monthly_stock ORDER BY date")]
    batch = []
    for previous_date, date in zip(dates, dates[1:]):
        previous = db.execute(
            "SELECT permno, ret, market_cap, sic FROM monthly_stock WHERE date = ?", (previous_date,)
        ).fetchall()
        totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
        observations = []
        for permno, ret, market_cap, sic in previous:
            group = ff49_industry(sic, ranges)
            if group is None:
                continue
            totals[group][0] += market_cap * ret
            totals[group][1] += market_cap
            observations.append((permno, ret, group))
        industry_return = {group: numerator / denominator for group, (numerator, denominator) in totals.items() if denominator}
        components = [
            {"permno": permno, "industry_momentum": industry_return[group],
             "relative_reversal": industry_return[group] - ret}
            for permno, ret, group in observations if group in industry_return
        ]
        momentum_rank = _percentile_ranks(components, "industry_momentum")
        reversal_rank = _percentile_ranks(components, "relative_reversal")
        for row in components:
            permno = row["permno"]
            batch.append((date, permno, momentum_rank[permno] + reversal_rank[permno]))
        if len(batch) >= 10_000:
            db.executemany("INSERT INTO high_frequency_combo_signal VALUES (?, ?, ?)", batch)
            batch.clear()
    if batch:
        db.executemany("INSERT INTO high_frequency_combo_signal VALUES (?, ?, ?)", batch)
    db.execute("CREATE INDEX idx_hfc_date ON high_frequency_combo_signal (date)")
    db.commit()


def select_portfolio(rows: list[sqlite3.Row], strategy: dict) -> dict[str, float]:
    """Построить value-weighted long/short portfolio из одного месячного среза."""
    nyse_signals = sorted(row["signal"] for row in rows if row["exchange"] == "N")
    if len(nyse_signals) < 10:
        return {}

    low_index = int((len(nyse_signals) - 1) * strategy["short_quantile"])
    high_index = int((len(nyse_signals) - 1) * strategy["long_quantile"])
    low_cutoff = nyse_signals[low_index]
    high_cutoff = nyse_signals[high_index]

    long_rows = [row for row in rows if row["signal"] >= high_cutoff]
    short_rows = [row for row in rows if row["signal"] <= low_cutoff]
    long_cap = sum(row["market_cap"] for row in long_rows)
    short_cap = sum(row["market_cap"] for row in short_rows)
    if not long_cap or not short_cap:
        return {}


    weights = {}
    for row in long_rows:
        weights[row["permno"]] = 0.5 * row["market_cap"] / long_cap
    for row in short_rows:
        weights[row["permno"]] = weights.get(row["permno"], 0.0) - 0.5 * row["market_cap"] / short_cap
    return {permno: weight for permno, weight in weights.items() if abs(weight) > 1e-12}


def select_buy_hold_portfolio(
    rows: list[sqlite3.Row], strategy: dict, held_long: set[str], held_short: set[str]
) -> tuple[dict[str, float], set[str], set[str]]:
    """Применить sS buy/hold rule: войти в 10%, держать в 20%."""
    nyse_signals = sorted(row["signal"] for row in rows if row["exchange"] == "N")
    if len(nyse_signals) < 10:
        return {}, set(), set()

    def cutoff(quantile: float) -> float:
        return nyse_signals[int((len(nyse_signals) - 1) * quantile)]

    entry_long, hold_long = cutoff(strategy["entry_long"]), cutoff(strategy["hold_long"])
    entry_short, hold_short = cutoff(strategy["entry_short"]), cutoff(strategy["hold_short"])
    by_permno = {row["permno"]: row for row in rows}

    next_long = {permno for permno in held_long
                 if permno in by_permno and by_permno[permno]["signal"] >= hold_long}
    next_short = {permno for permno in held_short
                  if permno in by_permno and by_permno[permno]["signal"] <= hold_short}
    next_long |= {row["permno"] for row in rows if row["signal"] >= entry_long}
    next_short |= {row["permno"] for row in rows if row["signal"] <= entry_short}

    long_rows = [by_permno[permno] for permno in next_long]
    short_rows = [by_permno[permno] for permno in next_short]
    long_cap = sum(row["market_cap"] for row in long_rows)
    short_cap = sum(row["market_cap"] for row in short_rows)
    if not long_cap or not short_cap:
        return {}, set(), set()

    weights = {row["permno"]: 0.5 * row["market_cap"] / long_cap for row in long_rows}
    for row in short_rows:
        weights[row["permno"]] = weights.get(row["permno"], 0.0) - 0.5 * row["market_cap"] / short_cap
    return weights, next_long, next_short


def select_adaptive_buy_hold_portfolio(
    rows: list[sqlite3.Row], strategy: dict, held_long: set[str], held_short: set[str]
) -> tuple[dict[str, float], set[str], set[str]]:
    """Buy/hold с индивидуальными границами, зависящими от lagged half-spread.

    b_i = clip(0.10 - lambda_b * c_i, 0.01, 0.10) задаёт узкий хвост
    для нового входа. h_i = clip(0.20 + lambda_h * c_i, 0.20, 0.50)
    задаёт более широкий хвост, в котором старую позицию можно сохранить.
    """
    nyse_signals = sorted(row["signal"] for row in rows if row["exchange"] == "N")
    if len(nyse_signals) < 10:
        return {}, set(), set()

    def cutoff(quantile: float) -> float:
        quantile = min(1.0, max(0.0, quantile))
        return nyse_signals[int((len(nyse_signals) - 1) * quantile)]

    def clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def bounds(row: sqlite3.Row) -> tuple[float, float]:
        # У части бумаг нет c. Не считаем их бесплатно и не исключаем из alpha
        # universe: в signal rule для них остаётся обычное 10%/20% правило.
        c = row["lagged_c"]
        if c is None:
            return strategy["entry_tail_base"], strategy["hold_tail_base"]
        buy_tail = clip(
            strategy["entry_tail_base"] - strategy["lambda_buy"] * c,
            strategy["entry_tail_min"], strategy["entry_tail_max"],
        )
        hold_tail = clip(
            strategy["hold_tail_base"] + strategy["lambda_hold"] * c,
            strategy["hold_tail_min"], strategy["hold_tail_max"],
        )
        return buy_tail, hold_tail

    by_permno = {row["permno"]: row for row in rows}
    next_long = {
        permno for permno in held_long
        if permno in by_permno
        and by_permno[permno]["signal"] >= cutoff(1.0 - bounds(by_permno[permno])[1])
    }
    next_short = {
        permno for permno in held_short
        if permno in by_permno
        and by_permno[permno]["signal"] <= cutoff(bounds(by_permno[permno])[1])
    }
    next_long |= {
        row["permno"] for row in rows
        if row["signal"] >= cutoff(1.0 - bounds(row)[0])
    }
    next_short |= {
        row["permno"] for row in rows
        if row["signal"] <= cutoff(bounds(row)[0])
    }
    # При h=50% границы могут встретиться на медиане; не допускаем long и short
    # по одной бумаге одновременно.
    overlap = next_long & next_short
    next_long -= overlap
    next_short -= overlap

    long_rows = [by_permno[permno] for permno in next_long]
    short_rows = [by_permno[permno] for permno in next_short]
    long_cap = sum(row["market_cap"] for row in long_rows)
    short_cap = sum(row["market_cap"] for row in short_rows)
    if not long_cap or not short_cap:
        return {}, set(), set()

    weights = {row["permno"]: 0.5 * row["market_cap"] / long_cap for row in long_rows}
    for row in short_rows:
        weights[row["permno"]] = weights.get(row["permno"], 0.0) - 0.5 * row["market_cap"] / short_cap
    return weights, next_long, next_short


def average_sleeves(sleeves: list[dict[str, float]]) -> dict[str, float]:
    """Сложить mini-portfolios и разделить веса на число слоёв."""
    combined: dict[str, float] = defaultdict(float)
    for sleeve in sleeves:
        for permno, weight in sleeve.items():
            combined[permno] += weight / len(sleeves)
    return {permno: weight for permno, weight in combined.items() if abs(weight) > 1e-12}


def select_low_cost_universe(rows: list[sqlite3.Row], strategy: dict) -> list[sqlite3.Row]:
    """Оставить нижнюю треть lagged c внутри каждого NYSE size decile.

    """
    nyse_sizes = sorted(row["market_cap"] for row in rows if row["exchange"] == "N")
    if len(nyse_sizes) < strategy["size_deciles"]:
        return []

    size_cutoffs = [nyse_sizes[int((len(nyse_sizes) - 1) * q / strategy["size_deciles"])]
                    for q in range(1, strategy["size_deciles"])]

    def size_bin(market_cap: float) -> int:
        return sum(market_cap > cutoff for cutoff in size_cutoffs)

    nyse_costs: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        if row["exchange"] == "N" and row["lagged_c"] is not None:
            nyse_costs[size_bin(row["market_cap"])].append(row["lagged_c"])

    cost_cutoff: dict[int, float] = {}
    for bucket, costs in nyse_costs.items():
        costs.sort()
        if costs:
            index = int((len(costs) - 1) * strategy["keep_cost_quantile"])
            cost_cutoff[bucket] = costs[index]

    return [row for row in rows
            if row["lagged_c"] is not None
            and size_bin(row["market_cap"]) in cost_cutoff
            and row["lagged_c"] <= cost_cutoff[size_bin(row["market_cap"])]]


def _percentile_ranks(rows: list[dict], field: str) -> dict[str, float]:
    """Нормализованный rank: 0 = низкое значение, 1 = высокое."""
    usable = sorted((row[field], row["permno"]) for row in rows if row[field] is not None)
    if len(usable) < 2:
        return {}
    return {permno: index / (len(usable) - 1) for index, (_, permno) in enumerate(usable)}


def _signal_rows(db: sqlite3.Connection, date: str, strategy: dict) -> list[dict]:
    """Вернуть единый срез с signal независимо от его источника."""
    signal = strategy["signal"]
    if signal == "momentum_12_2":
        rows = db.execute(
            """
            SELECT s.permno, s.signal, m.market_cap, m.exchange, c.c_log AS lagged_c
            FROM momentum_signal s
            JOIN monthly_stock m ON m.date = s.date AND m.permno = s.permno
            LEFT JOIN effective_cost c ON c.permno = s.permno
              AND c.year = CAST(substr(s.date, 1, 4) AS INTEGER) - ?
            WHERE s.date = ?
            """, (strategy.get("cost_lag_years", 0), date)
        ).fetchall()
        return [dict(row) for row in rows]

    if signal == "high_frequency_combo":
        rows = db.execute(
            """
            SELECT s.permno, s.signal, m.market_cap, m.exchange, c.c_log AS lagged_c
            FROM high_frequency_combo_signal s
            JOIN monthly_stock m ON m.date = s.date AND m.permno = s.permno
            LEFT JOIN effective_cost c ON c.permno = s.permno
              AND c.year = CAST(substr(s.date, 1, 4) AS INTEGER) - ?
            WHERE s.date = ?
            """, (strategy.get("cost_lag_years", 0), date)
        ).fetchall()
        return [dict(row) for row in rows]

    rows = [dict(row) for row in db.execute(
        """
        SELECT m.permno, m.market_cap, m.exchange, c.c_log AS lagged_c,
               a.book_to_market, a.gross_profitability, a.investment, ms.signal AS momentum
        FROM monthly_stock m
        JOIN annual_characteristic a ON a.permno = m.permno
          AND a.available_from = (
              SELECT MAX(a2.available_from) FROM annual_characteristic a2
              WHERE a2.permno = m.permno AND a2.available_from <= m.date
          )
        LEFT JOIN momentum_signal ms ON ms.date = m.date AND ms.permno = m.permno
        LEFT JOIN effective_cost c ON c.permno = m.permno
          AND c.year = CAST(substr(m.date, 1, 4) AS INTEGER) - ?
        WHERE m.date = ?
        """, (strategy.get("cost_lag_years", 0), date)
    )]
    if signal == "investment":
        for row in rows:
            row["signal"] = row["investment"]
    elif signal == "valprof":
        value_rank = _percentile_ranks(rows, "book_to_market")
        profit_rank = _percentile_ranks(rows, "gross_profitability")
        for row in rows:
            row["signal"] = (
                value_rank.get(row["permno"]) + profit_rank.get(row["permno"])
                if row["permno"] in value_rank and row["permno"] in profit_rank else None
            )
    elif signal == "valmom":
        value_rank = _percentile_ranks(rows, "book_to_market")
        momentum_rank = _percentile_ranks(rows, "momentum")
        for row in rows:
            row["signal"] = (
                value_rank.get(row["permno"]) + momentum_rank.get(row["permno"])
                if row["permno"] in value_rank and row["permno"] in momentum_rank else None
            )
    else:
        raise NotImplementedError(f"Signal {signal} пока не реализован.")
    return [row for row in rows if row["signal"] is not None]


def write_transactions(
    strategy_name: str,
    db: sqlite3.Connection,
    execution: str = "basic",
    lambda_buy: float | None = None,
    lambda_hold: float | None = None,
    entry_tail_base: float | None = None,
    hold_tail_base: float | None = None,
) -> None:
    """Из последовательности target weights создать читаемый журнал сделок."""
    strategy = resolve_strategy(
        strategy_name, execution, lambda_buy, lambda_hold, entry_tail_base, hold_tail_base
    )
    output_name = strategy["output_name"]
    calculate_momentum_signals(db)
    if strategy["signal"] == "high_frequency_combo":
        calculate_high_frequency_combo_signals(db)
    db.row_factory = sqlite3.Row
    dates = [row[0] for row in db.execute("SELECT DISTINCT date FROM monthly_stock ORDER BY date")]
    if strategy["rebalance"] == "annual":
        dates = [date for date in dates if date[5:7] == "07"]

    targets: list[tuple[str, str, float]] = []
    trades: list[dict] = []
    old_weights: dict[str, float] = {}
    held_long: set[str] = set()
    held_short: set[str] = set()
    sleeves: list[dict[str, float]] | None = None
    next_sleeve = 0

    for date in dates:
        if date < strategy["start"]:
            continue
        rows = _signal_rows(db, date, strategy)
        if strategy["method"] == "buy_hold":
            new_weights, held_long, held_short = select_buy_hold_portfolio(
                rows, strategy, held_long, held_short
            )
        elif strategy["method"] == "adaptive_buy_hold":
            new_weights, held_long, held_short = select_adaptive_buy_hold_portfolio(
                rows, strategy, held_long, held_short
            )
        elif strategy["method"] == "staggered":
            # Новый signal строится каждый месяц, но заменяет holdings лишь одного
            # постоянного mini-portfolio. Остальные слои ничего не торгуют.
            fresh_sleeve = select_portfolio(rows, strategy)
            if sleeves is None:
                # В первой дате наполняем все слои, чтобы не начинать с 1/3 exposure.
                sleeves = [fresh_sleeve.copy() for _ in range(strategy["sleeves"])]
            else:
                sleeves[next_sleeve] = fresh_sleeve
                next_sleeve = (next_sleeve + 1) % strategy["sleeves"]
            new_weights = average_sleeves(sleeves)
        elif strategy["method"] == "low_cost_universe":
            new_weights = select_portfolio(select_low_cost_universe(rows, strategy), strategy)
        else:
            new_weights = select_portfolio(rows, strategy)
        if not new_weights:
            continue

        targets.extend((date, permno, weight) for permno, weight in new_weights.items())
        for permno in sorted(set(old_weights) | set(new_weights)):
            old = old_weights.get(permno, 0.0)
            new = new_weights.get(permno, 0.0)
            _append_trade_rows(trades, date, permno, old, new)
        old_weights = new_weights

    OUTPUT.mkdir(exist_ok=True)
    _write_csv(OUTPUT / f"{output_name}_targets.csv", ["date", "permno", "target_weight"], targets)
    _write_dict_csv(
        OUTPUT / f"{output_name}_transactions.csv",
        ["date", "permno", "action", "notional_weight", "old_weight", "new_weight"],
        trades,
    )
    print(f"Готово: {len(trades):,} сделок записано в output/{output_name}_transactions.csv")


def _append_trade_rows(trades: list[dict], date: str, permno: str, old: float, new: float) -> None:
    """Записать BUY/SELL/SHORT/COVER, включая смену long на short и наоборот."""
    if abs(old - new) < 1e-12:
        return

    def add(action: str, notional: float) -> None:
        trades.append({"date": date, "permno": permno, "action": action,
                       "notional_weight": round(notional, 12), "old_weight": old, "new_weight": new})

    if old >= 0 and new >= 0:
        add("BUY" if new > old else "SELL", abs(new - old))
    elif old <= 0 and new <= 0:
        add("SHORT" if new < old else "COVER", abs(new - old))
    elif old > 0:
        add("SELL", old)
        add("SHORT", abs(new))
    else:
        add("COVER", abs(old))
        add("BUY", new)


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def _write_dict_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
