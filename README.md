# ObsidianAgent

> Created as an ambassador for Dedalus Labs — check out [https://dedaluslabs.ai](https://dedaluslabs.ai/?utm_source=ambassador&utm_medium=referral&utm_campaign=ambassador_program&utm_content=tarek).

A Dedalus-powered desktop workbench for your Obsidian vault. ObsidianAgent ships a polished terminal UI (Go + Bubble Tea) sitting on top of a FastAPI backend that orchestrates six specialised AI agents — fast research, deep research, transcription, mind-mapping, podcast generation, and Anki flashcards — all writing directly into your local vault.

## Introduction

ObsidianAgent treats your vault as the substrate for a small fleet of task-specific agents. Each agent (`Persona`) has its own model, system prompt, MCP servers, and a sandboxed set of vault tools. The frontend is a slash-command TUI inspired by OpenCode; the backend is a FastAPI service that exposes one endpoint per agent and tracks long-running work as polled jobs.

The architecture cleanly separates three concerns:

1. **Machine servers** (`obsidian_agent/orchestrator/<machine>/server_setup/`) — each of the three Dedalus VMs is described entirely by one self-contained folder: a `server.py` (the agents themselves — personas, prompts, pipelines, tools), its supporting modules, and a `setup.sh` that bootstraps the VM. Nothing agent-related runs locally.
2. **Machines** (`obsidian_agent/machine.py`) — the single owner of the Dedalus VM lifecycle. All six agents execute on three persistent Dedalus machines: `podcast` (1 agent), `flashcard` (1 agent), and `utility` (transcription + fast research + deep research + mind map behind one FastAPI server). `machine.py` tars each machine's entire `server_setup/` directory into a deterministic bundle, finds/wakes/creates the VM, deploys the bundle, and keeps deployed files in sync via a content hash.
3. **Surfaces** — a FastAPI HTTP API (`api/app.py`) that routes every agent endpoint through one dispatch table of thin async machine clients (`orchestrator/<machine>/client.py`), and a Go TUI built on Bubble Tea (`tui-go/`) that calls it.

Every agent request returns a `job_id`; the TUI polls `/jobs/{job_id}` and renders progress live in its sidebar. For the utility machine, jobs also run machine-side: the local API polls the machine's own `/jobs/{id}` endpoint, mirrors its progress lines into the local job, and writes the returned deliverables (new/changed vault files) into your local vault.

## Project Layout

```
ObsidianAgent/
├── main.py                          # Launches the FastAPI backend + TUI together
├── api/
│   ├── app.py                       # FastAPI service: one endpoint per orchestrator + job polling
│   └── utils.py                     # Job tracking, config helpers, shared request/response models
├── obsidian_agent/
│   ├── machine.py                   # ALL Dedalus VM lifecycle: specs, registry, bundle deploy
│   ├── machines.json                # Runtime registry of machine ids (gitignored)
│   └── orchestrator/
│       ├── __init__.py              # AGENT_CLIENTS dispatch table (agent kind → client fn)
│       ├── config.py                # API-key + vault-path resolution (local side)
│       ├── utility/                 # transcribe + fast/deep research + mind map, one VM
│       │   ├── client.py            # Local client: upload inputs, poll jobs, apply deliverables
│       │   └── server_setup/        # Self-contained bundle deployed to the utility VM
│       │       ├── server.py        # FastAPI routes + all four agents (personas + jobs)
│       │       ├── runner.py        # Persona class + run_persona() Dedalus runner
│       │       ├── tools.py         # Sandboxed vault tools (read/write/search/canvas/...)
│       │       ├── pipelines.py     # YouTube / audio-file transcript extraction
│       │       ├── prompts/         # The agents' system prompts
│       │       └── setup.sh         # VM bootstrap: venv, env file, systemd unit
│       ├── podcast/                 # Note → .wav podcast (Dedalus VM)
│       │   ├── client.py            # Thin async client over machine.py
│       │   └── server_setup/        # server.py + system_prompt.md + setup.sh
│       └── flashcard/               # Note → Anki .apkg deck (Dedalus VM)
│           ├── client.py
│           └── server_setup/
└── tui-go/                          # Go terminal UI (Bubble Tea + Lip Gloss + Glamour)
    ├── main.go                      # Entry point: flags, .env discovery, alt-screen program
    └── internal/
        ├── ui/                      # Root model, layout, viewport, sidebar, slash menu, wizard
        ├── api/                     # Backend HTTP client
        ├── theme/                   # Obsidian-flavored dark palette
        ├── recorder/                # ffmpeg live microphone recorder
        └── history/                 # Persistent command history
```

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.13+** | Backend runtime |
| **Go 1.22+** | TUI build toolchain (`brew install go`) |
| [`uv`](https://github.com/astral-sh/uv) | Python package manager used by `pyproject.toml` |
| **Dedalus API key** | Get one at [dedaluslabs.ai/dashboard/api-keys](https://dedaluslabs.ai/dashboard/api-keys) |
| **Obsidian vault** | Absolute path to a local vault. The agent works inside an auto-created `agent/` subfolder. |

Live microphone recording uses the system `ffmpeg` (`brew install ffmpeg`); everything else works without it.

## Setup

### 1. Clone and install the Python backend

```bash
cd ObsidianAgent
uv pip install -e .
```

### 2. The TUI builds itself

The TUI is a single Go binary that `main.py` builds automatically on first
launch (and rebuilds whenever the Go sources change) — no separate build step.

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

This builds the TUI binary if missing or stale, then spawns:

- `uvicorn api.app:app --port 8000` (the backend)
- `tui-go/obsidian-tui` (the TUI)

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
| `/podcast <note-path>` | Render the note as a `.wav` podcast on a fresh Dedalus VM |
| `/flashcard <note-path>` | Generate an Anki `.apkg` deck from the note on a fresh Dedalus VM |
| `/config` | Re-run the API-key + vault-path wizard |
| `/clear` | Clear the conversation pane |
| `/help` | List all commands |
| `/quit` | Quit |

Note paths can be absolute or relative to the vault's `agent/` sandbox folder.

## The Agents

Each agent is defined inside its machine's `server_setup/server.py`. Models and MCP servers are configured in code, not via env vars.

The transcription, fast research, deep research, and mind map agents all run on the **utility machine** (1 vCPU / 2 GiB / 5 GiB) as one self-contained server: the four `Persona`s live directly in `utility/server_setup/server.py`, backed by sibling modules (`runner.py`, `tools.py`, `pipelines.py`, `prompts/`) that ship in the same bundle, running against a persistent machine-side vault at `/home/machine/vault`. The local API sends each job only the minimal input it needs (a topic string, the one source note, the one audio file), polls the machine's job endpoint while mirroring progress, and copies the resulting files back into your local vault. The machine vault is never cleaned up, so it accumulates a copy of all agent-generated content that later runs can read and search.

### Transcription Agent

- **Pipeline:** auto-detects input — YouTube URL or local audio/video file (live microphone recordings are captured by the TUI and uploaded as files) — runs the appropriate transcript extractor, then hands the timestamped transcript to a Dedalus persona that polishes it into a single Obsidian note.
- **Model:** `anthropic/claude-haiku-4-5-20251001`
- **Output:** one markdown file with YAML frontmatter, timestamp-anchored sections, key takeaways, and a `## Sources` section citing the original.
- **Endpoint:** `POST /transcribe` → `job_id`

### Fast Research Agent

- **Pipeline:** topic in, single comprehensive wiki-style note out. The agent must perform at least three Brave searches plus two Defuddle page reads before writing anything.
- **Model:** `anthropic/claude-haiku-4-5-20251001`
- **MCP servers:** `windsor/brave-search-mcp`, `nickyhec/defuddle-mcp`, `tsion/exa`
- **Output:** 300–800 words, YAML frontmatter, headings, optional wikilinks, mandatory `## Sources` section with URLs and access dates.
- **Endpoint:** `POST /research/fast` → `job_id`

### Deep Research Agent

- **Pipeline:** two-stage planner/executor split.
  1. **Planner** — read-only persona running `openai/gpt-5.2` with a strict JSON-schema response. Performs broad research and emits a `CurriculumPlan` (4 units × 5–7 lessons each).
  2. **Executor** — build-mode persona running `anthropic/claude-sonnet-4-5-20250929` that receives the plan and creates every folder, every `overview.md`, and every lesson, integrating per-lesson web research as it writes. The orchestrator then verifies every planned file exists.
- **MCP servers:** Brave Search, Defuddle, Exa.
- **Output:** a full `project_folder/` hierarchy with root overview, per-unit overviews, and 20–28 lessons of 800–1500 words each — every file carrying YAML frontmatter, wikilinks, and cited sources.
- **Endpoint:** `POST /research/deep` → `job_id`

### Mind Map Agent

- **Pipeline:** one-shot. Reads a note (the source of truth), does light web research for recommended sources, then writes an Obsidian `.canvas` file laid out as a radial mind map and appends a "Mind Map Sources & Learning Path" section back to the original note.
- **Model:** `anthropic/claude-sonnet-4-5-20250929`
- **Output:** a `.canvas` with a central node, 4–6 colour-coded main branches, sub-branches, and an isolated "Sources & Next Steps" group — built under strict spacing and no-edge-crossing rules.
- **Endpoint:** `POST /mind-map` → `job_id`

### Podcast Agent

- **Pipeline:** runs on a **persistent** Dedalus VM (2 vCPU / 4 GiB / 10 GiB) that survives across requests. The machine ID is pinned in `obsidian_agent/machines.json` and Dedalus auto-sleeps the VM after 30 min of inactivity; no explicit teardown is performed. For each request `machine.py`:
  1. Reuses the persisted machine — wakes it from snapshot if it's sleeping (can take up to ~15 min — Kokoro + venv weigh ~10 GB), or builds a fresh VM if the persisted ID is gone (first run: ~12 min provisioning, deploys the `server_setup/` bundle).
  2. Health-checks the on-VM venv and re-deploys the bundle (with a service restart) whenever the local bundle's sha256 differs from what was last deployed.
  3. Reuses an existing `ready` HTTPS preview on port 8000 if one exists; otherwise opens a new one.
  4. POSTs the note content to `/generate-podcast`, with exponential-backoff retries on 5xx / connection errors.
  5. Saves the returned `.wav` to `<vault>/agent/podcasts/`.
- **Endpoint:** `POST /podcast` → `job_id`
- **Cleanup:** Routine runs never destroy the VM — Dedalus autosleep handles idle cost. `DELETE /machines` is an explicit "tear everything down" hatch (clears all org machines) for when you want to fully reset.

### Flashcard Agent

- **Pipeline:** same persistent-machine + autosleep + preview-reuse pattern as the podcast agent, sized smaller (1 vCPU / 2 GiB / 5 GiB). The on-VM server turns the note into a structured deck and returns an Anki `.apkg`, saved to `<vault>/agent/flashcards/`.
- **Endpoint:** `POST /flashcard` → `job_id`

## Vault Tools

The utility-machine personas share a sandboxed filesystem toolset (`utility/server_setup/tools.py`) that executes on the machine, scoped to the machine vault's `agent/` folder. Paths are validated against directory traversal, symlinks, depth, and length; absolute paths are rejected.

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
| `POST` | `/research/fast` | `{topic}` | `{job_id}` |
| `POST` | `/research/deep` | `{topic}` | `{job_id}` |
| `POST` | `/mind-map` | `{note_path}` | `{job_id}` |
| `POST` | `/podcast` | `{note_path}` | `{job_id}` |
| `POST` | `/flashcard` | `{note_path}` | `{job_id}` |
| `GET` | `/jobs/{job_id}` | — | `{status, progress[], result, error}` |
| `DELETE` | `/machines` | — | Destroys any leftover Dedalus VMs |

Jobs transition through `pending → running → done | failed`. Every agent endpoint dispatches through the same `AGENT_CLIENTS` table to an async machine client that appends progress bullets to the job (utility jobs additionally mirror the machine's own job progress). Note: the mind-map job result carries `final_output` (a string) instead of the raw runner object.

## Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `DEDALUS_API_KEY` | — | **Required.** Your Dedalus API key |
| `OBSIDIAN_VAULT_PATH` | — | **Required.** Absolute path to your Obsidian vault |
| `OBSIDIAN_AGENT_TIMEOUT` | `1800` | Dedalus runner timeout, in seconds |

Models, MCP servers, and persona behaviour are configured per-agent inside each machine's `server_setup/server.py`.

## Notes

- **Vault sandbox.** Every tool call is path-validated against the vault root. Absolute paths, `..` traversal, symlinks, and over-long/over-deep paths are rejected at the tool level.
- **Deep research determinism.** The planner uses strict JSON-schema output (`additionalProperties: false` injected recursively) so the executor always receives a well-typed plan.
- **Mind-map fidelity.** The mind-map agent treats the source note as authoritative — web research only contributes sources and a learning path, never replacement content.
- **VM hygiene.** All three machines (podcast, flashcard, utility) rely on Dedalus autosleep (30 min idle window) rather than per-request destroy. Machine ids and deployed-bundle hashes are persisted in `obsidian_agent/machines.json` so warm reuse survives TUI restarts, and code changes to any `server_setup/` bundle auto-redeploy on the next request. `DELETE /machines` is a manual hard-reset that destroys every non-destroyed machine in the org — useful when a VM gets into a broken state or you want to force a fresh build.
- **Utility machine vault.** The utility VM keeps its own vault at `/home/machine/vault` that persists across jobs and sleeps. Job inputs are uploaded into it, agent outputs are written to it, and only the files created/changed by a job are shipped back and merged into your local vault — your local vault remains the source of truth.
- **Live recording.** The TUI uses the system `ffmpeg` to capture from the default input device, validates the file is non-empty, then uploads it through the same `/transcribe` endpoint as a file path.

## License

MIT — see `LICENSE`.
