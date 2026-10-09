"""Fast-path execution of deterministic handlers.

Two runtimes:
- 'subprocess'  : run the handler in a separate Python process with a timeout. DEV ONLY.
                  This is NOT a security sandbox — it exists so you can try the flow offline.
- 'lambda'      : invoke one AWS Lambda per approved handler. PRODUCTION.
                  Each handler is its own function with a locked-down role and no network,
                  so untrusted generated code can never touch the API process or your data.

A handler is Python source that defines:  def handle(prompt: str) -> str
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from .config import settings


class HandlerError(RuntimeError):
    pass


_RUNNER = textwrap.dedent(
    """
    import json, sys
    payload = json.loads(sys.stdin.read())
    ns = {}
    exec(payload["code"], ns)
    if "handle" not in ns:
        print(json.dumps({"error": "handler defines no handle(prompt)"})); sys.exit(0)
    try:
        out = ns["handle"](payload["prompt"])
        print(json.dumps({"ok": str(out)}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": repr(e)}))
    """
)


def _run_subprocess(code: str, prompt: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=True) as f:
        f.write(_RUNNER)
        f.flush()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", f.name],
                input=json.dumps({"code": code, "prompt": prompt}),
                capture_output=True,
                text=True,
                timeout=settings.handler_timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise HandlerError("handler timed out") from e
    if proc.returncode != 0:
        raise HandlerError(f"handler process failed: {proc.stderr[:400]}")
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        raise HandlerError(f"handler produced no valid output: {proc.stdout[:400]}") from e
    if "error" in result:
        raise HandlerError(result["error"])
    return result["ok"]


def _run_lambda(pattern_id: str, prompt: str) -> str:
    import boto3

    client = boto3.client("lambda", region_name=settings.bedrock_region)
    fn = f"{settings.lambda_name_prefix}{pattern_id}"
    resp = client.invoke(
        FunctionName=fn,
        InvocationType="RequestResponse",
        Payload=json.dumps({"prompt": prompt}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        raise HandlerError(f"lambda error: {payload}")
    return str(payload.get("result", payload))


def run_handler(pattern_id: str, code: str, prompt: str) -> str:
    if settings.handler_runtime == "lambda":
        return _run_lambda(pattern_id, prompt)
    return _run_subprocess(code, prompt)
