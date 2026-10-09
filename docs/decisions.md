# Decisions

One line per decision taken during the build where BUILD.md left a choice open:
date — decision — reason.

- 2026-10-09 — Terraform 1.9.8 installed as a binary in ~/.local/bin (the Makefile adds it to PATH) — Homebrew could not install it (outdated Xcode CLT); only `fmt`/`validate` are used.
- 2026-10-09 — Local models (bge-small, MiniLM cross-encoder, bert-tiny) are cached in `.models/` at the repo root and downloaded by `make models` — no download at test time; the Docker image bakes them in.
- 2026-10-09 — The acceptance gate covers the phases built so far (`scripts/acceptance_gate.txt`); later phases' IDs are reported as `missing` but only fail the gate once their phase begins — §16.3 requires Step 0's `make verify` to run before any feature exists.
- 2026-10-09 — Postgres for tests runs on host port 5433 — avoids clashing with a local Postgres on 5432.
