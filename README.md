# AsterMem

> Your memories, on your own machine — a self-hosted memory service for you and your AI agents.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE) [![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/) [![X](https://img.shields.io/badge/X-@Asterove__ai-black.svg)](https://x.com/Asterove_ai)

AsterMem stores everything locally: plain Markdown files, SQLite, and an on-disk vector index. No cloud sync, no telemetry — nobody but you ever sees the plaintext.

## Highlights

- **Own your data** — memories live in `./data/` as Markdown + SQLite + a local vector index. Backing up means copying one folder.
- **Built for AI agents** — a single `POST /api/agent/call` endpoint, plus a drop-in SKILL package for Cursor and Claude Code.
- **Hybrid search** — keyword search (Whoosh + jieba) and semantic search (Chroma), with paragraph-level retrieval.
- **Bring your own models** — 24 providers out of the box, including LM Studio and Ollama for local inference, plus OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Moonshot, Zhipu, MiniMax, OpenRouter and more. Providers are config-driven; add your own without touching code.
- **Clean web UI** — a React SPA, available in 10 languages: English, Deutsch, Español, Français, Português, Русский, 日本語, 한국어, 繁體中文, and 简体中文.

## Quick start

You'll need Python 3.11 (3.10+ works) and Node.js 18+ for the web UI.

**macOS / Linux**

```bash
git clone https://github.com/Asterove/AsterMem.git && cd astermem
./start.sh
```

**Windows**

```powershell
git clone https://github.com/Asterove/AsterMem.git
cd astermem
.\start.bat          # or double-click start.bat in Explorer
```

The start script is idempotent: the first run creates `venv/`, installs dependencies, builds the web UI, and starts the server. Subsequent runs skip anything already in place and boot in seconds. Useful flags: `--rebuild-ui` after changing `web-ui/`, `--skip-ui`, `--reinstall` (PowerShell: `-RebuildUi`, `-SkipUi`, `-Reinstall`).

On first boot, the script prints the web UI address (a random port between 8000 and 9000, saved to `config.yaml`). Log in with the default credentials **`admin` / `admin`**, change them under **Admin → Sign-in**, pick an embedding provider in **Settings**, and you're all set.

AsterMem is single-user by design — there's exactly one admin account. On a machine only you can reach, you can disable **Require a username and password** in **Admin → Sign-in** to browse without logging in. API tokens used by your AI agents aren't affected by that switch.

Forgot your password? Run `./venv/bin/python server.py --reset-admin` (Windows: `venv\Scripts\python.exe server.py --reset-admin`) on the host. This resets the account to `admin` / `admin` without touching your memories.

**Docker (no local Python / Node needed)**

```bash
git clone https://github.com/Asterove/AsterMem.git && cd astermem
docker compose up -d
```

The web UI is at `http://localhost:8768` (fixed port in Docker, unlike the random port picked by the start scripts). Memories *and* `config.yaml` are persisted in `./data/`, so backup is still just copying that one folder. To pass provider API keys, copy `.env.example` to `.env` before starting — compose picks it up automatically. After changing the code, rebuild with `docker compose up -d --build`.

To reset a forgotten password: `docker compose exec astermem python server.py --reset-admin`.

<details>
<summary>Manual setup (if you'd rather run the steps yourself)</summary>

```bash
python3.11 -m venv venv && ./venv/bin/pip install -r requirements.txt
cd web-ui && npm install && npm run build && cd ..
./venv/bin/python server.py
```

On Windows, the interpreter lives at `venv\Scripts\python.exe`.
</details>

## Connect your AI (Cursor / Claude Code)

1. In the web UI, go to **Admin → API Tokens** and create a token.
2. Write `~/.astermem/credentials`:

   ```
   ASTERMEM_BASE_URL=http://localhost:<port>
   ASTERMEM_TOKEN=ast_xxxxxxxx
   ```

3. Copy `skill/astermem/` into `~/.cursor/skills/` (or `~/.claude/skills/`).

That's it — your agent can now add, search, patch, and archive memories through `scripts/astermem.sh`. It can also configure providers, store API keys, test model connections, and rebuild the vector index. See `skill/astermem/reference.md` for the full tool list.

Protected REST routes also accept scoped Bearer tokens. By default, a token can read and write memories and change model configuration; admin and destructive scopes are opt-in, and destructive requests require an explicit confirmation header.

## Architecture

```
start.sh / start.ps1      # one-command launch (start.bat = Windows double-click wrapper)
server.py                 # entry point: python3.11 server.py
backend/
  main.py                 # FastAPI app assembly, SPA hosting, API logging
  memory/                 # core: database / storage / vector / search / chunker / providers
  web/                    # ~115 REST endpoints + /api/agent/call
web-ui/                   # React SPA (Vite + TS), built into web-ui/dist
skill/                    # distributable SKILL packages (SKILL.md + astermem.sh)
scripts/                  # maintenance utilities
tests/                    # pytest suite
data/                     # your memories (gitignored): SQLite + MD + Chroma + Whoosh
```

Everything runs as one process on one port: `/api/*` serves the SPA and agents, static files serve the UI.

## Data ownership & backup

- All data lives in `./data/`. **Backup = copy that directory.**
- Export a portable zip anytime from the web UI (**Import / Export**).
- Switching embedding providers rebuilds the vector index in the background. Keyword search keeps working the whole time, and interrupted rebuilds resume automatically on the next boot.

## Tests

```bash
./venv/bin/python -m pytest tests/ -q
```

## License

AGPL-3.0 — Copyright (c) 2026 [Asterove](https://asterove.com/)

Website: [asterove.com](https://asterove.com/) · X: [@Asterove_ai](https://x.com/Asterove_ai) · Contact: [connect@asterove.com](mailto:connect@asterove.com)
