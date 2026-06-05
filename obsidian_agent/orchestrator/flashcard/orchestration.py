"""Flashcard orchestrator using a persistent Dedalus Machine with autosleep.

Flow:
  First call:        Create machine (autosleep=30m) → setup server → generate deck
  Warm reuse:        Machine still running → generate deck immediately
  After idle window: Dedalus auto-slept the VM → wake → generate deck
  Machine gone:      Fall back to create + setup

The machine_id is persisted in .machine_state.json next to this module so
the VM can be reused across processes. Dedalus auto-sleeps the VM after
AUTOSLEEP_WINDOW of inactivity, so no explicit sleep call is needed.

Machine specs: 1 vCPU / 2 GiB / 5 GiB
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
from dedalus_sdk import Dedalus

from obsidian_agent.orchestrator.utility import _ensure_vault_path, _load_api_key


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}
DEAD_PHASES = {"destroyed", "failed"}
AUTOSLEEP_WINDOW = "30m"
AUTOSLEEP_SECONDS = 1800
VM_SPECS = {
    "vcpu": 1,
    "memory_mib": 2048,
    "storage_gib": 5,
    "autosleep": AUTOSLEEP_WINDOW,
}
PREVIEW_VISIBILITY = "org"
SERVER_PORT = 8000
MACHINE_APP_DIR = "/home/machine/flashcard_app"
WAKE_TIMEOUT_S = 600
CREATE_TIMEOUT_S = 300
POLL_INTERVAL_S = 2
STATE_FILE = Path(__file__).parent / ".machine_state.json"


def _phase(m) -> str:
    """Pull the phase off a machine response, defaulting to 'unknown'."""
    return getattr(getattr(m, "status", None), "phase", "unknown")


def _run_and_wait(
    client, machine_id: str, command: list[str], timeout_ms: int = 600_000,
    _retries: int = 3,
) -> tuple[str, str]:
    """Execute a command on a machine and block until completion.

    Retries up to _retries times on execution_runner_interrupted, which is a
    transient Dedalus platform error (execution controller restarted mid-run).
    """
    for attempt in range(_retries + 1):
        exc = client.machines.executions.create(
            machine_id=machine_id,
            command=command,
            timeout_ms=timeout_ms,
        )
        while exc.status not in TERMINAL_STATUSES:
            wait = (
                (exc.retry_after_ms or 0) / 1000
                if exc.status == "wake_in_progress"
                else POLL_INTERVAL_S
            )
            time.sleep(wait)
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

        if exc.status == "succeeded":
            return stdout, stderr

        if exc.error_code == "execution_runner_interrupted" and attempt < _retries:
            wait_s = 10 * (attempt + 1)
            print(
                f"execution_runner_interrupted — retrying in {wait_s}s "
                f"(attempt {attempt + 1}/{_retries})..."
            )
            time.sleep(wait_s)
            continue

        raise RuntimeError(
            f"Command failed on machine {machine_id}\n"
            f"Status: {exc.status}\n"
            f"Error: {exc.error_code}: {exc.error_message}\n"
            f"Stdout: {stdout[:2000]}\n"
            f"Stderr: {stderr[:2000]}"
        )

    raise RuntimeError("Unreachable")


def _run_setup(client, machine_id: str, api_key: str) -> None:
    """Write server.py + setup.sh onto the VM and run setup.sh.

    Idempotent: rerunning is safe — setup.sh reinstalls the venv, rewrites the
    systemd unit, and `systemctl enable --now` is a no-op on the second run.
    Called both on fresh machine creation and on wake if the app dir was wiped.
    """
    here = Path(__file__).parent
    server_code = (here / "server_setup" / "server.py").read_text(encoding="utf-8")
    system_prompt = (here / "system_prompts" / "system_prompt.md").read_text(encoding="utf-8")
    server_code = server_code.replace("SYSTEM_PROMPT_PLACEHOLDER", system_prompt)
    setup_script = (here / "server_setup" / "setup.sh").read_text(encoding="utf-8")

    print("Creating app directory...")
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c", f"mkdir -p {MACHINE_APP_DIR}"],
        timeout_ms=10_000,
    )

    print("Writing server.py...")
    encoded = base64.b64encode(server_code.encode("utf-8")).decode("ascii")
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c", f"echo '{encoded}' | base64 -d > {MACHINE_APP_DIR}/server.py"],
        timeout_ms=30_000,
    )

    print("Writing setup script...")
    # base64 the wire format — setup.sh contains its own heredocs (systemd unit
    # file), and a naive `cat > … << EOF` wrapper terminates at the FIRST EOF.
    setup_encoded = base64.b64encode(setup_script.encode("utf-8")).decode("ascii")
    _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c",
         f"echo '{setup_encoded}' | base64 -d > {MACHINE_APP_DIR}/setup.sh && "
         f"chmod +x {MACHINE_APP_DIR}/setup.sh"],
        timeout_ms=30_000,
    )

    print("Running setup script (this may take a few minutes)...")
    setup_stdout, _ = _run_and_wait(
        client, machine_id,
        ["/bin/bash", "-c",
         f"export DEDALUS_API_KEY='{api_key}' && bash {MACHINE_APP_DIR}/setup.sh"],
        timeout_ms=600_000,
    )
    print("Setup complete")
    if setup_stdout.strip():
        print(f"Setup output:\n{setup_stdout}")


def generate_flashcards(note_path: str, note_content: str) -> dict:
    """Generate an Anki deck using a persistent Dedalus machine with autosleep.

    First call:        create VM, run setup (~2-3 min), generate deck.
    Warm reuse:        machine still running, generate deck immediately.
    After idle window: Dedalus auto-slept the VM; wake, generate deck.
    """
    api_key = _load_api_key()
    client = Dedalus(api_key=api_key)

    # 1. Find a usable VM: reuse persisted one, wake it, or build a fresh one.
    machine_id: str | None = None
    is_new = False

    try:
        persisted_id = json.loads(STATE_FILE.read_text())["machine_id"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        persisted_id = None

    if persisted_id:
        print(f"Found persisted machine: {persisted_id}")
        try:
            m = client.machines.retrieve(machine_id=persisted_id)
            phase = _phase(m)
            print(f"Machine phase: {phase}")

            if phase == "sleeping":
                print(f"Waking machine {persisted_id}...")
                client.machines.wake(machine_id=persisted_id)
                deadline = time.time() + WAKE_TIMEOUT_S
                while time.time() < deadline:
                    m = client.machines.retrieve(machine_id=persisted_id)
                    phase = _phase(m)
                    if phase == "running":
                        print("Confirmed: phase='running'")
                        break
                    if phase in DEAD_PHASES:
                        raise RuntimeError(
                            f"Machine {persisted_id} reached terminal phase "
                            f"'{phase}' while waking"
                        )
                    time.sleep(POLL_INTERVAL_S)
                else:
                    raise RuntimeError(
                        f"Timed out after {WAKE_TIMEOUT_S}s waking {persisted_id}"
                    )
                time.sleep(5)  # let OS services settle
                m = client.machines.retrieve(machine_id=persisted_id)

            if phase == "running":
                if getattr(m, "autosleep_seconds", 0) != AUTOSLEEP_SECONDS:
                    try:
                        client.machines.update(
                            machine_id=persisted_id, autosleep=AUTOSLEEP_WINDOW
                        )
                        print(f"Updated autosleep → {AUTOSLEEP_WINDOW}")
                    except Exception as exc:
                        print(f"Could not update autosleep: {exc}")
                machine_id = persisted_id
            else:
                print(f"Unusable phase '{phase}' — creating new machine")
                STATE_FILE.unlink(missing_ok=True)
        except Exception as exc:
            print(f"Could not retrieve machine ({exc}) — creating new machine")
            STATE_FILE.unlink(missing_ok=True)

    if machine_id is None:
        is_new = True

        print("Creating new Dedalus machine...")
        machine = client.machines.create(**VM_SPECS)
        machine_id = machine.machine_id
        print(f"Created: {machine_id}")

        print("Waiting for machine to be running...")
        deadline = time.time() + CREATE_TIMEOUT_S
        while time.time() < deadline:
            m = client.machines.retrieve(machine_id=machine_id)
            phase = _phase(m)
            if phase == "running":
                print("Machine is running!")
                time.sleep(10)  # let boot finish
                break
            if phase in DEAD_PHASES:
                raise RuntimeError(
                    f"Machine {machine_id} reached terminal phase '{phase}' while creating"
                )
            time.sleep(POLL_INTERVAL_S)
        else:
            raise RuntimeError(
                f"Machine {machine_id} did not reach running state within {CREATE_TIMEOUT_S}s"
            )

        print("Setting up server on VM...")
        _run_setup(client, machine_id, api_key)

        STATE_FILE.write_text(json.dumps({"machine_id": machine_id}))
        print(f"State saved: {machine_id}")

    # Steps 2-5 share a try/except that tails server.log from the VM on failure.
    try:
        # 2. Open a preview URL on the VM (reuse a ready one or create + poll).
        page = client.machines.previews.list(machine_id=machine_id)
        if page is None:
            previews = []
        elif hasattr(page, "items"):
            previews = page.items or []
        else:
            previews = list(page) if page else []

        preview_url: str | None = None
        for p in previews:
            if getattr(p, "status", None) == "ready":
                preview_url = p.url
                print(f"Reusing preview: {preview_url}")
                break

        if preview_url is None:
            print("Creating new preview...")
            preview = client.machines.previews.create(
                machine_id=machine_id,
                port=SERVER_PORT,
                protocol="https",
                visibility=PREVIEW_VISIBILITY,
            )
            preview_id = preview.preview_id
            deadline = time.time() + 90
            while time.time() < deadline:
                p = client.machines.previews.retrieve(
                    machine_id=machine_id, preview_id=preview_id
                )
                status = getattr(p, "status", None)
                if status == "ready":
                    preview_url = p.url
                    print(f"Preview ready: {preview_url}")
                    break
                if status in ("failed", "expired", "closed"):
                    raise RuntimeError(f"Preview entered terminal status: {status}")
                retry_ms = getattr(p, "retry_after_ms", None)
                time.sleep((retry_ms / 1000) if retry_ms else POLL_INTERVAL_S)
            else:
                raise RuntimeError("Preview did not become ready within 90s")

        # 3. uvicorn runs as flashcard-server.service — systemd starts it on
        # every VM boot (including post-wake). If the app dir is missing
        # (rare: a Dedalus-side reset, or a machine we inherited without
        # setup), re-run setup.sh; otherwise the POST below hits the
        # already-listening service.
        check_out, _ = _run_and_wait(
            client, machine_id,
            ["/bin/bash", "-c",
             f"test -x {MACHINE_APP_DIR}/.venv/bin/python && echo OK || echo MISSING"],
            timeout_ms=10_000,
        )
        if "MISSING" in check_out:
            print("App directory missing — re-running setup.sh (~3 min)...")
            _run_setup(client, machine_id, api_key)

        # 4. POST the note. Tight retries on transient errors (ConnectError +
        # gateway 502/503/504 while uvicorn warms up); conservative exponential
        # on 500 (real server-side bug).
        print(f"Sending note ({len(note_content)} chars)...")
        TRANSIENT_5XX = {502, 503, 504}
        WARMUP_RETRY_DELAY_S = 1.0
        MAX_WARMUP_RETRIES = 60
        MAX_500_RETRIES = 5
        warmup_attempts = 0
        http500_attempts = 0
        response = None

        while True:
            try:
                response = httpx.post(
                    f"{preview_url}/make-flashcard",
                    json={"note_content": note_content},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=120.0,
                )
                response.raise_for_status()
                break
            except httpx.ConnectError:
                warmup_attempts += 1
                if warmup_attempts == 1:
                    print("Waiting for uvicorn to accept connections...")
                if warmup_attempts >= MAX_WARMUP_RETRIES:
                    raise
                time.sleep(WARMUP_RETRY_DELAY_S)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in TRANSIENT_5XX:
                    warmup_attempts += 1
                    if warmup_attempts == 1:
                        print(f"Gateway returned {code} — waiting for upstream to come up...")
                    if warmup_attempts >= MAX_WARMUP_RETRIES:
                        raise
                    time.sleep(WARMUP_RETRY_DELAY_S)
                elif code == 500:
                    http500_attempts += 1
                    if http500_attempts >= MAX_500_RETRIES:
                        raise
                    delay = 2.0 * (2 ** (http500_attempts - 1))
                    print(
                        f"Server returned 500 — retrying in {delay:.0f}s "
                        f"({http500_attempts}/{MAX_500_RETRIES})..."
                    )
                    time.sleep(delay)
                else:
                    raise

        # 5. Save the returned deck to the vault and return metadata.
        print(f"Received .apkg ({len(response.content)} bytes)")

        agent_dir = Path(_ensure_vault_path())
        output_dir = agent_dir / "flashcards"
        output_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(note_path).stem
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{timestamp}.apkg"
        filepath = output_dir / filename
        filepath.write_bytes(response.content)
        print(f"Saved to: {filepath}")

        return {
            "filepath": str(filepath),
            "filename": filename,
            "size_bytes": len(response.content),
            "machine_id": machine_id,
            "reused_machine": not is_new,
        }

    except Exception as exc:
        print(f"Flashcard generation failed: {exc}")
        try:
            log_stdout, log_stderr = _run_and_wait(
                client, machine_id,
                ["/bin/bash", "-c",
                 f"cat {MACHINE_APP_DIR}/server.log 2>/dev/null | tail -100 || echo '(no log file)'"],
                timeout_ms=30_000,
            )
            if log_stdout.strip():
                print(f"Server log:\n{log_stdout}")
            if log_stderr.strip():
                print(f"Server log stderr:\n{log_stderr}")
        except Exception as log_exc:
            print(f"Could not fetch server log: {log_exc}")
        raise
