"""Flashcard orchestrator using a Dedalus Machine.

Flow: Create machine → Setup server → Generate flashcards → Delete machine
Each deck gets a fresh VM. No wake/sleep.

Machine specs: 1 vCPU / 2 GiB / 5 GiB
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import httpx
from dedalus_sdk import Dedalus

from obsidian_agent.orchestrator.utility import _ensure_vault_path, _load_api_key

# ---------------------------------------------------------------------------
# Dedalus SDK client
# ---------------------------------------------------------------------------


def _get_dedalus_client():
    """Return a Dedalus Machines client."""
    api_key = _load_api_key()
    return Dedalus(api_key=api_key)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}
VM_SPECS = {"vcpu": 1, "memory_mib": 2048, "storage_gib": 5}
PREVIEW_VISIBILITY = "org"
SERVER_PORT = 8000
MACHINE_APP_DIR = "/home/machine/flashcard_app"


# ---------------------------------------------------------------------------
# Execution helper
# ---------------------------------------------------------------------------


def _run_and_wait(
    client, machine_id: str, command: list[str], timeout_ms: int = 600_000
) -> tuple[str, str]:
    """Execute a command on a machine and block until completion."""
    exc = client.machines.executions.create(
        machine_id=machine_id,
        command=command,
        timeout_ms=timeout_ms,
    )
    delay = 1.0
    while exc.status not in TERMINAL_STATUSES:
        wait = (
            (exc.retry_after_ms or 0) / 1000
            if exc.status == "wake_in_progress"
            else delay
        )
        time.sleep(wait)
        delay = min(delay * 1.5, 5.0)
        exc = client.machines.executions.retrieve(
            machine_id=machine_id,
            execution_id=exc.execution_id,
        )

    out = client.machines.executions.output(
        machine_id=machine_id,
        execution_id=exc.execution_id,
    )
    stdout = out.stdout or ""
    stderr = out.stderr or ""

    if exc.status != "succeeded":
        raise RuntimeError(
            f"Command failed on machine {machine_id}\n"
            f"Status: {exc.status}\n"
            f"Error: {exc.error_code}: {exc.error_message}\n"
            f"Stdout: {stdout[:2000]}\n"
            f"Stderr: {stderr[:2000]}"
        )

    return stdout, stderr


# ---------------------------------------------------------------------------
# Server code
# ---------------------------------------------------------------------------


def _get_server_code() -> str:
    """Read the FastAPI server code and inject the system prompt from system_prompts/."""
    here = Path(__file__).parent
    server_code = (here / "server_setup" / "server.py").read_text(encoding="utf-8")
    system_prompt = (here / "system_prompts" / "system_prompt.md").read_text(encoding="utf-8")
    return server_code.replace("SYSTEM_PROMPT_PLACEHOLDER", system_prompt)


def _get_setup_script() -> str:
    """Read the VM setup script."""
    here = Path(__file__).parent / "server_setup"
    return (here / "setup.sh").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Machine lifecycle
# ---------------------------------------------------------------------------


def _machine_status_attr(obj, attr: str, default=""):
    return getattr(getattr(obj, "status", None), attr, default)


def _create_machine_and_wait(client) -> str:
    """Create a new Dedalus machine and wait for it to be running.

    Returns machine_id.
    """
    print("[flashcard] Creating new Dedalus machine...")
    machine = client.machines.create(**VM_SPECS)
    machine_id = machine.machine_id
    print(f"[flashcard] Created: {machine_id}")

    print("[flashcard] Waiting for machine to be running...")
    for attempt in range(300):
        m = client.machines.retrieve(machine_id=machine_id)
        phase = _machine_status_attr(m, "phase", "unknown")
        if phase == "running":
            print("[flashcard] Machine is running!")
            time.sleep(10)
            return machine_id
        time.sleep(1)

    raise RuntimeError(f"Machine {machine_id} did not reach running state within 300s")


def _delete_machine(client, machine_id: str) -> None:
    """Delete a machine, ignoring errors."""
    try:
        m = client.machines.retrieve(machine_id=machine_id)
        rev = _machine_status_attr(m, "revision")
        client.machines.delete(
            machine_id=machine_id,
            extra_headers={"If-Match": rev},
        )
        print(f"[flashcard] Deleted machine {machine_id}")
    except Exception as exc:
        print(f"[flashcard] Warning: could not delete machine: {exc}")


# ---------------------------------------------------------------------------
# Server setup on VM
# ---------------------------------------------------------------------------


def _setup_server(client, machine_id: str, api_key: str):
    """Write server code and setup script to VM, then run setup."""
    print("[flashcard] Setting up server on VM...")

    print("[flashcard] Creating app directory...")
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c", f"mkdir -p {MACHINE_APP_DIR}"],
        timeout_ms=10_000,
    )

    print("[flashcard] Writing server.py...")
    server_code = _get_server_code()
    encoded = base64.b64encode(server_code.encode("utf-8")).decode("ascii")
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c", f"echo '{encoded}' | base64 -d > {MACHINE_APP_DIR}/server.py"],
        timeout_ms=30_000,
    )

    print("[flashcard] Writing setup script...")
    setup_script = _get_setup_script()
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c", f"cat > {MACHINE_APP_DIR}/setup.sh << 'EOF'\n{setup_script}\nEOF\nchmod +x {MACHINE_APP_DIR}/setup.sh"],
        timeout_ms=30_000,
    )

    print("[flashcard] Running setup script...")
    stdout, stderr = _run_and_wait(
        client, machine_id,
        [
            "/bin/bash",
            "-c",
            f"export DEDALUS_API_KEY='{api_key}' && bash {MACHINE_APP_DIR}/setup.sh",
        ],
        timeout_ms=600_000,
    )
    print("[flashcard] Setup complete")
    if stdout.strip():
        print(f"[flashcard] Setup output:\n{stdout}")


# ---------------------------------------------------------------------------
# Preview management
# ---------------------------------------------------------------------------


def _create_and_wait_preview(client, machine_id: str) -> tuple[str, str]:
    """Create a preview and wait for it to be ready.

    Returns (preview_url, preview_id).
    """
    print("[flashcard] Creating HTTPS preview (org visibility)...")
    preview = client.machines.previews.create(
        machine_id=machine_id,
        port=SERVER_PORT,
        protocol="https",
        visibility=PREVIEW_VISIBILITY,
    )
    preview_id = preview.preview_id
    preview_url = preview.url
    print(f"[flashcard] Preview created: {preview_url}")

    print("[flashcard] Waiting for preview to be ready...")
    for attempt in range(60):
        p = client.machines.previews.retrieve(
            machine_id=machine_id,
            preview_id=preview_id,
        )
        if p.status == "ready":
            print("[flashcard] Preview is ready!")
            return preview_url, preview_id
        elif p.status in ("failed", "expired", "closed"):
            raise RuntimeError(f"Preview status: {p.status}")
        time.sleep(1)
    else:
        raise RuntimeError("Preview did not become ready within 60s")


# ---------------------------------------------------------------------------
# Main flashcard generation
# ---------------------------------------------------------------------------


def generate_flashcards(note_path: str, note_content: str) -> dict:
    """Generate an Anki flashcard deck from note content using a fresh Dedalus machine.

    Always creates a new machine, always deletes it after use (success or failure).

    Returns:
        {
            "filepath": str,
            "filename": str,
            "size_bytes": int,
            "machine_id": str,
        }
    """
    api_key = _load_api_key()
    client = _get_dedalus_client()

    machine_id = _create_machine_and_wait(client)

    try:
        _setup_server(client, machine_id, api_key)

        preview_url, preview_id = _create_and_wait_preview(client, machine_id)

        print(f"[flashcard] Sending note ({len(note_content)} chars)...")
        max_retries = 8
        base_delay = 1.0
        last_error = None

        for attempt in range(max_retries):
            try:
                response = httpx.post(
                    f"{preview_url}/make-flashcard",
                    json={"note_content": note_content},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=120.0,
                )
                response.raise_for_status()
                break
            except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status not in (500, 502, 503, 504):
                        raise
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[flashcard] Retrying in {delay:.0f}s ({attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                    last_error = exc
                    continue
                raise
        else:
            raise last_error or RuntimeError("Failed after all retries")

        print(f"[flashcard] Received .apkg ({len(response.content)} bytes)")

        agent_dir = Path(_ensure_vault_path())
        flashcards_dir = agent_dir / "flashcards"
        flashcards_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(note_path).stem
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{timestamp}.apkg"
        filepath = flashcards_dir / filename

        filepath.write_bytes(response.content)
        print(f"[flashcard] Saved to: {filepath}")

        return {
            "filepath": str(filepath),
            "filename": filename,
            "size_bytes": len(response.content),
            "machine_id": machine_id,
        }

    except Exception as exc:
        print(f"[flashcard] Flashcard generation failed: {exc}")
        try:
            print("[flashcard] Fetching server log for debugging...")
            log_stdout, log_stderr = _run_and_wait(
                client, machine_id,
                ["/bin/bash", "-c", f"cat {MACHINE_APP_DIR}/server.log 2>/dev/null | tail -100 || echo '(no log file)'"],
                timeout_ms=30_000,
            )
            if log_stdout.strip():
                print(f"[flashcard] Server log:\n{log_stdout}")
            if log_stderr.strip():
                print(f"[flashcard] Server log stderr:\n{log_stderr}")
        except Exception as log_exc:
            print(f"[flashcard] Could not fetch server log: {log_exc}")
        raise

    finally:
        print(f"[flashcard] Deleting machine {machine_id}...")
        _delete_machine(client, machine_id)
