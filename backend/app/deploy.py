"""Deploy-on-approve (BUILD.md §7.6, rule 3).

  deploy(pattern)      materializes the artifact; with CASCADE_ARTIFACT_RUNTIME=lambda it creates or
                       updates `cascade-artifact-<pattern_id>`:
                         rule   -> zip (lambda_function.lambda_handler runs handle(prompt)), python3.12,
                                   5 s, 256 MB, role CASCADE_ARTIFACT_ROLE_ARN
                         ml/dl  -> container image (onnxruntime runtime image) loading its model files
                       and writes the artifact to the versioned S3 bucket.
  teardown(pattern_id) deletes the function (ignores NotFound) and stops local workers.

A pattern is marked `approved` only if deploy succeeds (callers enforce this).
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from .config import settings
from .runtime import RUNNER, materialize, runtime
from .schemas import ArtifactKind, Pattern

LAMBDA_TIMEOUT_S = 5
LAMBDA_MEMORY_MB = 256


class DeployError(RuntimeError):
    pass


def function_name(pattern_id: str) -> str:
    return f"{settings.lambda_prefix}{pattern_id}"


def build_zip(artifact_path: Path) -> bytes:
    """Lambda zip: the runner as lambda_function.py plus the artifact's files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("lambda_function.py", RUNNER.read_text())
        for f in sorted(artifact_path.iterdir()):
            if f.is_file():
                z.write(f, f.name)
    return buf.getvalue()


def _lambda_client() -> Any:
    import boto3

    return boto3.client("lambda", region_name=settings.bedrock_region)


def _s3_client() -> Any:
    import boto3

    return boto3.client("s3", region_name=settings.bedrock_region)


def _upload_to_s3(pattern: Pattern, path: Path, s3: Any) -> str | None:
    if not settings.artifact_bucket:
        return None
    prefix = f"artifacts/{pattern.id}/v{pattern.version}/"
    for f in sorted(path.iterdir()):
        if f.is_file():
            s3.put_object(Bucket=settings.artifact_bucket, Key=prefix + f.name, Body=f.read_bytes())
    return f"s3://{settings.artifact_bucket}/{prefix}"


def vpc_config(pattern: Pattern) -> dict[str, Any]:
    """Phase 7 (P7-03): every artifact Lambda runs in the private subnets, which have no NAT route — no outbound
    network at all, only the S3 gateway endpoint for its model. Plain artifacts get the no-egress artifact
    security group; artifacts with an approved integration get the integration group, whose only egress rules
    are the declared targets (rule 11), reached inside the VPC (PrivateLink / peering), never via a NAT."""
    if pattern.integration_ids:
        if not (settings.artifact_subnet_ids and settings.artifact_integration_sg):
            raise DeployError("artifacts with integrations need CASCADE_ARTIFACT_SUBNET_IDS and "
                              "CASCADE_ARTIFACT_INTEGRATION_SG (egress-restricted)")
        sg = settings.artifact_integration_sg
    elif settings.artifact_subnet_ids:
        if not settings.artifact_sg:      # fail closed: never deploy outside the VPC by omission
            raise DeployError("CASCADE_ARTIFACT_SUBNET_IDS is set but CASCADE_ARTIFACT_SG is not")
        sg = settings.artifact_sg
    else:
        return {}                         # local / moto: no VPC configured
    return {"VpcConfig": {"SubnetIds": settings.artifact_subnet_ids.split(","), "SecurityGroupIds": [sg]}}


def _wait(client: Any, waiter: str, name: str) -> None:
    """Lambda rejects a second update while one is in progress, and a function with a VpcConfig stays Pending
    while its network interfaces are created: wait until it is ready (up to ~5 minutes)."""
    if hasattr(client, "get_waiter"):
        client.get_waiter(waiter).wait(FunctionName=name, WaiterConfig={"Delay": 2, "MaxAttempts": 150})


def deploy(pattern: Pattern, client: Any = None, s3: Any = None) -> str:
    path = materialize(pattern)
    if settings.artifact_runtime != "lambda":
        runtime().stop(pattern.id)       # next call starts a worker on the new version
        return f"local:{path}"
    client = client or _lambda_client()
    if settings.artifact_bucket:
        _upload_to_s3(pattern, path, s3 or _s3_client())
    if not settings.artifact_role_arn:
        raise DeployError("CASCADE_ARTIFACT_ROLE_ARN is not set")
    name = function_name(pattern.id)
    kind = pattern.kind or ArtifactKind.rule
    vpc = vpc_config(pattern)
    try:
        exists = True
        client.get_function(FunctionName=name)
    except client.exceptions.ResourceNotFoundException:
        exists = False
    try:
        if kind == ArtifactKind.rule:
            code = build_zip(path)
            if exists:
                if vpc:                   # configuration first, so the published version carries it
                    client.update_function_configuration(FunctionName=name, **vpc)
                    _wait(client, "function_updated_v2", name)
                client.update_function_code(FunctionName=name, ZipFile=code, Publish=True)
                _wait(client, "function_updated_v2", name)
            else:
                client.create_function(FunctionName=name, Runtime="python3.12", Role=settings.artifact_role_arn,
                                       Handler="lambda_function.lambda_handler", Code={"ZipFile": code},
                                       Timeout=LAMBDA_TIMEOUT_S, MemorySize=LAMBDA_MEMORY_MB, Publish=True,
                                       Tags={"cascade:pattern": pattern.id, "cascade:version": str(pattern.version)},
                                       **vpc)
                _wait(client, "function_active_v2", name)
        else:
            image = settings.ml_runtime_image if kind == ArtifactKind.ml else settings.dl_runtime_image
            if not image:
                raise DeployError(f"no runtime image configured for {kind.value} artifacts")
            env = {"Variables": {"MODEL_URI": f"s3://{settings.artifact_bucket}/artifacts/{pattern.id}/v{pattern.version}/"}}
            if exists:
                client.update_function_configuration(FunctionName=name, Environment=env, **vpc)
                _wait(client, "function_updated_v2", name)
                client.update_function_code(FunctionName=name, ImageUri=image, Publish=True)
                _wait(client, "function_updated_v2", name)
            else:
                client.create_function(FunctionName=name, PackageType="Image", Code={"ImageUri": image},
                                       Role=settings.artifact_role_arn, Timeout=LAMBDA_TIMEOUT_S * 2,
                                       MemorySize=1024 if kind == ArtifactKind.dl else 512, Environment=env,
                                       Publish=True, Tags={"cascade:pattern": pattern.id}, **vpc)
                _wait(client, "function_active_v2", name)
    except DeployError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DeployError(f"lambda deploy failed: {exc}") from exc
    return name


def teardown(pattern_id: str, client: Any = None) -> None:
    runtime().stop(pattern_id)
    if settings.artifact_runtime != "lambda":
        return
    client = client or _lambda_client()
    try:
        client.delete_function(FunctionName=function_name(pattern_id))
    except client.exceptions.ResourceNotFoundException:
        pass
