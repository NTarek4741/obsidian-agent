# ObsidianAgent

> Created as an ambassador for Dedalus Labs — check out [https://dedaluslabs.ai](https://dedaluslabs.ai/?utm_source=ambassador&utm_medium=referral&utm_campaign=ambassador_program&utm_content=tarek).

A Dedalus-powered desktop workbench for your Obsidian vault. ObsidianAgent ships a polished terminal UI (Ink + React + TypeScript) sitting on top of a FastAPI backend that orchestrates six specialised AI agents — research, transcription, mind-mapping, podcast generation, and Anki flashcards — all writing directly into your local vault.

## Introduction

ObsidianAgent treats your vault as the substrate for a small fleet of task-specific agents. Each agent (`Persona`) has its own model, system prompt, MCP servers, and a sandboxed set of vault tools. The frontend is a slash-command TUI inspired by OpenCode; the backend is a FastAPI service that exposes one endpoint per agent and tracks long-running work as polled jobs.

The architecture cleanly separates three concerns:

1. **Personas** (`obsidian_agent/agent.py`) — declarative wrappers around a Dedalus runner: model, tools, MCP servers, optional JSON-schema output.
2. **Orchestrators** (`obsidian_agent/orchestrator/<agent>/`) — multi-step pipelines that compose personas, MCPs, and vault tools to deliver a finished artefact (a note, a curriculum, a `.canvas`, an `.m4a`, an `.apkg`).
3. **Surfaces** — a FastAPI HTTP API (`api/app.py`) and an Ink-based TUI (`tui/`) that calls it.

Long-running orchestrators (deep research, podcast, flashcard, transcription) execute on the backend and return a `job_id`; the TUI polls `/jobs/{job_id}` and renders progress live in its sidebar.

## Project Layout

```
ObsidianAgent/
├── main.py                          # Launches the FastAPI backend + TUI together
├── api/
│   └── app.py                       # FastAPI service: one endpoint per orchestrator + job polling
├── obsidian_agent/
│   ├── agent.py                     # Persona class + run_persona() Dedalus runner
│   ├── tools.py                     # Sandboxed vault tools (read/write/search/canvas/...)
│   └── orchestrator/
│       ├── utility.py               # API-key + vault-path resolution
│       ├── transcription/           # File / YouTube / live mic → polished Obsidian note
│       ├── fast_research/           # Topic → single wiki-style note with citations
│       ├── deep_research/           # Topic → JSON curriculum plan → full folder build
│       ├── mind_map/                # Note → radial .canvas mind map + learning path
│       ├── podcast/                 # Note → M4A podcast (Dedalus VM)
│       └── flashcard/               # Note → Anki .apkg deck (Dedalus VM)
└── tui/                             # Ink + React + TypeScript terminal UI
    ├── src/
    │   ├── components/              # App, Sidebar, Viewport, ConfigWizard, JobBox, ...
    │   ├── hooks/                   # useBackend, useJobs, useCommands, useHistory, useEnv
    │   └── api/                     # Backend HTTP client + live microphone recorder
    └── dist/                        # Compiled entry point (node dist/index.js)
```

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.13+** | Backend runtime |
| **Node.js 18+** | TUI runtime |
| [`uv`](https://github.com/astral-sh/uv) | Python package manager used by `pyproject.toml` |
| **Dedalus API key** | Get one at [dedaluslabs.ai/dashboard/api-keys](https://dedaluslabs.ai/dashboard/api-keys) |
| **Obsidian vault** | Absolute path to a local vault. The agent works inside an auto-created `agent/` subfolder. |

The TUI bundles its own `ffmpeg` binary via `@ffmpeg-installer/ffmpeg`, so live microphone recording works without a system install.

## Setup

### 1. Clone and install the Python backend

```bash
cd ObsidianAgent
uv pip install -e .
```

### 2. Build the TUI

```bash
cd tui
npm install
npm run build
cd ..
```

### 3. Configure credentials

You can configure the agent in either of two ways:

**Option A — interactive wizard (recommended):** just launch the app and the TUI will detect a missing config and walk you through it.

**Option B — manual `.env`:** create `/Users/you/Desktop/ObsidianAgent/.env` with:

```bash
DEDALUS_API_KEY=sk-your-key-here
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
```

Optional:

```bash
OBSIDIAN_AGENT_TIMEOUT=1800   # Dedalus agent run timeout in seconds (default 1800)
```

### 4. Run

```bash
uv run main.py
```

This spawns:

- `uvicorn api.app:app --port 8000` (the backend)
- `node tui/dist/index.js` (the TUI)

Quitting the TUI cleanly terminates the backend.

## Using the TUI

The TUI is slash-command driven. Type `/` to open the auto-complete menu.

| Command | Description |
|---------|-------------|
| `/transcribe <file>` | Transcribe a local audio or video file into a polished note |
| `/transcribe yt <url>` | Pull a YouTube transcript and turn it into a note |
| `/transcribe live` | Start microphone recording; press Enter again to stop and transcribe |
| `/research fast <topic>` | One-shot, single-note research with web citations |
| `/research deep <topic>` | Two-stage curriculum: planner emits JSON, executor builds the full folder |
| `/mindmap <note-path>` | Build a radial `.canvas` mind map and append a learning path to the note |
| `/podcast <note-path>` | Render the note as an `.m4a` podcast on a fresh Dedalus VM |
| `/flashcard <note-path>` | Generate an Anki `.apkg` deck from the note on a fresh Dedalus VM |
| `/config` | Re-run the API-key + vault-path wizard |
| `/jobs` | Show active and recent jobs |
| `/clear` | Clear the conversation pane |
| `/help` | List all commands |
| `/quit` | Quit |

Note paths can be absolute or relative to the vault's `agent/` sandbox folder.

## The Agents

Each agent is a `Persona` (or pair of personas) defined in `obsidian_agent/orchestrator/<agent>/orchestration.py`. Models and MCP servers are configured in code, not via env vars.

### Transcription Agent

- **Pipeline:** auto-detects input — YouTube URL, local audio/video file, or raw bytes from the live microphone — runs the appropriate transcript extractor, then hands the timestamped transcript to a Dedalus persona that polishes it into a single Obsidian note.
- **Model:** `anthropic/claude-haiku-4-5`
- **Output:** one markdown file with YAML frontmatter, timestamp-anchored sections, key takeaways, and a `## Sources` section citing the original.
- **Endpoint:** `POST /transcribe` → `job_id`

### Fast Research Agent

- **Pipeline:** topic in, single comprehensive wiki-style note out. The agent must perform at least three Brave searches plus two Defuddle page reads before writing anything.
- **Model:** `anthropic/claude-haiku-4-5`
- **MCP servers:** `windsor/brave-search-mcp`, `nickyhec/defuddle-mcp`, `tsion/exa`
- **Output:** 300–800 words, YAML frontmatter, headings, optional wikilinks, mandatory `## Sources` section with URLs and access dates.
- **Endpoint:** `POST /research/fast` (direct, returns the note inline)

### Deep Research Agent

- **Pipeline:** two-stage planner/executor split.
  1. **Planner** — read-only persona running `openai/gpt-5.2` with a strict JSON-schema response. Performs broad research and emits a `CurriculumPlan` (4 units × 5–7 lessons each).
  2. **Executor** — build-mode persona running `anthropic/claude-sonnet-4-5` that receives the plan and creates every folder, every `overview.md`, and every lesson, integrating per-lesson web research as it writes. The orchestrator then verifies every planned file exists.
- **MCP servers:** Brave Search, Defuddle, Exa.
- **Output:** a full `project_folder/` hierarchy with root overview, per-unit overviews, and 20–28 lessons of 800–1500 words each — every file carrying YAML frontmatter, wikilinks, and cited sources.
- **Endpoint:** `POST /research/deep` → `job_id`

### Mind Map Agent

- **Pipeline:** one-shot. Reads a note (the source of truth), does light web research for recommended sources, then writes an Obsidian `.canvas` file laid out as a radial mind map and appends a "Mind Map Sources & Learning Path" section back to the original note.
- **Model:** `anthropic/claude-sonnet-4-5`
- **Output:** a `.canvas` with a central node, 4–6 colour-coded main branches, sub-branches, and an isolated "Sources & Next Steps" group — built under strict spacing and no-edge-crossing rules.
- **Endpoint:** `POST /mind-map` (direct, returns canvas path + metadata)

### Podcast Agent

- **Pipeline:** because podcast generation needs heavier audio tooling, this agent runs on a freshly provisioned Dedalus VM (2 vCPU / 4 GiB / 10 GiB). For each request the orchestrator:
  1. Creates a new machine via `dedalus_sdk`.
  2. Uploads `server.py` + `setup.sh` and installs dependencies on the VM.
  3. Opens an HTTPS preview to the machine's FastAPI server.
  4. POSTs the note content to `/generate-podcast`, with exponential-backoff retries.
  5. Saves the returned `.m4a` to `<vault>/agent/podcasts/`.
  6. Destroys the machine — success or failure.
- **Endpoint:** `POST /podcast` → `job_id`
- **Cleanup:** `DELETE /machines` deletes any machines left behind by an interrupted run.

### Flashcard Agent

- **Pipeline:** same VM-per-job pattern as the podcast agent, sized smaller (1 vCPU / 2 GiB / 5 GiB). The on-VM server turns the note into a structured deck and returns an Anki `.apkg`, saved to `<vault>/agent/flashcards/`.
- **Endpoint:** `POST /flashcard` → `job_id`

## Vault Tools

All personas share a sandboxed filesystem toolset scoped to the configured vault. Paths are validated against directory traversal, symlinks, depth, and length; absolute paths are rejected.

| Tool | Purpose |
|------|---------|
| `vault_read` | Read any file in the vault |
| `vault_write` | Create or overwrite files (auto-creates folders) |
| `vault_append` | Append content to existing files |
| `vault_delete` | Delete files |
| `vault_list` | List files and folders |
| `vault_exists` | Check if a path exists |
| `vault_search` | Fuzzy-search filenames |
| `vault_grep` | Search inside markdown contents |
| `vault_get_metadata` | Parse YAML frontmatter |
| `vault_get_tags` | Extract `#tags` from markdown |
| `vault_get_links` | Extract `[[wikilinks]]` from a note |
| `vault_get_backlinks` | Find all notes linking to a given note |
| `vault_write_canvas` | Write `.canvas` JSON diagram files |

An `agent/` sandbox folder is auto-created inside your vault on first run; transcription recordings, generated podcasts, and flashcard decks land there.

## HTTP API Reference

The backend can be used standalone (without the TUI). Default base URL: `http://localhost:8000`.

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `GET` | `/health` | — | `{status, configured, vault}` |
| `POST` | `/config` | `{api_key, vault_path}` | Persists credentials to `.env` |
| `POST` | `/transcribe` | `{content}` | `{job_id}` — content is a path or YouTube URL |
| `POST` | `/research/fast` | `{topic}` | `{result}` (direct) |
| `POST` | `/research/deep` | `{topic}` | `{job_id}` |
| `POST` | `/mind-map` | `{note_path}` | Canvas metadata (direct) |
| `POST` | `/podcast` | `{note_path}` | `{job_id}` |
| `POST` | `/flashcard` | `{note_path}` | `{job_id}` |
| `GET` | `/jobs` | — | All jobs, newest first |
| `GET` | `/jobs/{job_id}` | — | `{status, progress[], result, error}` |
| `DELETE` | `/machines` | — | Destroys any leftover Dedalus VMs |

Jobs transition through `pending → running → done | failed`. Progress messages are captured from the orchestrator's stdout.

## Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `DEDALUS_API_KEY` | — | **Required.** Your Dedalus API key |
| `OBSIDIAN_VAULT_PATH` | — | **Required.** Absolute path to your Obsidian vault |
| `OBSIDIAN_AGENT_TIMEOUT` | `1800` | Dedalus runner timeout, in seconds |

Models, MCP servers, and persona behaviour are configured per-agent inside `obsidian_agent/orchestrator/<agent>/orchestration.py`.

## Notes

- **Vault sandbox.** Every tool call is path-validated against the vault root. Absolute paths, `..` traversal, symlinks, and over-long/over-deep paths are rejected at the tool level.
- **Deep research determinism.** The planner uses strict JSON-schema output (`additionalProperties: false` injected recursively) so the executor always receives a well-typed plan.
- **Mind-map fidelity.** The mind-map agent treats the source note as authoritative — web research only contributes sources and a learning path, never replacement content.
- **VM hygiene.** Podcast and flashcard runs always destroy their VM in a `finally` block. If a run is killed mid-flight, `DELETE /machines` (also exposed in the TUI as part of housekeeping) reclaims any orphaned machines.
- **Live recording.** The TUI uses a bundled `ffmpeg` to capture from the default input device, validates the file is non-empty, then uploads it through the same `/transcribe` endpoint as a file path.

## License

MIT — see `LICENSE`.
