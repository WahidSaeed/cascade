"""ArtifactRuntime: where compiled artifacts execute (BUILD.md §4.3, rule 2).

  subprocess  DEV ONLY. One persistent worker process per artifact version, running
              app/artifact_runner.py with `python -I`, a scrubbed environment (no AWS credentials)
              and a timeout. A convenience, not a security boundary.
  lambda      PRODUCTION. One Lambda per approved artifact (`cascade-artifact-<pattern_id>`), deployed by
              app/deploy.py; least-privilege role, and (Phase 7) no network access.

Generated code never runs in the API process. All kinds share one contract:
{"prompt": str} -> {"result": str, "confidence": float}.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .schemas import ArtifactKind, Pattern
from .state import on_reset

RUNNER = Path(__file__).with_name("artifact_runner.py")
FALLBACK = "__FALLBACK__"


@dataclass
class ArtifactResult:
    result: str | None
    confidence: float
    latency_ms: float
    error: str | None = None

    @property
    def usable(self) -> bool:
        return (self.error is None and self.result is not None and self.result.strip() != FALLBACK
                and self.confidence >= settings.artifact_min_confidence)


def artifact_dir(pattern: Pattern) -> Path:
    return Path(settings.data_dir) / "artifacts" / pattern.id / f"v{pattern.version}"


def materialize(pattern: Pattern) -> Path:
    """Write the artifact's files (manifest + code or model) to its versioned directory."""
    d = artifact_dir(pattern)
    d.mkdir(parents=True, exist_ok=True)
    kind = (pattern.kind or ArtifactKind.rule).value
    if kind == "rule":
        (d / "handler.py").write_text(pattern.handler_code)
    else:
        src = Path(pattern.model_uri)
        if not src.exists():
            raise FileNotFoundError(f"model files missing: {pattern.model_uri}")
        if src.resolve() != d.resolve():
            for f in src.iterdir():
                if f.is_file():
                    shutil.copy2(f, d / f.name)
    (d / "manifest.json").write_text(json.dumps({"kind": kind, "pattern_id": pattern.id, "version": pattern.version}))
    return d


class ArtifactRuntime(ABC):
    @abstractmethod
    def invoke(self, pattern: Pattern, prompt: str) -> ArtifactResult: ...

    def stop(self, pattern_id: str) -> None:  # noqa: B027 - optional hook
        """Release resources held for a pattern (on teardown)."""

    def shutdown(self) -> None:  # noqa: B027
        pass


# ============================================================================ subprocess (dev)
def _scrubbed_env() -> dict[str, str]:
    keep = {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR", "SYSTEMROOT"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"})
    return env


class _Worker:
    def __init__(self, path: Path) -> None:
        self.lock = threading.Lock()
        self.cwd = tempfile.mkdtemp(prefix="cascade-artifact-")
        self.proc = subprocess.Popen([sys.executable, "-I", str(RUNNER), str(path)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     cwd=self.cwd, env=_scrubbed_env())
        ready = self._readline(timeout=60.0)
        if ready is None or "ready" not in ready:
            self.kill()
            raise RuntimeError(f"artifact failed to load: {ready}")

    def _readline(self, timeout: float) -> dict[str, Any] | None:
        assert self.proc.stdout is not None
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            return None
        line = self.proc.stdout.readline()
        if not line:
            return None
        parsed: dict[str, Any] = json.loads(line)
        return parsed

    def call(self, prompt: str, timeout: float) -> dict[str, Any] | None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"prompt": prompt}) + "\n")
        self.proc.stdin.flush()
        return self._readline(timeout)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001,S110
            pass
        shutil.rmtree(self.cwd, ignore_errors=True)


class SubprocessRuntime(ArtifactRuntime):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[tuple[str, int], _Worker] = {}

    def _worker(self, pattern: Pattern) -> _Worker:
        key = (pattern.id, pattern.version)
        with self._lock:
            w = self._workers.get(key)
            if w is not None and w.alive():
                return w
            for (pid, ver), old in list(self._workers.items()):     # an older version is retired
                if pid == pattern.id and ver != pattern.version:
                    old.kill()
                    del self._workers[(pid, ver)]
            path = artifact_dir(pattern)
            if not (path / "manifest.json").exists():
                materialize(pattern)
            w = _Worker(path)
            self._workers[key] = w
            return w

    def invoke(self, pattern: Pattern, prompt: str) -> ArtifactResult:
        t0 = time.perf_counter()
        try:
            w = self._worker(pattern)
            with w.lock:
                out = w.call(prompt, settings.artifact_timeout_s)
            if out is None:                                       # timeout or crash: restart next time
                self.stop(pattern.id)
                return ArtifactResult(None, 0.0, (time.perf_counter() - t0) * 1000, "artifact timed out")
        except Exception as exc:  # noqa: BLE001 - the user never sees an artifact error
            return ArtifactResult(None, 0.0, (time.perf_counter() - t0) * 1000, repr(exc)[:300])
        latency = (time.perf_counter() - t0) * 1000
        if "error" in out:
            return ArtifactResult(None, 0.0, latency, str(out["error"]))
        return ArtifactResult(str(out.get("result")), float(out.get("confidence", 0.0)), latency)

    def stop(self, pattern_id: str) -> None:
        with self._lock:
            for key in [k for k in self._workers if k[0] == pattern_id]:
                self._workers.pop(key).kill()

    def shutdown(self) -> None:
        with self._lock:
            for w in self._workers.values():
                w.kill()
            self._workers.clear()


# ============================================================================ lambda (prod)
class LambdaRuntime(ArtifactRuntime):
    def __init__(self, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("lambda", region_name=settings.bedrock_region)
        self._client = client

    def invoke(self, pattern: Pattern, prompt: str) -> ArtifactResult:
        return self.invoke_function(pattern.deployed_function or f"{settings.lambda_prefix}{pattern.id}", prompt)

    def invoke_function(self, fn: str, prompt: str) -> ArtifactResult:
        t0 = time.perf_counter()
        try:
            resp = self._client.invoke(FunctionName=fn, InvocationType="RequestResponse",
                                       Payload=json.dumps({"prompt": prompt}).encode())
            payload = json.loads(resp["Payload"].read())
        except Exception as exc:  # noqa: BLE001
            return ArtifactResult(None, 0.0, (time.perf_counter() - t0) * 1000, repr(exc)[:300])
        latency = (time.perf_counter() - t0) * 1000
        if resp.get("FunctionError") or "error" in payload:
            return ArtifactResult(None, 0.0, latency, str(payload.get("error", payload))[:300])
        return ArtifactResult(str(payload.get("result")), float(payload.get("confidence", 0.0)), latency)


def warm(patterns: list[Pattern]) -> None:
    """Start the workers of approved artifacts ahead of traffic (the local stand-in for provisioned
    concurrency), so the first matching request isn't a cold start."""
    rt = runtime()
    if not isinstance(rt, SubprocessRuntime):
        return
    for p in patterns:
        try:
            rt._worker(p)
        except Exception:  # noqa: BLE001,S112 - a broken artifact is reported when it is invoked
            continue


_lock = threading.Lock()
_runtimes: dict[str, ArtifactRuntime] = {}


def runtime() -> ArtifactRuntime:
    name = settings.artifact_runtime
    with _lock:
        if name not in _runtimes:
            _runtimes[name] = LambdaRuntime() if name == "lambda" else SubprocessRuntime()
        return _runtimes[name]


def set_runtime(name: str, rt: ArtifactRuntime | None) -> None:
    with _lock:
        old = _runtimes.pop(name, None)
        if old is not None and old is not rt:
            old.shutdown()
        if rt is not None:
            _runtimes[name] = rt


def _reset() -> None:
    with _lock:
        for rt in _runtimes.values():
            rt.shutdown()
        _runtimes.clear()


on_reset(_reset)
