# CoDiscover Video contributor guide

- This is a local single-user Agent project, not a public hosted service. `serve` binds loopback.
- Preserve the upstream commit/license notices when changing `src/codiscover_video/vendor` or DSH helpers.
- Platform capabilities are read-only. Do not add comments, direct messages, follows or favorites to the agent tool surface.
- Keep demo fixtures visibly synthetic. Do not present hash-vector retrieval as semantic embeddings, or model confidence as user-satisfaction probability.
- User intent, explicit feedback, raw evidence and model inference are separate concepts. Keep source/timestamp provenance and negative feedback isolation by goal.
- Keep changes within the existing module boundaries. `docs/ARCHITECTURE.md` explains task checkpoints, budget and concurrency rules.
- Never commit `.env`, `API调用指南.md`, credentials or `data/` runtime content. Live model tests are opt-in and billable; ordinary test runs use mocks or demo providers.
- Baseline checks: `make lint`, `uv run pytest -q`, `node --test integrations/dsh/tests/*.test.mjs`. Browser tests require the documented demo server and Chromium.
- A cancelled job may still be draining a network call. Do not allow resumption or data deletion until that execution has exited.
- The supported server platforms are macOS/Linux; the worker ownership lock uses POSIX `fcntl`.
