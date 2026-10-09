"""Download the local models into CASCADE_MODELS_DIR (make models; baked into Docker images)."""
from __future__ import annotations

from .config import settings


def main() -> None:
    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    TextEmbedding(settings.embed_model, cache_dir=settings.models_dir)
    TextCrossEncoder(settings.reranker_model, cache_dir=settings.models_dir)
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(settings.dl_base_model, cache_dir=settings.models_dir + "/hf")
    except Exception as exc:  # noqa: BLE001 - optional for environments without the dl group
        print(f"dl base model not downloaded: {exc}")
    print(f"models ready in {settings.models_dir}")


if __name__ == "__main__":
    main()
