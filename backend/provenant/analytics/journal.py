"""Journal entry tests over a client's whole general ledger, in DuckDB over Parquet. Deterministic.

The ledger never becomes Python rows: DuckDB reads the CSV, checks it, writes Parquet, and every test is SQL
over that file, so a multi-million-line ledger costs disk, not memory. Amounts are DECIMAL(18,2) throughout.

GL CSV: entry_id, date, account_code and amount (debit positive) or debit and credit; optional posted_at
(date and time), description, user, source.

Tests are fixed and versioned (TEST_VERSION). They select entries for a person to look at; none of them says an
entry is wrong. Every flagged entry links back to its lines.
"""
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

TEST_VERSION = "je-2026.1"
ROUND_FLOOR = Decimal("100000")  # round-sum test: lines of at least this, in whole tens of thousands
ROUND_UNIT = Decimal("10000")
RARE_ACCOUNT_LINES = 2  # accounts used this many times or fewer in the year
PERIOD_END_DAYS = 5
WORK_HOURS = (7, 20)  # postings outside 07:00-20:00
SAMPLE = 200  # entries listed per test; counts and totals cover the whole population

TESTS = {
    "unbalanced": ("Entries that do not balance", "Debits and credits of the entry differ."),
    "duplicates": ("Possible duplicates", "Same account, amount, date and description in more than one entry."),
    "round_amounts": ("Round amounts", "A line of 100,000 or more in whole tens of thousands."),
    "weekend": ("Weekend postings", "Dated on a Saturday or Sunday."),
    "after_hours": ("After-hours postings", "Posted before 07:00 or after 20:00 (needs posted_at)."),
    "rare_users": ("Unusual users", "Posted by someone with very few entries in the year (needs user)."),
    "rare_accounts": ("Rarely used accounts", f"Touches an account with {RARE_ACCOUNT_LINES} or fewer lines in the year."),
    "period_end": ("Period-end entries", f"Dated in the last {PERIOD_END_DAYS} days of the year, or after it."),
    "near_limit": ("Just below the approval limit", "Entry value within 10% below the approval limit entered."),
}


class LedgerError(ValueError):
    pass


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit = '512MB'; SET threads = 2")  # a laptop shares the machine with everything else
    return con


def load(csv_path: Path, parquet_path: Path) -> int:
    """Validate the CSV and write the normalised ledger to Parquet. Returns the number of lines."""
    con = _connect()
    try:
        con.execute(f"CREATE VIEW raw AS SELECT * FROM read_csv({_q(str(csv_path))}, header = true, all_varchar = true)")
    except duckdb.Error as exc:
        raise LedgerError(f"the general ledger could not be read as CSV: {exc}") from exc
    columns = {c.strip().lower(): c for (c,) in con.execute("SELECT column_name FROM (DESCRIBE raw)").fetchall()}
    need = ["entry_id", "date", "account_code"]
    missing = [c for c in need if c not in columns]
    if missing or not ("amount" in columns or {"debit", "credit"} <= set(columns)):
        raise LedgerError("general ledger: columns needed: entry_id, date, account_code, and amount (or debit and "
                          f"credit); optional posted_at, description, user, source. Missing: {', '.join(missing) or 'amount'}")

    def col(name: str) -> str:
        return f'"{columns[name]}"' if name in columns else "NULL"

    def money(expr: str) -> str:
        return f"TRY_CAST(NULLIF(replace(trim({expr}), ',', ''), '') AS DECIMAL(18,2))"

    amount = money(col("amount")) if "amount" in columns else (
        f"COALESCE({money(col('debit'))}, 0) - COALESCE({money(col('credit'))}, 0)")
    when = col("date")
    con.execute(f"""
        CREATE TABLE gl AS SELECT
            row_number() OVER () + 1 AS row_no,
            trim({col('entry_id')}) AS entry_id,
            COALESCE(TRY_CAST({when} AS DATE), TRY_STRPTIME({when}, '%d/%m/%Y')::DATE) AS date,
            TRY_CAST({col('posted_at')} AS TIMESTAMP) AS posted_at,
            trim({col('account_code')}) AS account_code,
            COALESCE({col('description')}, '') AS description,
            NULLIF(trim(COALESCE({col('user')}, '')), '') AS user_id,
            COALESCE({col('source')}, '') AS source,
            {amount} AS amount,
            {when} AS date_text
        FROM raw
    """)
    bad = con.execute("""
        SELECT row_no, CASE WHEN entry_id IS NULL OR entry_id = '' THEN 'entry_id is empty'
                            WHEN account_code IS NULL OR account_code = '' THEN 'account_code is empty'
                            WHEN date IS NULL THEN 'date ' || COALESCE(date_text, '') || ' is not a date'
                            ELSE 'amount is not a number' END
        FROM gl WHERE entry_id IS NULL OR entry_id = '' OR account_code IS NULL OR account_code = ''
                   OR date IS NULL OR amount IS NULL
        ORDER BY row_no LIMIT 5
    """).fetchall()
    if bad:
        raise LedgerError("general ledger: " + "; ".join(f"row {r}: {why}" for r, why in bad))
    rows = con.execute("SELECT count(*) FROM gl").fetchone()[0]
    if not rows:
        raise LedgerError("the general ledger has no lines")
    con.execute(f"COPY (SELECT * EXCLUDE (date_text) FROM gl ORDER BY row_no) TO {_q(str(parquet_path))} (FORMAT parquet)")
    con.close()
    return rows


@dataclass(frozen=True)
class Flag:
    entry_id: str
    value: Decimal  # the entry's debits
    detail: str


@dataclass(frozen=True)
class TestResult:
    key: str
    title: str
    description: str
    count: int  # entries flagged, whole population
    value: Decimal  # their debits
    sample: tuple[Flag, ...]
    applies: bool = True  # False when the ledger lacks the column the test needs


@dataclass(frozen=True)
class Benford:
    lines: int
    observed: tuple[float, ...]  # proportions for digits 1-9 (statistics, not money)
    expected: tuple[float, ...]
    mad: float
    conformity: str  # close | acceptable | marginal | nonconformity (Nigrini's first-digit thresholds)


@dataclass(frozen=True)
class ReconRow:
    account_code: str
    ledger: Decimal
    trial_balance: Decimal

    @property
    def difference(self) -> Decimal:
        return self.ledger - self.trial_balance


@dataclass(frozen=True)
class Analysis:
    lines: int
    entries: int
    debits: Decimal
    first_date: date | None
    last_date: date | None
    tests: tuple[TestResult, ...]
    benford: Benford | None
    reconciliation: tuple[ReconRow, ...]  # profit or loss accounts: the year's movement is the balance
    risk: tuple[tuple[str, Decimal, tuple[str, ...]], ...]  # entries flagged by most tests first


def analyse(parquet_path: Path, year_start: date, year_end: date, *, approval_limit: Decimal | None = None,
            pl_balances: Mapping[str, Decimal] | None = None) -> Analysis:
    con = _connect()
    con.execute(f"CREATE VIEW gl AS SELECT * FROM read_parquet({_q(str(parquet_path))})")
    con.execute("CREATE TABLE entries AS SELECT entry_id, sum(greatest(amount, 0)) AS value, min(date) AS date "
                "FROM gl GROUP BY entry_id")
    lines, debits, first, last = con.execute(
        "SELECT count(*), COALESCE(sum(greatest(amount, 0)), 0), min(date), max(date) FROM gl").fetchone()
    n_entries = con.execute("SELECT count(*) FROM entries").fetchone()[0]
    has_time = con.execute("SELECT count(posted_at) > 0 FROM gl").fetchone()[0]
    has_user = con.execute("SELECT count(user_id) > 0 FROM gl").fetchone()[0]
    params = {"start": year_start, "end": year_end}

    selections: dict[str, tuple[str, dict]] = {
        "unbalanced": ("SELECT entry_id, 'debits less credits ' || CAST(sum(amount) AS VARCHAR) FROM gl "
                       "GROUP BY entry_id HAVING sum(amount) <> 0", {}),
        "duplicates": ("""
            SELECT g.entry_id, 'same as ' || string_agg(DISTINCT o.entry_id, ', ')
            FROM gl g JOIN gl o ON o.account_code = g.account_code AND o.amount = g.amount AND o.date = g.date
              AND lower(trim(o.description)) = lower(trim(g.description)) AND o.entry_id <> g.entry_id
            GROUP BY g.entry_id""", {}),
        "round_amounts": (f"SELECT entry_id, 'line of ' || CAST(max(abs(amount)) AS VARCHAR) FROM gl "
                          f"WHERE abs(amount) >= {ROUND_FLOOR} AND abs(amount) % {ROUND_UNIT} = 0 GROUP BY entry_id", {}),
        "weekend": ("SELECT entry_id, dayname(min(date)) FROM gl WHERE dayofweek(date) IN (0, 6) GROUP BY entry_id", {}),
        "after_hours": (f"SELECT entry_id, 'posted ' || strftime(min(posted_at), '%Y-%m-%d %H:%M') FROM gl "
                        f"WHERE hour(posted_at) < {WORK_HOURS[0]} OR hour(posted_at) >= {WORK_HOURS[1]} "
                        "GROUP BY entry_id", {}),
        "rare_users": ("""
            WITH per_user AS (SELECT user_id, count(DISTINCT entry_id) AS n FROM gl WHERE user_id IS NOT NULL GROUP BY user_id),
                 cutoff AS (SELECT greatest(2, (SELECT count(DISTINCT entry_id) FROM gl) / 100) AS c)
            SELECT g.entry_id, 'by ' || min(g.user_id) || ' (' || CAST(min(u.n) AS VARCHAR) || ' entries in the year)'
            FROM gl g JOIN per_user u USING (user_id), cutoff WHERE u.n <= cutoff.c GROUP BY g.entry_id""", {}),
        "rare_accounts": (f"""
            WITH rare AS (SELECT account_code FROM gl GROUP BY account_code HAVING count(*) <= {RARE_ACCOUNT_LINES})
            SELECT entry_id, 'account ' || string_agg(DISTINCT account_code, ', ') FROM gl JOIN rare USING (account_code)
            GROUP BY entry_id""", {}),
        "period_end": (f"""
            SELECT entry_id, CASE WHEN min(date) > $end THEN 'dated after the year end' ELSE 'last days of the year' END
            FROM gl WHERE date > $end - INTERVAL {PERIOD_END_DAYS} DAY GROUP BY entry_id""", {"end": year_end}),
    }
    if approval_limit is not None:
        selections["near_limit"] = (
            "SELECT entry_id, 'entry value ' || CAST(value AS VARCHAR) FROM entries WHERE value >= $low AND value < $limit",
            {"low": approval_limit * Decimal("0.9"), "limit": approval_limit})

    results, flagged = [], {}
    for key, (title, description) in TESTS.items():
        applies = not ((key == "after_hours" and not has_time) or (key == "rare_users" and not has_user)
                       or (key == "near_limit" and approval_limit is None))
        if not applies:
            results.append(TestResult(key, title, description, 0, Decimal(0), (), False))
            continue
        sql, extra = selections[key]
        con.execute(f"CREATE OR REPLACE TABLE hit AS SELECT f.entry_id, f.detail, e.value FROM ({sql}) AS f(entry_id, detail) "
                    "JOIN entries e USING (entry_id)", extra or None)
        count, value = con.execute("SELECT count(*), COALESCE(sum(value), 0) FROM hit").fetchone()
        sample = con.execute(f"SELECT entry_id, value, detail FROM hit ORDER BY value DESC, entry_id LIMIT {SAMPLE}").fetchall()
        for (entry_id,) in con.execute("SELECT entry_id FROM hit").fetchall():
            flagged.setdefault(entry_id, []).append(key)
        results.append(TestResult(key, title, description, count, Decimal(value),
                                  tuple(Flag(e, Decimal(v), d) for e, v, d in sample)))

    values = dict(con.execute("SELECT entry_id, value FROM entries WHERE entry_id IN (SELECT unnest($ids))",
                              {"ids": list(flagged)}).fetchall()) if flagged else {}
    risk = sorted(((e, Decimal(values[e]), tuple(t)) for e, t in flagged.items()),
                  key=lambda x: (-len(x[2]), -x[1], x[0]))[:500]

    recon = ()
    if pl_balances is not None:
        moved = dict(con.execute("SELECT account_code, sum(amount) FROM gl WHERE date BETWEEN $start AND $end "
                                 "GROUP BY account_code", params).fetchall())
        recon = tuple(ReconRow(code, Decimal(moved.get(code, 0)), tb) for code, tb in sorted(pl_balances.items()))
    benford = _benford(con)
    con.close()
    return Analysis(lines, n_entries, Decimal(debits), first, last, tuple(results), benford, recon, tuple(risk))


def _benford(con: duckdb.DuckDBPyConnection) -> Benford | None:
    rows = con.execute("""
        SELECT CAST(substr(regexp_replace(CAST(abs(amount) AS VARCHAR), '^[0.]+', ''), 1, 1) AS INTEGER) AS d,
               count(*) FROM gl WHERE abs(amount) >= 10 GROUP BY d
    """).fetchall()
    counts = {d: n for d, n in rows if d and 1 <= d <= 9}
    total = sum(counts.values())
    if total < 100:
        return None  # too few lines for a first-digit test to mean anything
    expected = tuple(math.log10(1 + 1 / d) for d in range(1, 10))
    observed = tuple(counts.get(d, 0) / total for d in range(1, 10))
    mad = sum(abs(o - e) for o, e in zip(observed, expected)) / 9
    conformity = ("close" if mad <= 0.006 else "acceptable" if mad <= 0.012
                  else "marginal" if mad <= 0.015 else "nonconformity")
    return Benford(total, observed, expected, round(mad, 4), conformity)


def entry_lines(parquet_path: Path, entry_ids: Iterable[str]) -> list[tuple]:
    """The lines behind flagged entries, for the reviewer."""
    con = _connect()
    rows = con.execute(f"SELECT entry_id, row_no, date, posted_at, account_code, description, user_id, source, amount "
                       f"FROM read_parquet({_q(str(parquet_path))}) WHERE entry_id IN (SELECT unnest($ids)) "
                       f"ORDER BY entry_id, row_no", {"ids": list(entry_ids)}).fetchall()
    con.close()
    return rows
