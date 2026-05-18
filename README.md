# ObsidianAgent

> Created as an ambassador for Dedalus Labs — check out [https://dedaluslabs.ai](https://dedaluslabs.ai/?utm_source=ambassador&utm_medium=referral&utm_campaign=ambassador_program&utm_content=tarek).

A Dedalus-powered interactive agent for managing your local Obsidian vault through a rich terminal UI.

## Features

- **Interactive TUI** — Arrow-key menu navigation with a deep purple aesthetic, powered by `prompt_toolkit` and `rich`
- **Four specialized modes**:
  - **Transcribe** — Convert audio files, YouTube videos, or live microphone recordings into polished Obsidian notes
  - **Fast Research** — Research any topic via web search and create a single comprehensive wiki-style note
  - **Deep Research** — Generate a full college-semester curriculum (planner + executor) with structured units, lessons, and verified sources
  - **Create Mind Map** — Transform any note into a radial mind map with web-researched sources and an auto-generated learning path
- **Sandboxed filesystem tools** — Direct vault manipulation via Python `pathlib` (no Obsidian CLI required)
- **Persona-based architecture** — Each mode runs a dedicated `Persona` with tailored models, system prompts, tools, and MCP servers
- **MCP integration** — Brave Search, Defuddle, and Exa for real-time web research

## Prerequisites

1. **Python 3.13+**
2. **Dedalus API key** from [https://dedaluslabs.ai/dashboard/api-keys](https://dedaluslabs.ai/dashboard/api-keys)
3. **Obsidian vault path** — the absolute path to your local Obsidian vault (persisted in `.env`)

## Setup

1. Clone or navigate to the project directory.
2. Install dependencies:
   ```bash
   uv pip install -e .
   ```
3. Copy the environment file and add your API key:
   ```bash
   cp .env.example .env
   ```
4. Edit `.env` and set:
   ```bash
   DEDALUS_API_KEY=your_key_here
   # OBSIDIAN_VAULT_PATH will be prompted on first run and saved automatically
   ```

## Usage

Launch the interactive terminal UI:

```bash
uv run main.py
```

Navigate with arrow keys and Enter. Press **Ctrl+B** to go back, **Ctrl+C** to quit.

### Main Menu

| Mode | Description |
|------|-------------|
| **Transcribe** | Audio file / YouTube URL / Live recording → timestamped transcript → polished Obsidian note |
| **Fast Research** | Topic → web search → single comprehensive markdown note with citations |
| **Deep Research** | Topic → JSON curriculum plan → full folder structure with overviews, lessons, and sources |
| **Create Mind Map** | Note → radial mind map canvas + researched sources + learning path appended to note |

## Vault Tools

All personas share a sandboxed filesystem toolset scoped to your vault:

| Tool | Purpose |
|------|---------|
| `vault_read` | Read any file in the vault |
| `vault_write` | Create or overwrite files (auto-creates folders) |
| `vault_append` | Append content to existing files |
| `vault_delete` | Delete files |
| `vault_list` | List files and folders |
| `vault_exists` | Check if a path exists |
| `vault_search` | Fuzzy-search filenames |
| `vault_grep` | Search inside markdown file contents |
| `vault_get_metadata` | Parse YAML frontmatter |
| `vault_get_tags` | Extract `#tags` from markdown |
| `vault_get_links` | Extract `[[wikilinks]]` from a note |
| `vault_get_backlinks` | Find all notes linking to a given note |
| `vault_write_canvas` | Write `.canvas` JSON diagram files |

## Configuration

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `DEDALUS_API_KEY` | — | **Required.** Your Dedalus API key |
| `OBSIDIAN_VAULT_PATH` | — | **Required.** Absolute path to your Obsidian vault (auto-prompted) |
| `OBSIDIAN_AGENT_TIMEOUT` | `1800` | Agent run timeout in seconds |

Models and MCP servers are configured per-persona in the orchestrator modules (`obsidian_agent/orchestrator/`).

## Architecture

```
obsidian_agent/
├── agent.py              # Persona class + run_persona() Dedalus runner
├── config.py             # Vault path resolution + agent sandbox folder
├── tools.py              # Sandboxed filesystem tools for vault CRUD, search, metadata, canvas
├── tui.py                # Terminal UI: menus, prompts, progress reports, live recording clock
├── pipelines.py          # Transcription pipelines (file, YouTube, live recording)
└── orchestrator/
    ├── transcription.py  # Transcript → polished Obsidian note
    ├── fast_research.py  # Topic → single research note
    ├── deep_research.py  # Topic → curriculum plan → full build + verify
    └── mind_map.py       # Note → radial mind map + sources + learning path
main.py                   # Entry point: launches the TUI
```

## Notes

- On first run, the app prompts for your vault path and persists it to `.env`.
- An `agent/` sandbox folder is automatically created inside your vault for recordings and temporary files.
- Deep Research uses a two-stage pipeline: a **planner** (read-only, emits JSON) and an **executor** (build mode, creates all files). The orchestrator verifies every planned file exists after execution.
- All transcription outputs include timestamped sections, YAML frontmatter, and source citations.
- Mind maps use Obsidian's native `.canvas` format but are generated with a radial layout (not left-to-right flowcharts).
