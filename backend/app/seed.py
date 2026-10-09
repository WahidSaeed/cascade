"""Seed a demo pattern + sample traffic so the workspace isn't empty on first run.

Run:  python -m app.seed
"""
from __future__ import annotations

from .engine import route
from .patterns import compile_handler
from .store import store

POSITIVE = ["I love this product", "this is great", "absolutely amazing experience",
            "so happy with it", "excellent service"]
NEGATIVE = ["I hate this", "terrible experience", "the worst", "really bad quality",
            "awful and broken"]


def main() -> None:
    st = store()

    # 1) compile a draft sentiment handler from example traffic
    examples = [f"What is the sentiment of: {t}" for t in POSITIVE + NEGATIVE]
    pat = compile_handler(
        name="Sentiment classifier",
        description="Classify the sentiment of a short message as positive or negative.",
        examples=examples,
    )
    st.put_pattern(pat)
    print(f"seeded draft pattern {pat.id} ({pat.name})")

    # 2) generate some traffic so metrics have data
    for t in POSITIVE + NEGATIVE + ["summarize the news", "write me a poem"]:
        route(f"What is the sentiment of: {t}")

    print("seeded sample traffic. Start the API and open the workspace to verify & approve.")


if __name__ == "__main__":
    main()
