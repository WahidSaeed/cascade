"""Ledger rollups (BUILD.md §13, P7-02): hourly aggregate rows maintained in the same transaction as every
ledger and overhead write, so the dashboard reads small tables at any volume. Totals reconcile exactly with
the raw ledger (tests/unit/test_phase7_acceptance.py).

The dashboard reads rollups whenever the requested range is whole hours (the dashboard's ranges are), and
falls back to the raw ledger for unaligned ranges and per-collection breakdowns (a request can count toward
several collections, which an additive rollup cannot express).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Connection,
    Integer,
    bindparam,
    case,
    cast,
    delete,
    func,
    insert,
    literal,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import LedgerRollup, LedgerRow, OverheadRollup, OverheadRow

HOUR = 3600
INT_SUMS = ("input_tokens", "output_tokens", "embed_tokens", "tokens_avoided_in", "tokens_avoided_out",
            "context_avoided_tokens", "prompt_tokens", "context_tokens", "history_tokens", "system_tokens",
            "cache_read_tokens", "cache_write_tokens")
FLOAT_SUMS = ("cost_actual_usd", "cost_avoided_usd", "runtime_cost_usd", "embed_cost_usd")
LEDGER_SUMS = INT_SUMS + FLOAT_SUMS
METHODS = ("cache_exact", "pattern_measured", "heuristic")


def bucket(ts: float) -> int:
    return int(ts // HOUR) * HOUR


def bucket_expr(col: Any, dialect: str, size: int = HOUR) -> ColumnElement[Any]:
    """floor(ts / size) * size in SQL. (SQLite's CAST truncates, which is floor for positive timestamps;
    Postgres' CAST rounds, so it needs FLOOR.)"""
    if dialect == "postgresql":
        return cast(func.floor(col / size), BigInteger) * size
    return cast(col / size, Integer) * size


def aligned(frm: float | None, to: float | None) -> bool:
    return (frm is None or frm % HOUR == 0) and (to is None or to % HOUR == 0)


# ----------------------------------------------------------------------------- writes
_stmts: dict[tuple[str, str], Any] = {}


def _upsert(s: Session | Connection, model: Any, keys: dict[str, Any], incs: dict[str, Any]) -> None:
    """INSERT … ON CONFLICT (bucket key) DO UPDATE SET col = col + excluded.col — one statement per
    (dialect, table), built once with bind parameters (it runs on every request)."""
    dialect = (s.get_bind() if isinstance(s, Session) else s).dialect.name
    stmt = _stmts.get((dialect, model.__tablename__))
    if stmt is None:
        table = model.__table__
        ins = (pg_insert if dialect == "postgresql" else sqlite_insert)(table).values(
            {c: bindparam(c) for c in (*keys, *incs)})
        stmt = ins.on_conflict_do_update(index_elements=list(keys),
                                         set_={k: table.c[k] + ins.excluded[k] for k in incs})
        _stmts[(dialect, model.__tablename__)] = stmt
    s.execute(stmt, {**keys, **incs})


def add_ledger(s: Session, row: dict[str, Any]) -> None:
    """Add one ledger row to its hourly bucket (call inside the transaction that writes the row)."""
    keys = {"bucket": bucket(row["ts"]), "user_id": row["user_id"], "pattern_id": row.get("pattern_id") or "",
            "route": row["route"]}
    incs: dict[str, Any] = {k: row.get(k) or 0 for k in LEDGER_SUMS}
    method = row.get("estimate_method")
    incs.update({"requests": 1,
                 "heuristic_avoided_usd": (row.get("cost_avoided_usd") or 0.0) if method == "heuristic" else 0.0,
                 "uncited": 1 if row.get("uncited") else 0, "simulated": 1 if row.get("simulated") else 0,
                 **{f"n_{m}": 1 if method == m else 0 for m in METHODS}})
    _upsert(s, LedgerRollup, keys, incs)


def add_overhead(s: Session, row: dict[str, Any]) -> None:
    keys = {"bucket": bucket(row["ts"]), "user_id": row.get("user_id") or "", "pattern_id": row.get("pattern_id") or "",
            "collection_id": row.get("collection_id") or "", "category": row["category"]}
    incs = {"entries": 1, "input_tokens": row.get("input_tokens") or 0, "output_tokens": row.get("output_tokens") or 0,
            "cost_usd": row.get("cost_usd") or 0.0}
    _upsert(s, OverheadRollup, keys, incs)


def rebuild_sql(conn: Connection) -> None:
    """Recompute every rollup row from the raw tables with one INSERT … SELECT … GROUP BY each (migration
    backfill, and after bulk edits such as the demo seed re-timestamping overhead)."""
    d = conn.dialect.name
    L, OH = LedgerRow.__table__.c, OverheadRow.__table__.c
    conn.execute(delete(LedgerRollup))
    conn.execute(delete(OverheadRollup))
    b = bucket_expr(L.ts, d).label("bucket")
    pid = func.coalesce(L.pattern_id, literal("")).label("pattern_id")
    method_counts = [func.sum(case((L.estimate_method == m, 1), else_=0)).label(f"n_{m}") for m in METHODS]
    sel = select(
        b, L.user_id, pid, L.route, func.count().label("requests"),
        *[func.coalesce(func.sum(L[c]), 0).label(c) for c in LEDGER_SUMS],
        func.coalesce(func.sum(case((L.estimate_method == "heuristic", L.cost_avoided_usd), else_=0.0)), 0.0)
        .label("heuristic_avoided_usd"),
        func.sum(case((L.uncited.is_(True), 1), else_=0)).label("uncited"),
        func.sum(case((L.simulated.is_(True), 1), else_=0)).label("simulated"),
        *method_counts,
    ).group_by(b, L.user_id, pid, L.route)
    cols = ["bucket", "user_id", "pattern_id", "route", "requests", *LEDGER_SUMS, "heuristic_avoided_usd",
            "uncited", "simulated", *[f"n_{m}" for m in METHODS]]
    conn.execute(insert(LedgerRollup).from_select(cols, sel))
    ob = bucket_expr(OH.ts, d).label("bucket")
    keys = [ob, func.coalesce(OH.user_id, literal("")).label("user_id"),
            func.coalesce(OH.pattern_id, literal("")).label("pattern_id"),
            func.coalesce(OH.collection_id, literal("")).label("collection_id"), OH.category]
    osel = select(*keys, func.count().label("entries"), func.coalesce(func.sum(OH.input_tokens), 0).label("i"),
                  func.coalesce(func.sum(OH.output_tokens), 0).label("o"),
                  func.coalesce(func.sum(OH.cost_usd), 0.0).label("c")).group_by(*keys)
    conn.execute(insert(OverheadRollup).from_select(
        ["bucket", "user_id", "pattern_id", "collection_id", "category", "entries", "input_tokens", "output_tokens",
         "cost_usd"], osel))


def rebuild(s: Session) -> None:
    rebuild_sql(s.connection())
