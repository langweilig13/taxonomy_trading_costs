from __future__ import annotations

import csv
import gzip
import io
import sqlite3
import zipfile
from collections import defaultdict, deque
from pathlib import Path

from sources import GIBBS_COST_MEMBER, MONTHLY_STOCK_MEMBER, MONTHLY_STOCK_ZIP
from strategies import resolve_strategy

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

                        batch.append((row["MthCalDt"], row["PERMNO"], ret, market_cap, row["PrimaryExch"]))
                        if len(batch) == 10_000:
                            db.executemany("INSERT OR REPLACE INTO monthly_stock VALUES (?, ?, ?, ?, ?)", batch)
                            inserted += len(batch)
                            batch.clear()

                    if batch:
                        db.executemany("INSERT OR REPLACE INTO monthly_stock VALUES (?, ?, ?, ?, ?)", batch)
                        inserted += len(batch)

        db.execute("CREATE INDEX IF NOT EXISTS idx_monthly_permno_date ON monthly_stock (permno, date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_monthly_date ON monthly_stock (date)")
        _prepare_effective_costs(db)
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


def write_transactions(strategy_name: str, db: sqlite3.Connection, execution: str = "basic") -> None:
    """Из последовательности target weights создать читаемый журнал сделок."""
    strategy = resolve_strategy(strategy_name, execution)
    output_name = strategy["output_name"]
    if strategy["signal"] != "momentum_12_2":
        raise NotImplementedError("В MVP пока реализован только signal momentum_12_2.")

    calculate_momentum_signals(db)
    db.row_factory = sqlite3.Row
    dates = [row[0] for row in db.execute("SELECT DISTINCT date FROM momentum_signal ORDER BY date")]

    targets: list[tuple[str, str, float]] = []
    trades: list[dict] = []
    old_weights: dict[str, float] = {}
    held_long: set[str] = set()
    held_short: set[str] = set()
    sleeves: list[dict[str, float]] | None = None
    next_sleeve = 0

    for date in dates:
        rows = db.execute(
            """
            SELECT s.permno, s.signal, m.market_cap, m.exchange, c.c_log AS lagged_c
            FROM momentum_signal s
            JOIN monthly_stock m ON m.date = s.date AND m.permno = s.permno
            LEFT JOIN effective_cost c
              ON c.permno = s.permno
             AND c.year = CAST(substr(s.date, 1, 4) AS INTEGER) - ?
            WHERE s.date = ? AND s.date >= ?
            """,
            (strategy.get("cost_lag_years", 0), date, strategy["start"]),
        ).fetchall()
        if strategy["method"] == "buy_hold":
            new_weights, held_long, held_short = select_buy_hold_portfolio(
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
