"""Security tests (BUILD.md §14.1): generated code never runs in the API process (rule 2), and
nothing in the build can reach a real AWS account (§14.2)."""
import ast
import os
from pathlib import Path

import boto3

from app.runtime import SubprocessRuntime
from app.schemas import ArtifactKind, Pattern

APP = Path(__file__).resolve().parents[2] / "app"
# The runner executes artifacts and is only ever started as a separate process (or in Lambda).
ALLOWED_EXEC = {"artifact_runner.py"}


def test_no_exec_or_eval_in_api_code():
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name in ALLOWED_EXEC:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []


def test_runner_is_never_imported_by_the_api():
    for path in APP.rglob("*.py"):
        if path.name == "artifact_runner.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "artifact_runner" in node.module:
                raise AssertionError(f"{path} imports the artifact runner")
            if isinstance(node, ast.Import) and any("artifact_runner" in a.name for a in node.names):
                raise AssertionError(f"{path} imports the artifact runner")


def test_generated_code_runs_out_of_process_without_credentials(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    code = ("import os\n"
            "def handle(prompt):\n"
            "    return '%d|%s' % (os.getpid(), os.environ.get('AWS_SECRET_ACCESS_KEY', '-'))\n")
    rt = SubprocessRuntime()
    try:
        res = rt.invoke(Pattern(name="probe", kind=ArtifactKind.rule, handler_code=code), "x")
    finally:
        rt.shutdown()
    pid, secret = res.result.split("|")
    assert int(pid) != os.getpid() and secret == "-"


def test_build_environment_cannot_reach_real_aws():
    creds = boto3.Session().get_credentials()
    assert creds is None or creds.access_key == "testing"
    assert os.environ.get("AWS_CONFIG_FILE") == "/dev/null"
    assert os.environ.get("AWS_SHARED_CREDENTIALS_FILE") == "/dev/null"
