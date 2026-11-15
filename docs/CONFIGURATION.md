# Configuration

Run commands from the project root. Only `.env` in the current working directory is loaded; parent-directory files are not searched. Explicit shell variables override that file. The published repository contains `.env.example` with an empty key, never a working `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CDV_PROVIDER` | `demo` | `demo` (synthetic fixtures) or `bilibili` (read-only platform requests) |
| `CDV_LLM` | `demo` | `demo` (deterministic rules) or `deepseek` (billable API) |
| `CDV_DATA_DIR` | `data` | SQLite, local account credentials, and worker lock directory |
| `CDV_SCHEDULER` | `true` | Allow scheduled exploration for opted-in goals |
| `CDV_MAX_DAILY_JOBS` | `12` | Stop creating automatic jobs once the UTC daily count reaches this threshold; count includes manual jobs |
| `DEEPSEEK_API_KEY` | empty | Your API key, required only for real model calls |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com` | Chat Completions endpoint base; changing this changes where model inputs and credentials are sent |
| `BILI_CREDENTIAL_PATH` | `<CDV_DATA_DIR>/bili_credential.json` | Local Bilibili login state |
| `CDV_API_URL` | `http://127.0.0.1:8765` | Optional DSH bridge target; loopback HTTP origins only |
| `CDV_BROWSER_BASE_URL` | unset | Explicit test server override; browser tests require demo/demo and the correct app brand |

The runtime currently uses `deepseek-flash`, 60-second model timeouts, up to 4,096 output tokens per call, and at most two concurrent model calls. These are validated settings in `config.py`, not extra environment switches. The scheduler's goal interval is set in the UI/API; `0` disables it, positive values must be at least 15 minutes. A fresh database has no scheduled goals.

`make demo` forces both demo modes and uses `data/demo`. To run a separate instance on another port:

```bash
CDV_PROVIDER=demo CDV_LLM=demo CDV_DATA_DIR=data/demo-alt uv run codiscover-video serve --port 8875
```

Never run two workers against the same data directory. `serve` already includes a worker. Closing the browser does not stop the server, but stopping the process, losing network access or sleeping the computer prevents live work.

`data/` is ignored by Git. JSON exported from the workbench contains user memory and is **not** a redacted public dataset; store it privately. Use the authored [example](../examples/README.md) when sharing a reproduction.
