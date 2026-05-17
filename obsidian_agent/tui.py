"""
Terminal UI for the Obsidian Agent.

Arrow-key menus via prompt_toolkit. Everything else via Rich.
Deep purple color palette: #060313 -> #674be2 -> #f0edfc.

This is the SOLE printer in the application. All console output flows through here.
"""

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, Layout, Window
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .config import _ensure_vault_path
from .orchestrator import (
    fast_research_orchestration,
    mind_map_generate,
    transcribe_orchestration,
)
from .pipelines import (
    LiveRecorder,
    transcribe_file,
    transcribe_live,
    transcribe_youtube,
)

console = Console()
_session = PromptSession()


class Navigation(Exception):
    """Raised when the user presses Ctrl+B to go back."""


PURPLE = "\x1b[38;2;103;75;226m"
RESET = "\x1b[0m"


async def _prompt(label: str = "❯ ") -> str:
    """Prompt for text input. Ctrl+B raises Navigation; Ctrl+C raises KeyboardInterrupt."""
    kb = KeyBindings()

    @kb.add("c-b")
    def _back(event):
        event.app.exit(exception=Navigation())

    with patch_stdout():
        return await _session.prompt_async(
            ANSI(f"{PURPLE}{label}{RESET}"), key_bindings=kb
        )


async def _confirm(text: str, default: bool = False) -> bool:
    """Yes/No prompt. Ctrl+B raises Navigation."""
    result = await _prompt(f"{text} [y/n]: ")
    stripped = result.strip().lower()
    if not stripped:
        return default
    return stripped in ("y", "yes")


MAIN_OPTIONS = [
    "Transcribe",
    "Fast Research",
    "Deep Research",
    "Create Mind Map",
    "Exit",
]

TRANSCRIBE_OPTIONS = [
    "From File",
    "From YouTube",
    "Live Recording",
    "Back",
]

MENU_STYLE = Style.from_dict(
    {
        "title": "bold #a391ed",
        "selected": "bold reverse #a391ed",
        "normal": "#a391ed",
    }
)


# ---------------------------------------------------------------------------
# Thinking animation
# ---------------------------------------------------------------------------


def _thinking_status(text: str) -> "Status":
    """Return a Rich status context manager with purple spinner."""
    return console.status(
        f"[bold #674be2]{text}[/bold #674be2]",
        spinner="dots",
        spinner_style="#674be2",
    )


# ---------------------------------------------------------------------------
# Display helpers (from old utility.py / deep_research.py)
# ---------------------------------------------------------------------------


def _extract_quoted_path(result_str: str) -> str:
    """Pull the first quoted substring out of a tool result message."""
    m = re.search(r'"([^"]+)"', result_str)
    return m.group(1) if m else "?"


def _count_lines(result_str: str) -> int:
    """Count non-empty lines in a result string."""
    return len([l for l in result_str.splitlines() if l.strip()])


def _summarize_canvas_critique(result_str: str) -> tuple[str, str]:
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
        status = data.get("status", "?")
        icon = "✅" if status == "APPROVED" else "🔴"
        critique = data.get("critique", "")
        first_issue = critique.split(".")[0] if critique else ""
        if len(first_issue) > 60:
            first_issue = first_issue[:57] + "..."
        return icon, f"{status} — {first_issue}" if first_issue else status
    except Exception:
        return "❓", result_str[:80]


_TOOL_SUMMARIZERS: dict[str, Callable[[str], tuple[str, str]]] = {
    "vault_write_canvas": lambda r: (
        "🎨",
        (m.group(1) if (m := re.search(r'"([^"]+\.canvas)"', r)) else "?"),
    ),
    "vault_read": lambda r: ("📖", f"{len(r)} chars"),
    "vault_write": lambda r: ("✍️", _extract_quoted_path(r)),
    "vault_append": lambda r: ("➕", _extract_quoted_path(r)),
    "vault_delete": lambda r: ("🗑", _extract_quoted_path(r)),
    "vault_search": lambda r: ("🔍", f"{_count_lines(r)} file(s) matched"),
    "vault_grep": lambda r: ("🔎", f"{_count_lines(r)} match(es)"),
    "vault_list": lambda r: ("📁", f"{_count_lines(r)} item(s)"),
    "vault_exists": lambda r: ("❓", r[:20]),
    "vault_get_metadata": lambda r: ("🏷", f"{len(r)} chars"),
    "vault_get_tags": lambda r: ("#️⃣", f"{len(r)} chars"),
    "vault_get_links": lambda r: ("🔗", f"{len(r)} chars"),
    "vault_get_backlinks": lambda r: ("🔗", f"{len(r)} chars"),
    "canvas_critique_verdict": _summarize_canvas_critique,
}


def _summarize_tool_result(tool_name: str, result) -> tuple[str, str]:
    """Return (icon, summary_line) for a tool result. Never dump raw content."""
    result_str = str(result) if result is not None else ""
    result_lower = result_str.lower()

    if result_str.startswith("Error:") or "error" in result_lower[:20]:
        return "❌", f"failed: {result_str[:120]}"

    if handler := _TOOL_SUMMARIZERS.get(tool_name):
        return handler(result_str)

    clean = result_str.replace("\n", " ").strip()
    if len(clean) > 80:
        clean = clean[:77] + "..."
    return "•", clean if clean else "(no result)"


def format_progress_report(result, title: str) -> None:
    """Print a clean, deduplicated progress report from a _RunResult."""
    steps = getattr(result, "steps_used", 0)
    msgs = len(getattr(result, "messages", []))

    console.print(
        f"\n[bold #674be2]{title}[/bold #674be2]  [dim]({steps} steps, {msgs} msgs)[/dim]"
    )

    tools_called = getattr(result, "tools_called", [])
    tool_results = getattr(result, "tool_results", [])

    if not tools_called:
        console.print("  [dim]No tools called[/dim]")
        return

    counts = Counter(tools_called)

    for name in counts:
        matching = [tr for tr in tool_results if tr.get("name") == name]
        best_result = None
        for tr in matching:
            res = tr.get("result", "")
            if res is not None and str(res).strip():
                best_result = res

        icon, summary = _summarize_tool_result(name, best_result)
        count_suffix = f" ({counts[name]}×)" if counts[name] > 1 else ""
        console.print(
            f"  {icon}  [#f0edfc]{name}[/#f0edfc]{count_suffix}  →  {summary}"
        )

    if result.final_output and not str(result.final_output).strip().startswith("{"):
        out = str(result.final_output).strip()
        if out:
            out_clean = out.replace("\n", " ")
            if len(out_clean) > 120:
                out_clean = out_clean[:117] + "..."
            console.print(f"  [dim]💬 {out_clean}[/dim]")


def _display_plan_summary(plan: dict) -> None:
    """Print a human-readable summary of the curriculum plan."""
    title = plan.get("title", "Untitled")
    units = plan.get("units", [])
    total_lessons = sum(len(u.get("lessons", [])) for u in units)

    console.print(f"\n[bold #674be2]📋 Research Plan: {title}[/bold #674be2]")
    console.print(f"  [dim]Project folder:[/dim] {plan.get('project_folder', 'N/A')}")
    console.print(
        f"  [dim]Units:[/dim] {len(units)}  |  [dim]Lessons:[/dim] {total_lessons}"
    )

    for i, unit in enumerate(units, 1):
        lesson_count = len(unit.get("lessons", []))
        console.print(
            f"  [dim]  Unit {i}:[/dim] {unit.get('title', 'Untitled')} ({lesson_count} lessons)"
        )


# ---------------------------------------------------------------------------
# UI primitives
# ---------------------------------------------------------------------------


def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def _purple_gradient_text(text: str) -> Text:
    """Create a purple gradient effect for titles."""
    shades = ["#674be2", "#a391ed", "#7f67e7"]
    result = Text()
    for i, char in enumerate(text):
        if char == " ":
            result.append(char)
        else:
            result.append(char, style=f"bold {shades[i % len(shades)]}")
    return result


def _header():
    title = _purple_gradient_text("◆ OBSIDIAN AGENT ◆")
    console.print(
        Panel.fit(
            title,
            border_style="#674be2",
            padding=(1, 4),
            box=box.DOUBLE,
        )
    )


def _pause():
    console.input("[dim]> Press [#674be2]Enter[/#674be2] to continue[/dim]")


def _msg(icon: str, text: str, color: str = "#674be2"):
    console.print(f"[{color}]{icon} {text}[/{color}]")


def _show_goodbye():
    _clear()
    goodbye = _purple_gradient_text("Goodbye!")
    console.print(
        Panel(
            Align.center(goodbye),
            border_style="#674be2",
            box=box.DOUBLE,
            padding=(1, 4),
        )
    )
    sys.exit(0)


async def _menu(title: str, options: list[str], vault_path: str = "") -> int:
    """Arrow-key menu. Returns selected index."""
    selected = 0

    def get_text():
        lines = []
        if vault_path:
            lines.append(("class:normal", f"  📁 {vault_path}\n"))
        lines.append(("class:title", f"  🎯 {title}\n"))
        for i, opt in enumerate(options):
            prefix = "▸" if i == selected else " "
            style = "class:selected" if i == selected else "class:normal"
            lines.append((style, f" {prefix} {opt}\n"))
        lines.append(("class:normal", "\n  [Ctrl+B = Back | Ctrl+C = Quit]\n"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        nonlocal selected
        selected = max(0, selected - 1)
        event.app.invalidate()

    @kb.add("down")
    def _down(event):
        nonlocal selected
        selected = min(len(options) - 1, selected + 1)
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=selected)

    @kb.add("c-b")
    def _ctrl_b(event):
        event.app.exit(result=-1)

    @kb.add("c-c")
    def _ctrl_c(event):
        event.app.exit(exception=KeyboardInterrupt())

    app = Application(
        layout=Layout(Window(FormattedTextControl(get_text))),
        key_bindings=kb,
        full_screen=False,
        style=MENU_STYLE,
    )
    return await app.run_async()


# ---------------------------------------------------------------------------
# Transcription flows via pipelines
# ---------------------------------------------------------------------------


async def _process_transcript(transcript: str, source_info: str = ""):
    """Send a transcript to the transcription orchestrator and display results."""
    stripped = transcript.strip()
    _msg("✓", f"Transcription complete ({len(transcript)} chars)")

    if len(stripped) < 5:
        _msg("✗", "Transcript is empty or too short.", "#f6f4fd")
        console.print("[dim]> Check that the audio contains speech.[/dim]")
        _pause()
        return

    preview = stripped[:300].replace("\n", " ")
    if len(stripped) > 300:
        preview += " ..."
    console.print(f"[dim]> Preview:[/dim] [#674be2]{preview}[/#674be2]")

    if source_info:
        content = f"{source_info}\n\n--- TRANSCRIPT ---\n{transcript}"
    else:
        content = f"Please transcribe this audio.\n\n--- TRANSCRIPT ---\n{transcript}"
    console.print("[#674be2]> Sending to agent...[/#674be2]")

    try:
        from .orchestrator.transcription import TRANSCRIBE

        messages = [{"role": "user", "content": content}]
        with _thinking_status(f"{TRANSCRIBE.name} working..."):
            result = await transcribe_orchestration(messages)
        format_progress_report(result, f"✓ {TRANSCRIBE.name}")
        console.print("[bold #674be2]✓ Note saved to vault[/bold #674be2]")
    except Exception as exc:
        console.print(f"[bold red]✗ Agent failed: {exc}[/bold red]")
    _pause()


async def _transcribe_file():
    _clear()
    _header()
    console.print(
        Panel("🎙 Transcribe from File", border_style="#674be2", box=box.ROUNDED)
    )

    path = (await _prompt()).strip()
    if not path:
        _msg("✗", "No path provided.", "#f6f4fd")
        _pause()
        return

    console.print("[#674be2]> Transcribing...[/#674be2]")
    try:
        transcript = await transcribe_file(path)
    except ValueError as exc:
        _msg("✗", str(exc), "#f6f4fd")
        _pause()
        return
    except Exception as exc:
        _msg("✗", f"Failed: {exc}", "#f6f4fd")
        _pause()
        return

    await _process_transcript(transcript, source_info=f"Audio file: {path}")


async def _transcribe_youtube():
    _clear()
    _header()
    console.print(
        Panel("📺 Transcribe from YouTube", border_style="#674be2", box=box.ROUNDED)
    )

    url = (await _prompt()).strip()
    if not url:
        _msg("✗", "No URL provided.", "#f6f4fd")
        _pause()
        return

    console.print("[#674be2]> Fetching transcript...[/#674be2]")
    try:
        result = await transcribe_youtube(url)
    except ValueError as exc:
        _msg("⚠", str(exc))
        if not await _confirm("Continue anyway?", default=False):
            return
    except Exception as exc:
        _msg("✗", f"Failed: {exc}", "#f6f4fd")
        _pause()
        return

    await _process_transcript(result["transcript"], source_info=result["source_info"])


async def _transcribe_live():
    _clear()
    _header()
    console.print(Panel("🔴 Live Recording", border_style="#674be2", box=box.ROUNDED))
    console.print("[#674be2]> Press Enter to stop recording[/#674be2]")

    recorder = LiveRecorder()
    try:
        recorder.start()
    except RuntimeError as exc:
        _msg("✗", f"Could not start recording: {exc}", "#f6f4fd")
        _pause()
        return

    stop_event = asyncio.Event()

    async def _wait_for_enter():
        """Cross-platform async Enter detection that doesn't fight rich.Live."""
        if sys.platform == "win32":
            import msvcrt

            while not stop_event.is_set():
                await asyncio.sleep(0.05)
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b"\r", b"\n"):
                        stop_event.set()
                        return
        else:
            import select

            loop = asyncio.get_running_loop()
            while not stop_event.is_set():
                ready, _, _ = await loop.run_in_executor(
                    None, select.select, [sys.stdin], [], [], 0.1
                )
                if ready:
                    await loop.run_in_executor(None, sys.stdin.readline)
                    stop_event.set()
                    return
                await asyncio.sleep(0.05)

    start = time.time()

    def _make_clock_display(elapsed: float, blink_on: bool) -> Panel:
        total = int(elapsed)
        hrs = total // 3600
        mins = (total % 3600) // 60
        secs = total % 60

        sep_style = "bold #674be2" if blink_on else "dim #674be2"
        sep = Text(":", style=sep_style)

        if hrs > 0:
            time_text = Text.assemble(
                Text(f"{hrs:02d}", style="bold #674be2"),
                sep,
                Text(f"{mins:02d}", style="bold #674be2"),
                sep,
                Text(f"{secs:02d}", style="bold #674be2"),
            )
        else:
            time_text = Text.assemble(
                Text(f"{mins:02d}", style="bold #674be2"),
                sep,
                Text(f"{secs:02d}", style="bold #674be2"),
            )

        rec_style = "bold red blink" if blink_on else "bold red"
        rec_text = Text("🔴 REC", style=rec_style)

        content = Text.assemble(
            rec_text,
            "\n\n",
            time_text,
            "\n\n",
            Text("Press Enter to stop", style="dim #674be2"),
        )

        return Panel(
            Align.center(content),
            border_style="#674be2",
            box=box.ROUNDED,
            padding=(1, 6),
        )

    enter_task = asyncio.create_task(_wait_for_enter())

    try:
        with Live(console=console, refresh_per_second=8, auto_refresh=True) as live:
            while not stop_event.is_set():
                elapsed = time.time() - start
                blink_on = int(elapsed * 2) % 2 == 0
                live.update(_make_clock_display(elapsed, blink_on))
                await asyncio.sleep(0.125)
    finally:
        if not enter_task.done():
            enter_task.cancel()
            try:
                await enter_task
            except asyncio.CancelledError:
                pass

    wav = recorder.stop()
    console.print(f"[dim]> Captured {recorder.frame_count} frames[/dim]")

    if len(wav) < 1000:
        _msg("✗", "Recording too short or empty.", "#f6f4fd")
        _pause()
        return

    if not await _confirm("Transcribe this recording?", default=True):
        return

    console.print("[#674be2]> Transcribing...[/#674be2]")
    try:
        result = await transcribe_live(wav)
    except Exception as exc:
        _msg("✗", f"Failed: {exc}", "#f6f4fd")
        _pause()
        return

    console.print(f"[dim]> Saved to {Path(result['recording_path']).name}[/dim]")
    await _process_transcript(result["transcript"], source_info=result["source_info"])


# ---------------------------------------------------------------------------
# Research & Canvas flows
# ---------------------------------------------------------------------------


async def _fast_research():
    from .orchestrator.fast_research import GENERATE_NOTE

    _clear()
    _header()
    console.print(Panel("⚡ Fast Research", border_style="#674be2", box=box.ROUNDED))

    topic = (await _prompt()).strip()
    if not topic:
        _msg("✗", "No topic provided.", "#f6f4fd")
        _pause()
        return

    console.print(f"[#674be2]> Researching:[/#674be2] [#674be2]{topic}[/#674be2]")

    messages = [{"role": "user", "content": topic}]
    try:
        with _thinking_status(f"{GENERATE_NOTE.name} working..."):
            result = await fast_research_orchestration(messages)
        format_progress_report(result, f"✓ {GENERATE_NOTE.name}")
    except Exception as exc:
        console.print(f"[bold red]✗ Agent failed: {exc}[/bold red]")
    _pause()


async def _deep_research():
    from .orchestrator.deep_research import (
        DR_EXECUTOR,
        DR_PLANNER,
        deep_research_execute,
        deep_research_plan,
    )

    _clear()
    _header()
    console.print(Panel("🎓 Deep Research", border_style="#674be2", box=box.ROUNDED))

    topic = (await _prompt()).strip()
    if not topic:
        _msg("✗", "No topic provided.", "#f6f4fd")
        _pause()
        return

    console.print(f"[#674be2]> Deep researching:[/#674be2] [#674be2]{topic}[/#674be2]")

    messages = [{"role": "user", "content": topic}]

    # Stage 1: Planner
    console.print("[dim]> Designing curriculum...[/dim]")
    try:
        with _thinking_status(f"{DR_PLANNER.name} working..."):
            plan_data = await deep_research_plan(messages)
    except Exception as exc:
        console.print(f"[bold red]✗ Planner failed: {exc}[/bold red]")
        _pause()
        return

    format_progress_report(plan_data.get("plan_result"), f"✓ {DR_PLANNER.name}")

    plan = plan_data.get("plan")
    if plan is None:
        error = plan_data.get("error", "Unknown error")
        console.print(f"[bold #f6f4fd]✗ {error}[/bold #f6f4fd]")
        if plan_result := plan_data.get("plan_result"):
            console.print("[dim]> Planner output:[/dim]")
            console.print(
                str(getattr(plan_result, "final_output", "(no output)")[:800])
            )
        _pause()
        return

    _display_plan_summary(plan)

    # Stage 2: Executor
    console.print("[dim]> Building all units and lessons...[/dim]")
    try:
        with _thinking_status(f"{DR_EXECUTOR.name} working..."):
            exec_data = await deep_research_execute(plan)
    except Exception as exc:
        console.print(f"[bold red]✗ Executor failed: {exc}[/bold red]")
        _pause()
        return

    if executor_result := exec_data.get("executor_result"):
        format_progress_report(executor_result, f"✓ {DR_EXECUTOR.name}")

    # Verification
    console.print(f"\n[bold #674be2]🔍 Verifying output...[/bold #674be2]")
    missing = exec_data.get("missing_files", [])
    if missing:
        console.print(f"[bold #a391ed]⚠ Missing files ({len(missing)}):[/bold #a391ed]")
        for m in missing[:10]:
            console.print(f"  [dim]  - {m}[/dim]")
        if len(missing) > 10:
            console.print(f"  [dim]  ... and {len(missing) - 10} more[/dim]")
    else:
        console.print(f"[bold #7f67e7]✓ All planned files verified.[/bold #7f67e7]")

    _pause()


async def _create_mind_map():
    from .orchestrator.mind_map import MIND_MAP_AGENT

    _clear()
    _header()
    console.print(Panel("🧠 Create Mind Map", border_style="#674be2", box=box.ROUNDED))

    file_path_str = (await _prompt("File path ❯ ")).strip()
    if not file_path_str:
        _msg("✗", "No file path provided.", "#f6f4fd")
        _pause()
        return

    # Resolve path (absolute or relative to vault)
    vault_path = _ensure_vault_path()
    file_path = Path(file_path_str)
    if not file_path.is_absolute():
        file_path = Path(vault_path) / file_path_str

    # Validate
    if not file_path.exists():
        _msg("✗", f"File not found: {file_path}", "#f6f4fd")
        _pause()
        return
    if not file_path.is_file():
        _msg("✗", f"Path is not a file: {file_path}", "#f6f4fd")
        _pause()
        return
    if file_path.suffix.lower() != ".md":
        _msg("✗", f"File must be a markdown (.md) file: {file_path}", "#f6f4fd")
        _pause()
        return
    if file_path.stat().st_size == 0:
        _msg("✗", f"File is empty: {file_path}", "#f6f4fd")
        _pause()
        return

    # Read content
    try:
        note_content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        _msg("✗", f"Failed to read file: {exc}", "#f6f4fd")
        _pause()
        return

    console.print(
        f"[#674be2]> Creating mind map from:[/#674be2] [#674be2]{file_path.name}[/#674be2]"
    )

    # One-shot mind map generation with research
    console.print("[dim]> Researching topic and building mind map...[/dim]")
    try:
        with _thinking_status(f"{MIND_MAP_AGENT.name} working..."):
            result = await mind_map_generate(str(file_path), note_content)
    except Exception as exc:
        console.print(f"[bold red]✗ Mind map generation failed: {exc}[/bold red]")
        _pause()
        return

    format_progress_report(result.get("result"), f"✓ {MIND_MAP_AGENT.name}")

    mind_map_path = result.get("mind_map_path")
    sources = result.get("sources_found", [])
    note_refined = result.get("note_refined", False)
    error = result.get("error")

    if error:
        console.print(f"[bold #a391ed]⚠ {error}[/bold #a391ed]")
    elif mind_map_path:
        console.print(
            f"[bold #7f67e7]✓ Mind map created: {mind_map_path}[/bold #7f67e7]"
        )

    if note_refined:
        console.print(
            f"[bold #7f67e7]✓ Note refined with sources & learning path[/bold #7f67e7]"
        )

    if sources:
        console.print(f"\n[bold #674be2]📚 Sources Found:[/bold #674be2]")
        for src in sources[:5]:
            title = src.get("title", "Unknown")
            desc = src.get("description", "")
            console.print(f"  [dim]• {title}[/dim]")
            if desc:
                console.print(f"    [dim]{desc[:120]}[/dim]")

    _pause()


async def _noop():
    """No-op async function for menu handlers that do nothing."""
    return None


# ---------------------------------------------------------------------------
# Main TUI loop
# ---------------------------------------------------------------------------


async def run_tui():
    vault_path = _ensure_vault_path()

    async def _transcribe_menu():
        _clear()
        _header()
        sub = await _menu("Transcribe", TRANSCRIBE_OPTIONS, vault_path=vault_path)
        if sub == -1:
            return
        handlers = [_transcribe_file, _transcribe_youtube, _transcribe_live, _noop]
        if 0 <= sub < len(handlers):
            h = handlers[sub]
            if h:
                try:
                    await h()
                except Navigation:
                    pass

    handlers = [
        _transcribe_menu,
        _fast_research,
        _deep_research,
        _create_mind_map,
        _show_goodbye,
    ]

    try:
        while True:
            _clear()
            _header()

            choice = await _menu(
                "What would you like to do?", MAIN_OPTIONS, vault_path=vault_path
            )
            if choice == -1:
                continue
            if 0 <= choice < len(handlers):
                try:
                    await handlers[choice]()
                except Navigation:
                    pass

    except KeyboardInterrupt:
        _show_goodbye()
