"""Готовит annual accounting characteristics без look-ahead bias.

Fiscal-year data с датой в году y становятся доступными стратегии только
с 1 июля y+1, как в Appendix B статьи.
"""

from __future__ import annotations

import sqlite3

from build import connect


def book_equity(row: sqlite3.Row) -> float | None:
    stockholders_equity = row["seq"]
    if stockholders_equity is None:
        stockholders_equity = row["ceq"]
    if stockholders_equity is None and row["at"] is not None and row["lt"] is not None:
        stockholders_equity = row["at"] - row["lt"]
    if stockholders_equity is None:
        return None
    preferred = row["pstkrv"] or row["pstkl"] or row["pstk"] or 0.0
    return stockholders_equity + (row["txditc"] or 0.0) - preferred


def prepare_annual_characteristics() -> None:
    with connect() as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            DROP TABLE IF EXISTS annual_characteristic;
            CREATE TABLE annual_characteristic (
                available_from TEXT NOT NULL,
                permno TEXT NOT NULL,
                book_to_market REAL,
                gross_profitability REAL,
                investment REAL,
                PRIMARY KEY (available_from, permno)
            );
            """
        )
        december_cap = {
            (int(date[:4]), permno): cap
            for date, permno, cap in db.execute(
                "SELECT date, permno, market_cap FROM monthly_stock WHERE substr(date, 6, 2) = '12'"
            )
        }
        links_by_gvkey: dict[str, list[sqlite3.Row]] = {}
        for link in db.execute("SELECT gvkey, permno, link_start, link_end FROM ccm_link"):
            links_by_gvkey.setdefault(link["gvkey"], []).append(link)
        rows = db.execute(
            """
            SELECT gvkey, datadate, at, revt, cogs, ppegt, invt, seq, ceq, lt,
                   txditc, pstkrv, pstkl, pstk
            FROM annual_fundamental
            ORDER BY gvkey, datadate
            """
        )
        previous: dict[str, sqlite3.Row] = {}
        batch = []
        for row in rows:
            year = int(row["datadate"][:4])
            available_from = f"{year + 1}-07-01"
            prior = previous.get(row["gvkey"])
            gp = None
            if row["revt"] is not None and row["cogs"] is not None and row["at"] and row["at"] > 0:
                gp = (row["revt"] - row["cogs"]) / row["at"]
            investment = None
            if prior is not None and prior["at"] and prior["at"] > 0:
                if all(row[x] is not None and prior[x] is not None for x in ("ppegt", "invt")):
                    # Высокий investment предсказывает низкую доходность, поэтому
                    # меняем знак: более высокий signal всегда означает long-кандидата.
                    investment = -((row["ppegt"] - prior["ppegt"] + row["invt"] - prior["invt"]) / prior["at"])
            be = book_equity(row)
            bm = None
            links = [
                link for link in links_by_gvkey.get(row["gvkey"], [])
                if link["link_start"] <= available_from <= link["link_end"]
            ]
            for link in links:
                permno = link["permno"]
                cap = december_cap.get((year, permno))
                bm = be / cap if be is not None and cap and cap > 0 else None
                if bm is not None or gp is not None or investment is not None:
                    batch.append((available_from, permno, bm, gp, investment))
            previous[row["gvkey"]] = row
            if len(batch) >= 10_000:
                db.executemany("INSERT OR REPLACE INTO annual_characteristic VALUES (?, ?, ?, ?, ?)", batch)
                batch.clear()
        if batch:
            db.executemany("INSERT OR REPLACE INTO annual_characteristic VALUES (?, ?, ?, ?, ?)", batch)
        db.execute("CREATE INDEX idx_annual_characteristic_permno_date ON annual_characteristic (permno, available_from)")
        total = db.execute("SELECT count(*) FROM annual_characteristic").fetchone()[0]
        db.commit()
    print(f"Annual characteristics: {total:,} строк")


if __name__ == "__main__":
    prepare_annual_characteristics()
