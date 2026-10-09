"""Heavy model (System 2) client. 'mock' works offline; 'bedrock' calls Amazon Bedrock."""
from __future__ import annotations

import json

from .config import settings


class HeavyModel:
    def generate(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError


class MockHeavyModel(HeavyModel):
    """Deterministic canned responses so the full routing flow runs with no credentials."""

    def generate(self, prompt: str, system: str | None = None) -> str:
        p = prompt.strip().lower()
        # a couple of structured cases so the demo + verification have signal
        if "sentiment" in p:
            return "positive" if any(w in p for w in ("love", "great", "good", "happy")) else "negative"
        if p.startswith(("classify", "categorize")):
            return "general"
        return (
            "[mock heavy model] This is a stand-in response. Set CASCADE_HEAVY_BACKEND=bedrock "
            "and configure AWS credentials to get real reasoning-model answers."
        )


class BedrockHeavyModel(HeavyModel):
    def __init__(self) -> None:
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)

    def generate(self, prompt: str, system: str | None = None) -> str:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        resp = self._client.invoke_model(
            modelId=settings.bedrock_model_id, body=json.dumps(body)
        )
        payload = json.loads(resp["body"].read())
        return payload["content"][0]["text"]


_heavy: HeavyModel | None = None


def heavy_model() -> HeavyModel:
    global _heavy
    if _heavy is None:
        _heavy = BedrockHeavyModel() if settings.heavy_backend == "bedrock" else MockHeavyModel()
    return _heavy
