"""Shared fixtures + the acceptance-matrix reporter (BUILD.md §14.3).

Every test that carries `@pytest.mark.acceptance("P1-04")` is written to
reports/acceptance/<suite>.json with its outcome, for scripts/acceptance_matrix.py.
Tests marked `aws` are recorded as `aws` even though `make` deselects them.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "acceptance"

os.environ.setdefault("CASCADE_LLM", "demo")
os.environ.setdefault("CASCADE_DEMO_LATENCY_MS", "0")
os.environ.setdefault("CASCADE_ENV", "test")
os.environ.setdefault("CASCADE_MODELS_DIR", str(ROOT / ".models"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
for _k, _v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
               "AWS_DEFAULT_REGION": "us-east-1", "AWS_CONFIG_FILE": "/dev/null",
               "AWS_SHARED_CREDENTIALS_FILE": "/dev/null"}.items():
    os.environ.setdefault(_k, _v)

_rows: dict[str, dict[str, Any]] = {}


def _ids(item: pytest.Item) -> list[str]:
    return [m.args[0] for m in item.iter_markers("acceptance") if m.args]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        ids = _ids(item)
        if ids and item.get_closest_marker("aws"):
            _rows[item.nodeid] = {"nodeid": item.nodeid, "ids": ids, "outcome": "aws"}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    ids = getattr(report, "acceptance_ids", None)
    if not ids:
        return
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        if hasattr(report, "wasxfail"):
            # §14.2: xfail is allowed only for behaviour that needs real AWS (reason says "aws-only").
            outcome = "xfailed-aws" if "aws-only" in str(report.wasxfail).lower() else "failed"
        else:
            outcome = report.outcome
        prev = _rows.get(report.nodeid)
        if prev and prev["outcome"] == "failed":
            return
        _rows[report.nodeid] = {"nodeid": report.nodeid, "ids": ids, "outcome": outcome}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any) -> Iterator[None]:
    outcome = yield
    rep = outcome.get_result()
    rep.acceptance_ids = _ids(item)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _rows:
        return
    REPORTS.mkdir(parents=True, exist_ok=True)
    args = [a for a in session.config.args if not a.startswith("-")]
    suite = Path(args[0]).name if args else "tests"
    out = REPORTS / f"{suite}-{os.getpid()}.json"
    out.write_text(json.dumps(list(_rows.values()), indent=1))


# --------------------------------------------------------------------------- database fixtures
_template_db: Path | None = None


def _sqlite_template(tmp_root: Path) -> Path:
    global _template_db
    if _template_db is None:
        from app import db

        path = tmp_root / "template.db"
        db.migrate(f"sqlite:///{path}")
        _template_db = path
    return _template_db


@pytest.fixture
def fresh_db(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A freshly migrated database for one test: SQLite by default, Postgres in tests/integration."""
    from app import db
    from app.config import settings

    pg = os.environ.get("CASCADE_TEST_DATABASE_URL")
    if pg and os.environ.get("CASCADE_TEST_USE_PG", "1") == "1" and _is_integration():
        url = _pg_fresh_database(pg)
    else:
        root = tmp_path_factory.getbasetemp()
        tmpl = _sqlite_template(root)
        target = root / f"db-{uuid.uuid4().hex[:8]}.db"
        shutil.copy(tmpl, target)
        url = f"sqlite:///{target}"
    monkeypatch.setattr(settings, "database_url", url)
    db.reset_engine()
    from app import state

    state.reset_runtime_state()
    yield url
    db.reset_engine()
    state.reset_runtime_state()


_integration = {"on": False}


def _is_integration() -> bool:
    return _integration["on"]


_pg_template = {"ready": False}


def _pg_fresh_database(base_url: str) -> str:
    """CREATE DATABASE … TEMPLATE a migrated template: a fresh schema per test in ~100 ms."""
    from sqlalchemy import create_engine, text

    from app import db

    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not _pg_template["ready"]:
            stale = c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'cascade_t_%'"))
            for (old,) in list(stale):
                c.execute(text(f'DROP DATABASE IF EXISTS "{old}" WITH (FORCE)'))
            c.execute(text("DROP DATABASE IF EXISTS cascade_test_tmpl WITH (FORCE)"))
            c.execute(text("CREATE DATABASE cascade_test_tmpl"))
            tmpl_url = base_url.rsplit("/", 1)[0] + "/cascade_test_tmpl"
            db.migrate(tmpl_url)
            _pg_template["ready"] = True
        name = f"cascade_t_{uuid.uuid4().hex[:10]}"
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE cascade_test_tmpl'))
    admin.dispose()
    return base_url.rsplit("/", 1)[0] + f"/{name}"


def pytest_configure(config: pytest.Config) -> None:
    args = " ".join(str(a) for a in config.args)
    _integration["on"] = "integration" in args


@pytest.fixture
def client(fresh_db: str) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
