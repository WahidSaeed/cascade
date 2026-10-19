"""The token ledger (BUILD.md §4.2) — the product's core metric.

Every request writes exactly one `LedgerEntry`; every overhead LLM call (compile, verify,
labelling, training, drift, ingestion, re-verification, discovery, agent) writes an
`OverheadEntry`. Savings are always reported net of overhead and artifact runtime cost.
Aggregates are plain SQL GROUP BY queries, so they reconcile exactly with the rows.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import String, any_, case, cast, func, literal, select
from sqlalchemy.sql.elements import ColumnElement

from . import db, rollups
from .models import LedgerRollup, LedgerRow, OverheadRollup, OverheadRow, new_id

ESTIMATE_METHODS = ("cache_exact", "pattern_measured", "heuristic")
# summary() column order: counts/tokens, then costs, then the input mix
_SUMMARY_INTS = ("input_tokens", "output_tokens", "embed_tokens", "tokens_avoided_in", "tokens_avoided_out",
                 "context_avoided_tokens", "prompt_tokens", "context_tokens", "history_tokens", "system_tokens",
                 "cache_read_tokens", "cache_write_tokens")
_SUMMARY_FLOATS = ("cost_actual_usd", "cost_avoided_usd", "runtime_cost_usd", "embed_cost_usd")


@dataclass
class LedgerEntry:
    request_id: str
    user_id: str
    route: str
    ts: float = field(default_factory=time.time)
    pattern_id: str | None = None
    artifact_kind: str | None = None
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_tokens: int = 0
    context_tokens: int = 0
    history_tokens: int = 0
    system_tokens: int = 0
    split_method: str | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    collection_ids: list[str] = field(default_factory=list)
    context_fingerprint: str | None = None
    sources: list[Any] = field(default_factory=list)
    uncited: bool = False
    embed_tokens: int = 0
    embed_cost_usd: float = 0.0
    cost_actual_usd: float = 0.0
    tokens_avoided_in: int = 0
    tokens_avoided_out: int = 0
    context_avoided_tokens: int = 0
    cost_avoided_usd: float = 0.0
    runtime_cost_usd: float = 0.0
    estimate_method: str | None = None
    latency_ms: float = 0.0
    simulated: bool = False


@dataclass
class OverheadEntry:
    category: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_id: str | None = None
    pattern_id: str | None = None
    run_id: str | None = None
    collection_id: str | None = None
    user_id: str | None = None
    note: str = ""
    ts: float = field(default_factory=time.time)
    simulated: bool = False


class Ledger:
    # ------------------------------------------------------------------ writes
    def record(self, entry: LedgerEntry) -> None:
        if entry.estimate_method is not None and entry.estimate_method not in ESTIMATE_METHODS:
            raise ValueError(f"unknown estimate_method {entry.estimate_method}")
        with db.session() as s:
            row = asdict(entry)
            s.add(LedgerRow(**row))
            rollups.add_ledger(s, row)

    def record_many(self, entries: list[LedgerEntry]) -> None:
        with db.session() as s:
            for e in entries:
                row = asdict(e)
                s.add(LedgerRow(**row))
                rollups.add_ledger(s, row)

    def record_overhead(self, entry: OverheadEntry) -> str:
        oid = new_id("oh")
        with db.session() as s:
            row = asdict(entry)
            s.add(OverheadRow(id=oid, **row))
            rollups.add_overhead(s, row)
        return oid

    # ------------------------------------------------------------------ filters
    @staticmethod
    def _ledger_where(frm: float | None, to: float | None, user_id: str | None, pattern_id: str | None,
                      collection_id: str | None) -> list[ColumnElement[bool]]:
        w: list[ColumnElement[bool]] = []
        if frm is not None:
            w.append(LedgerRow.ts >= frm)
        if to is not None:
            w.append(LedgerRow.ts < to)
        if user_id:
            w.append(LedgerRow.user_id == user_id)
        if pattern_id:
            w.append(LedgerRow.pattern_id == pattern_id)
        if collection_id:
            if db.dialect_name() == "postgresql":
                w.append(literal(collection_id) == any_(LedgerRow.collection_ids))
            else:
                # SQLite stores the list as JSON text: match the quoted id.
                w.append(cast(LedgerRow.collection_ids, String).like(f'%"{collection_id}"%'))
        return w

    @staticmethod
    def _overhead_where(frm: float | None, to: float | None, user_id: str | None, pattern_id: str | None,
                        collection_id: str | None) -> list[ColumnElement[bool]]:
        w: list[ColumnElement[bool]] = []
        if frm is not None:
            w.append(OverheadRow.ts >= frm)
        if to is not None:
            w.append(OverheadRow.ts < to)
        if user_id:
            w.append(OverheadRow.user_id == user_id)
        if pattern_id:
            w.append(OverheadRow.pattern_id == pattern_id)
        if collection_id:
            w.append(OverheadRow.collection_id == collection_id)
        return w

    # ------------------------------------------------------------------ aggregates
    # Each aggregate reads the hourly rollups (Phase 7) when the range is whole hours, else the raw ledger.
    # Both paths produce identical numbers (reconciliation test, P7-02). `raw=True` forces the raw path.
    @staticmethod
    def _use_rollups(frm: float | None, to: float | None, collection_id: str | None, raw: bool) -> bool:
        return not raw and collection_id is None and rollups.aligned(frm, to)

    @staticmethod
    def _rollup_where(model: Any, frm: float | None, to: float | None, user_id: str | None,
                      pattern_id: str | None) -> list[ColumnElement[bool]]:
        w: list[ColumnElement[bool]] = []
        if frm is not None:
            w.append(model.bucket >= frm)
        if to is not None:
            w.append(model.bucket < to)
        if user_id:
            w.append(model.user_id == user_id)
        if pattern_id:
            w.append(model.pattern_id == pattern_id)
        return w

    def summary(self, frm: float | None = None, to: float | None = None, user_id: str | None = None,
                pattern_id: str | None = None, collection_id: str | None = None, raw: bool = False) -> dict[str, Any]:
        if self._use_rollups(frm, to, collection_id, raw):
            R, OR = LedgerRollup, OverheadRollup
            rw = self._rollup_where(R, frm, to, user_id, pattern_id)
            ow_r = self._rollup_where(OR, frm, to, user_id, pattern_id)
            with db.read_session() as s:
                r = s.execute(select(
                    func.coalesce(func.sum(R.requests), 0),
                    *[func.coalesce(func.sum(getattr(R, c)), 0) for c in _SUMMARY_INTS[:6]],
                    *[func.coalesce(func.sum(getattr(R, c)), 0.0) for c in _SUMMARY_FLOATS],
                    *[func.coalesce(func.sum(getattr(R, c)), 0) for c in _SUMMARY_INTS[6:]],
                    func.coalesce(func.sum(R.heuristic_avoided_usd), 0.0),
                    func.coalesce(func.sum(R.uncited), 0), func.coalesce(func.sum(R.simulated), 0),
                ).where(*rw)).one()
                routes = dict(s.execute(select(R.route, func.sum(R.requests)).where(*rw).group_by(R.route)).all())
                counts = s.execute(select(*[func.coalesce(func.sum(getattr(R, f"n_{m}")), 0)
                                            for m in ESTIMATE_METHODS]).where(*rw)).one()
                methods: dict[Any, Any] = {m: int(n) for m, n in zip(ESTIMATE_METHODS, counts, strict=True) if n}
                o = s.execute(select(func.coalesce(func.sum(OR.entries), 0), func.coalesce(func.sum(OR.input_tokens), 0),
                                     func.coalesce(func.sum(OR.output_tokens), 0),
                                     func.coalesce(func.sum(OR.cost_usd), 0.0)).where(*ow_r)).one()
                ocat_rows = s.execute(select(OR.category, func.sum(OR.cost_usd),
                                             func.sum(OR.input_tokens + OR.output_tokens))
                                      .where(*ow_r).group_by(OR.category)).all()
            return self._summary_dict(tuple(r), routes, methods, tuple(o), ocat_rows)
        lw = self._ledger_where(frm, to, user_id, pattern_id, collection_id)
        ow = self._overhead_where(frm, to, user_id, pattern_id, collection_id)
        L = LedgerRow
        with db.read_session() as s:
            r = s.execute(select(
                func.count(),
                *[func.coalesce(func.sum(getattr(L, c)), 0) for c in _SUMMARY_INTS[:6]],
                *[func.coalesce(func.sum(getattr(L, c)), 0.0) for c in _SUMMARY_FLOATS],
                *[func.coalesce(func.sum(getattr(L, c)), 0) for c in _SUMMARY_INTS[6:]],
                func.coalesce(func.sum(case((L.estimate_method == "heuristic", L.cost_avoided_usd), else_=0.0)), 0.0),
                func.coalesce(func.sum(case((L.uncited.is_(True), 1), else_=0)), 0),
                func.coalesce(func.sum(case((L.simulated.is_(True), 1), else_=0)), 0),
            ).where(*lw)).one()
            routes = dict(s.execute(select(L.route, func.count()).where(*lw).group_by(L.route)).all())
            methods = dict(s.execute(select(L.estimate_method, func.count()).where(*lw, L.estimate_method.is_not(None))
                                     .group_by(L.estimate_method)).all())
            o = s.execute(select(func.count(), func.coalesce(func.sum(OverheadRow.input_tokens), 0),
                                 func.coalesce(func.sum(OverheadRow.output_tokens), 0),
                                 func.coalesce(func.sum(OverheadRow.cost_usd), 0.0)).where(*ow)).one()
            ocat_rows = s.execute(select(OverheadRow.category, func.sum(OverheadRow.cost_usd),
                                         func.sum(OverheadRow.input_tokens + OverheadRow.output_tokens))
                                  .where(*ow).group_by(OverheadRow.category)).all()
        return self._summary_dict(tuple(r), routes, methods, tuple(o), ocat_rows)

    @staticmethod
    def _summary_dict(r: tuple[Any, ...], routes: dict[Any, Any], methods: dict[Any, Any], o: tuple[Any, ...],
                      ocat_rows: Any) -> dict[str, Any]:
        ocat = {k: {"cost_usd": round(float(c), 8), "tokens": int(t)} for k, c, t in ocat_rows}
        (n, tin, tout, emb, ain, aout, actx, cact, cav, crun, cemb, qp, qc, qh, qs, crd, cwr, heur, uncited, sim) = r
        overhead_tokens = int(o[1]) + int(o[2])
        spent_requests = int(tin) + int(tout) + int(emb)
        spent = spent_requests + overhead_tokens
        avoided = int(ain) + int(aout)
        actual, gross, runtime, overhead = float(cact), float(cav), float(crun), float(o[3])
        embed = float(cemb)
        baseline = actual + gross
        # net of everything cheap routing itself costs: overhead, artifact runtime, router embeddings
        net = gross - overhead - runtime - embed
        return {
            "requests": int(n),
            "by_route": {k: int(v) for k, v in routes.items()},
            "input_tokens": int(tin), "output_tokens": int(tout), "embed_tokens": int(emb),
            "tokens_spent_requests": spent_requests,
            "overhead_tokens": overhead_tokens,
            "tokens_spent": spent,
            "tokens_avoided_in": int(ain), "tokens_avoided_out": int(aout), "tokens_avoided": avoided,
            "context_tokens_avoided": int(actx),
            "token_reduction": round(avoided / (spent + avoided), 6) if spent + avoided else 0.0,
            "actual_cost_usd": round(actual, 8),
            "runtime_cost_usd": round(runtime, 8),
            "embed_cost_usd": round(embed, 8),
            "gross_saved_usd": round(gross, 8),
            "overhead_cost_usd": round(overhead, 8),
            "overhead_entries": int(o[0]),
            "overhead_by_category": ocat,
            "baseline_cost_usd": round(baseline, 8),
            "net_saved_usd": round(net, 8),
            "savings_rate": round(net / baseline, 6) if baseline else 0.0,
            "estimated_share": round(float(heur) / gross, 6) if gross else 0.0,
            "estimate_methods": {str(k): int(v) for k, v in methods.items()},
            "input_mix": {"prompt": int(qp), "context": int(qc), "history": int(qh), "system": int(qs),
                          "cache_read": int(crd), "cache_write": int(cwr)},
            "uncited": int(uncited),
            "simulated_requests": int(sim),
        }

    def timeseries(self, bucket: str = "day", frm: float | None = None, to: float | None = None,
                   user_id: str | None = None, pattern_id: str | None = None, raw: bool = False) -> list[dict[str, Any]]:
        size = 3600 if bucket == "hour" else 86400
        if self._use_rollups(frm, to, None, raw):
            R, OR = LedgerRollup, OverheadRollup
            b = (R.bucket // size) * size                # integer division of whole-hour buckets
            ob = (OR.bucket // size) * size
            with db.read_session() as s:
                rows = s.execute(select(b.label("b"), func.sum(R.requests), func.sum(R.cost_actual_usd),
                                        func.sum(R.cost_avoided_usd), func.sum(R.runtime_cost_usd + R.embed_cost_usd),
                                        func.sum(R.input_tokens + R.output_tokens + R.embed_tokens),
                                        func.sum(R.tokens_avoided_in + R.tokens_avoided_out),
                                        *[func.sum(case((R.route == rt, R.requests), else_=0))
                                          for rt in ("cache", "artifact", "llm")])
                                 .where(*self._rollup_where(R, frm, to, user_id, pattern_id)).group_by(b).order_by(b)).all()
                orows = dict(s.execute(select(ob, func.sum(OR.cost_usd))
                                       .where(*self._rollup_where(OR, frm, to, user_id, pattern_id))
                                       .group_by(ob)).all())
        else:
            lw = self._ledger_where(frm, to, user_id, pattern_id, None)
            ow = self._overhead_where(frm, to, user_id, pattern_id, None)
            L = LedgerRow
            dialect = db.dialect_name()
            b = rollups.bucket_expr(L.ts, dialect, size)
            ob = rollups.bucket_expr(OverheadRow.ts, dialect, size)
            with db.read_session() as s:
                rows = s.execute(select(b.label("b"), func.count(), func.sum(L.cost_actual_usd), func.sum(L.cost_avoided_usd),
                                        func.sum(L.runtime_cost_usd + L.embed_cost_usd),
                                        func.sum(L.input_tokens + L.output_tokens + L.embed_tokens),
                                        func.sum(L.tokens_avoided_in + L.tokens_avoided_out),
                                        *[func.sum(case((L.route == rt, 1), else_=0)) for rt in ("cache", "artifact", "llm")])
                                 .where(*lw).group_by(b).order_by(b)).all()
                orows = dict(s.execute(select(ob, func.sum(OverheadRow.cost_usd)).where(*ow).group_by(ob)).all())
        orows = {int(k): v for k, v in orows.items()}
        out = []
        for bk, n, actual, avoided, runtime, spent, av_tok, c, a, llm in rows:
            overhead = float(orows.pop(int(bk), 0.0) or 0.0)
            out.append(self._bucket(int(bk), int(n), float(actual or 0), float(avoided or 0), float(runtime or 0),
                                    int(spent or 0), int(av_tok or 0), int(c or 0), int(a or 0), int(llm or 0), overhead))
        for bk, overhead in orows.items():  # buckets with overhead but no requests
            out.append(self._bucket(int(bk), 0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, float(overhead or 0)))
        return sorted(out, key=lambda x: x["bucket"])

    @staticmethod
    def _bucket(bk: int, n: int, actual: float, avoided: float, runtime: float, spent: int, av_tok: int, c: int,
                a: int, llm: int, overhead: float) -> dict[str, Any]:
        return {"bucket": bk, "requests": n, "actual_cost_usd": round(actual, 8),
                "baseline_cost_usd": round(actual + avoided, 8), "gross_saved_usd": round(avoided, 8),
                "overhead_cost_usd": round(overhead, 8), "net_saved_usd": round(avoided - overhead - runtime, 8),
                "tokens_spent": spent, "tokens_avoided": av_tok, "routes": {"cache": c, "artifact": a, "llm": llm}}

    def by_user(self, frm: float | None = None, to: float | None = None, raw: bool = False) -> list[dict[str, Any]]:
        if self._use_rollups(frm, to, None, raw):
            R, OR = LedgerRollup, OverheadRollup
            with db.read_session() as s:
                rows = s.execute(select(R.user_id, func.sum(R.requests),
                                        func.sum(R.input_tokens + R.output_tokens + R.embed_tokens),
                                        func.sum(R.tokens_avoided_in + R.tokens_avoided_out), func.sum(R.cost_actual_usd),
                                        func.sum(R.cost_avoided_usd), func.sum(R.runtime_cost_usd + R.embed_cost_usd),
                                        func.sum(case((R.route != "llm", R.requests), else_=0)))
                                 .where(*self._rollup_where(R, frm, to, None, None)).group_by(R.user_id)).all()
                ov: dict[Any, Any] = dict(s.execute(select(OR.user_id, func.sum(OR.cost_usd))
                                    .where(*self._rollup_where(OR, frm, to, None, None), OR.user_id != "")
                                    .group_by(OR.user_id)).all())
        else:
            L = LedgerRow
            lw = self._ledger_where(frm, to, None, None, None)
            with db.read_session() as s:
                rows = s.execute(select(L.user_id, func.count(), func.sum(L.input_tokens + L.output_tokens + L.embed_tokens),
                                        func.sum(L.tokens_avoided_in + L.tokens_avoided_out), func.sum(L.cost_actual_usd),
                                        func.sum(L.cost_avoided_usd), func.sum(L.runtime_cost_usd + L.embed_cost_usd),
                                        func.sum(case((L.route != "llm", 1), else_=0)))
                                 .where(*lw).group_by(L.user_id)).all()
                ov = dict(s.execute(select(OverheadRow.user_id, func.sum(OverheadRow.cost_usd))
                                    .where(*self._overhead_where(frm, to, None, None, None), OverheadRow.user_id.is_not(None))
                                    .group_by(OverheadRow.user_id)).all())
        out = []
        for uid, n, spent, avoided, actual, gross, runtime, cheap in rows:
            overhead = float(ov.get(uid, 0.0) or 0.0)
            out.append({"user_id": uid, "requests": int(n), "tokens_spent": int(spent or 0),
                        "tokens_avoided": int(avoided or 0), "actual_cost_usd": round(float(actual or 0), 8),
                        "gross_saved_usd": round(float(gross or 0), 8), "overhead_cost_usd": round(overhead, 8),
                        "net_saved_usd": round(float(gross or 0) - float(runtime or 0) - overhead, 8),
                        "served_without_llm": int(cheap or 0)})
        return sorted(out, key=lambda r: (-r["requests"], r["user_id"]))

    def by_pattern(self, frm: float | None = None, to: float | None = None, raw: bool = False) -> list[dict[str, Any]]:
        """Per-pattern savings with payback: cumulative avoided cost vs that pattern's overhead. Totals come from
        the rollups; the payback point (the request at which savings overtook overhead) needs the ordered rows."""
        L = LedgerRow
        lw = self._ledger_where(frm, to, None, None, None)
        rows: Any
        ov_rows: Any
        with db.read_session() as s:
            if self._use_rollups(frm, to, None, raw):
                R, OR = LedgerRollup, OverheadRollup
                rows = s.execute(select(R.pattern_id, func.sum(R.requests), func.sum(R.tokens_avoided_in + R.tokens_avoided_out),
                                        func.sum(R.cost_avoided_usd), func.sum(R.runtime_cost_usd + R.embed_cost_usd))
                                 .where(*self._rollup_where(R, frm, to, None, None), R.pattern_id != "")
                                 .group_by(R.pattern_id)).all()
                ov_rows = s.execute(select(OR.pattern_id, func.sum(OR.cost_usd), func.sum(OR.input_tokens + OR.output_tokens))
                                    .where(*self._rollup_where(OR, frm, to, None, None), OR.pattern_id != "")
                                    .group_by(OR.pattern_id)).all()
            else:
                ow = self._overhead_where(frm, to, None, None, None)
                rows = s.execute(select(L.pattern_id, func.count(), func.sum(L.tokens_avoided_in + L.tokens_avoided_out),
                                        func.sum(L.cost_avoided_usd), func.sum(L.runtime_cost_usd + L.embed_cost_usd))
                                 .where(*lw, L.pattern_id.is_not(None)).group_by(L.pattern_id)).all()
                ov_rows = s.execute(select(OverheadRow.pattern_id, func.sum(OverheadRow.cost_usd),
                                           func.sum(OverheadRow.input_tokens + OverheadRow.output_tokens))
                                    .where(*ow, OverheadRow.pattern_id.is_not(None)).group_by(OverheadRow.pattern_id)).all()
            ov = {pid: (float(c or 0), int(t or 0)) for pid, c, t in ov_rows}
            seen = set()
            out = []
            for pid, n, avoided, gross, runtime in rows:
                seen.add(pid)
                overhead, otok = ov.get(pid, (0.0, 0))
                payback = self._payback(s, str(pid), overhead, lw)
                out.append({"pattern_id": pid, "requests": int(n), "tokens_avoided": int(avoided or 0),
                            "gross_saved_usd": round(float(gross or 0), 8), "runtime_cost_usd": round(float(runtime or 0), 8),
                            "overhead_cost_usd": round(overhead, 8), "overhead_tokens": otok,
                            "net_saved_usd": round(float(gross or 0) - float(runtime or 0) - overhead, 8),
                            "paid_back": payback is not None, "paid_back_after_requests": payback})
            for pid, (overhead, otok) in ov.items():
                if pid not in seen:
                    out.append({"pattern_id": pid, "requests": 0, "tokens_avoided": 0, "gross_saved_usd": 0.0,
                                "runtime_cost_usd": 0.0, "overhead_cost_usd": round(overhead, 8),
                                "overhead_tokens": otok, "net_saved_usd": round(-overhead, 8), "paid_back": False,
                                "paid_back_after_requests": None})
        return sorted(out, key=lambda r: (-r["net_saved_usd"], str(r["pattern_id"])))

    @staticmethod
    def _payback(s: Any, pattern_id: str, overhead: float, lw: list[ColumnElement[bool]]) -> int | None:
        if overhead <= 0:
            return 0
        cum = 0.0
        q = (select(LedgerRow.cost_avoided_usd - LedgerRow.runtime_cost_usd - LedgerRow.embed_cost_usd)
             .where(*lw, LedgerRow.pattern_id == pattern_id).order_by(LedgerRow.ts))
        for i, (v,) in enumerate(s.execute(q), start=1):
            cum += float(v or 0)
            if cum >= overhead:
                return i
        return None

    def by_collection(self, frm: float | None = None, to: float | None = None) -> list[dict[str, Any]]:
        """Per knowledge collection. A request with several collections counts toward each."""
        L = LedgerRow
        lw = self._ledger_where(frm, to, None, None, None)
        with db.session() as s:
            rows = s.execute(select(L.collection_ids, L.route, L.context_tokens, L.context_avoided_tokens,
                                    L.cost_actual_usd, L.cost_avoided_usd, L.input_tokens, L.output_tokens)
                             .where(*lw)).all()
            ov = dict(s.execute(select(OverheadRow.collection_id, func.sum(OverheadRow.cost_usd))
                                .where(*self._overhead_where(frm, to, None, None, None),
                                       OverheadRow.collection_id.is_not(None))
                                .group_by(OverheadRow.collection_id)).all())
        agg: dict[str, dict[str, Any]] = {}
        for cols, route, ctx, ctx_av, actual, avoided, tin, tout in rows:
            for c in cols or []:
                c = str(c)
                a = agg.setdefault(c, {"collection_id": c, "requests": 0, "cache_hits": 0, "context_tokens": 0,
                                       "context_tokens_avoided": 0, "tokens_spent": 0, "actual_cost_usd": 0.0,
                                       "gross_saved_usd": 0.0})
                a["requests"] += 1
                a["cache_hits"] += int(route == "cache")
                a["context_tokens"] += int(ctx or 0)
                a["context_tokens_avoided"] += int(ctx_av or 0)
                a["tokens_spent"] += int(tin or 0) + int(tout or 0)
                a["actual_cost_usd"] += float(actual or 0)
                a["gross_saved_usd"] += float(avoided or 0)
        for oc in ov:
            c = str(oc)
            agg.setdefault(c, {"collection_id": c, "requests": 0, "cache_hits": 0, "context_tokens": 0,
                               "context_tokens_avoided": 0, "tokens_spent": 0, "actual_cost_usd": 0.0,
                               "gross_saved_usd": 0.0})
        for c, a in agg.items():
            a["overhead_cost_usd"] = round(float(ov.get(c, 0.0) or 0.0), 8)
            a["actual_cost_usd"] = round(a["actual_cost_usd"], 8)
            a["gross_saved_usd"] = round(a["gross_saved_usd"], 8)
            a["net_saved_usd"] = round(a["gross_saved_usd"] - a["overhead_cost_usd"], 8)
        return sorted(agg.values(), key=lambda r: -r["requests"])

    def artifact_hits(self) -> dict[str, int]:
        """Artifact invocations per pattern (all time), from the rollups."""
        R = LedgerRollup
        with db.read_session() as s:
            return {str(pid): int(n) for pid, n in s.execute(
                select(R.pattern_id, func.sum(R.requests)).where(R.route == "artifact", R.pattern_id != "")
                .group_by(R.pattern_id)).all()}

    def entries(self, limit: int = 100, user_id: str | None = None) -> list[dict[str, Any]]:
        with db.session() as s:
            q = select(LedgerRow).order_by(LedgerRow.ts.desc()).limit(limit)
            if user_id:
                q = q.where(LedgerRow.user_id == user_id)
            return [{c.name: getattr(r, c.name) for c in LedgerRow.__table__.columns} for r in s.scalars(q)]


_ledger = Ledger()


def ledger() -> Ledger:
    return _ledger


__all__ = ["Ledger", "LedgerEntry", "OverheadEntry", "ledger"]
