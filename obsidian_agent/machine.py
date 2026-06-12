"""Single owner of the Dedalus machine lifecycle.

Every machine this project runs is described by a MachineSpec and lives one
of two lifecycles:

  * persistent (podcast, utility) — managed through ensure_machine(): find
    the persisted machine, wake it if Dedalus auto-slept it, create +
    bootstrap a fresh one if it is gone, keep its deployed files in sync,
    and hand back a ready preview URL. Machine ids persist in machines.json
    next to this module so VMs are reused across processes.
  * ephemeral (flashcard) — managed through ephemeral_machine(): created per
    job, destroyed afterwards, never registered. The disposable sandbox for
    running untrusted model-generated code.

Each machine folder contributes its entire server_setup/ directory as a
self-contained bundle — all lifecycle logic lives here. Lifecycle steps are
also reported into the machine state surface (_emit / machine_status) so the
TUI can show machines waking, syncing, and being destroyed.

Dedalus auto-sleeps VMs after their autosleep window; executions and preview
hits count as activity, so polling a machine's job endpoint keeps it awake
for the duration of a job.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import io
import json
import tarfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import httpx
from dedalus_sdk import Dedalus

from obsidian_agent.config import _load_api_key

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}
DEAD_PHASES = {"destroyed", "failed"}
POLL_INTERVAL_S = 2
PREVIEW_VISIBILITY = "org"
MACHINE_VAULT_ROOT = "/home/machine/vault"

REGISTRY_FILE = Path(__file__).parent / "machines.json"
_ORCH = Path(__file__).parent / "orchestrator"

# Names excluded from server_setup/ bundles.
_BUNDLE_EXCLUDE = {"__pycache__", ".DS_Store"}


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineSpec:
    name: str                  # registry key: "podcast" | "flashcard" | "utility"
    server_dir: Path           # server_setup/ folder, deployed in its entirety
    app_dir: str               # install location on the machine
    vcpu: int
    memory_mib: int
    storage_gib: int
    persistent: bool = True    # False: per-job ephemeral_machine(), never registered
    autosleep: str = "30m"
    autosleep_seconds: int = 1800
    port: int = 8000
    service_name: str = ""     # defaults to f"{name}-server"
    wake_timeout_s: int = 600
    create_timeout_s: int = 300
    setup_timeout_ms: int = 600_000
    request_timeout_s: float = 900.0
    env: tuple[tuple[str, str], ...] = ()            # exported when running setup.sh

    @property
    def service(self) -> str:
        return self.service_name or f"{self.name}-server"


@dataclass
class MachineHandle:
    spec: MachineSpec
    machine_id: str
    base_url: str
    client: Dedalus
    api_key: str
    reused: bool


MACHINES: dict[str, MachineSpec] = {
    "podcast": MachineSpec(
        name="podcast",
        server_dir=_ORCH / "podcast" / "server_setup",
        app_dir="/home/machine/podcast_app",
        vcpu=2,
        memory_mib=4096,
        storage_gib=10,
        wake_timeout_s=900,  # loaded VMs (10GB Kokoro/venv) wake slowly from snapshot
        setup_timeout_ms=1_200_000,
        request_timeout_s=900.0,
    ),
    "flashcard": MachineSpec(
        name="flashcard",
        server_dir=_ORCH / "flashcard" / "server_setup",
        app_dir="/home/machine/flashcard_app",
        vcpu=1,
        memory_mib=2048,
        storage_gib=5,
        # The sandbox story: this machine runs model-generated Python, so it
        # is created per job and destroyed after — nothing persists, nothing
        # is reused, untrusted code has no lasting blast radius.
        persistent=False,
        request_timeout_s=120.0,
    ),
    "utility": MachineSpec(
        name="utility",
        server_dir=_ORCH / "utility" / "server_setup",
        app_dir="/home/machine/utility_app",
        vcpu=1,
        memory_mib=2048,
        storage_gib=5,
        # Short wake budget on purpose: this machine's state is fully
        # recoverable (bundle redeploys, vault re-syncs from local), so when
        # a wake stalls it is faster to give up and recreate than to wait.
        wake_timeout_s=240,
        setup_timeout_ms=900_000,
        request_timeout_s=120.0,
        env=(("OBSIDIAN_VAULT_PATH", MACHINE_VAULT_ROOT),),
    ),
}

def get_spec(name: str) -> MachineSpec:
    return MACHINES[name]


# ---------------------------------------------------------------------------
# Registry (machines.json)
# ---------------------------------------------------------------------------
#
# No locking needed: the backend runs exactly one job at a time (gate in
# api/utils.py) and DELETE /machines is rejected while a job runs, so the
# registry is only ever touched by one worker.


def _registry_load() -> dict:
    try:
        reg = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        reg = {}
    reg.setdefault("version", 1)
    reg.setdefault("machines", {})
    return reg


def _registry_save(reg: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _registry_get(name: str) -> dict:
    return dict(_registry_load()["machines"].get(name) or {})


def _registry_set(name: str, machine_id: str, bundle_sha256: str | None) -> None:
    reg = _registry_load()
    reg["machines"][name] = {
        "machine_id": machine_id,
        "bundle_sha256": bundle_sha256,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _registry_save(reg)


def forget_machine(name: str) -> None:
    reg = _registry_load()
    reg["machines"].pop(name, None)
    _registry_save(reg)


# ---------------------------------------------------------------------------
# Machine state surface
# ---------------------------------------------------------------------------
#
# Every lifecycle step reports into this in-process store via _emit(). The
# local API exposes it as GET /machines and the TUI renders it — the point of
# the showcase is *seeing* machines wake from snapshots, sync, and get
# destroyed, not just receiving their outputs.

# The ONE lock in this codebase: the active job's worker thread writes here
# (via _emit/_set_state) while the API's event-loop thread reads machine_status()
# for GET /machines. Everything else is serialized by the single-job gate.
_STATE_LOCK = threading.Lock()
_machine_state: dict[str, dict] = {}
_machine_events: deque = deque(maxlen=60)


def _emit(name: str, event: str, **fields) -> None:
    """Record a lifecycle event and merge fields into the machine's state."""
    now = time.time()
    with _STATE_LOCK:
        state = _machine_state.setdefault(name, {})
        state.update(fields)
        state["last_event"] = event
        state["last_event_ts"] = now
        _machine_events.appendleft({"ts": now, "machine": name, "event": event})


def _set_state(name: str, **fields) -> None:
    """Update state quietly (no event) — for periodic phase refreshes."""
    with _STATE_LOCK:
        _machine_state.setdefault(name, {}).update(fields)


def machine_status() -> dict:
    """Snapshot of every machine for the local API / TUI machines panel."""
    with _STATE_LOCK:
        state = {name: dict(st) for name, st in _machine_state.items()}
        events = list(_machine_events)
    machines = []
    for name, spec in MACHINES.items():
        st = state.get(name, {})
        machine_id = st.get("machine_id") or _registry_get(name).get("machine_id")
        default_phase = "registered" if machine_id else "none"
        machines.append({
            "name": name,
            "lifecycle": "persistent" if spec.persistent else "ephemeral",
            "resources": f"{spec.vcpu} vCPU · {spec.memory_mib} MiB · {spec.storage_gib} GiB",
            "autosleep": spec.autosleep,
            "machine_id": machine_id,
            "phase": st.get("phase") or default_phase,
            "last_event": st.get("last_event"),
            "last_event_ts": st.get("last_event_ts"),
            "wake_seconds": st.get("wake_seconds"),
            "sync": st.get("sync"),
        })
    return {"machines": machines, "events": events}


_last_phase_refresh = 0.0


def refresh_machine_phases(max_age_s: float = 60.0) -> None:
    """Best-effort refresh of real Dedalus phases for persistent machines.

    Rate-limited so the TUI can poll machine_status() freely without every
    poll turning into Dedalus API calls. Lets the panel show autosleep
    transitions (running → sleeping) without any job running.
    """
    global _last_phase_refresh
    if time.time() - _last_phase_refresh < max_age_s:
        return
    _last_phase_refresh = time.time()

    try:
        client = Dedalus(api_key=_load_api_key())
    except Exception:
        return
    for name, spec in MACHINES.items():
        if not spec.persistent:
            continue
        machine_id = _registry_get(name).get("machine_id")
        if not machine_id:
            continue
        try:
            phase = _phase(client.machines.retrieve(machine_id=machine_id))
        except Exception:
            continue
        with _STATE_LOCK:
            prev = _machine_state.setdefault(name, {}).get("phase")
        if phase != prev:
            _emit(name, f"phase → {phase}", phase=phase, machine_id=machine_id)
        else:
            _set_state(name, phase=phase, machine_id=machine_id)


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


def _phase(m) -> str:
    """Pull the phase off a machine response, defaulting to 'unknown'."""
    return getattr(getattr(m, "status", None), "phase", "unknown")


def run_command(
    client: Dedalus,
    machine_id: str,
    command: list[str],
    *,
    timeout_ms: int = 600_000,
    retries: int = 3,
    stdin: str | None = None,
    log: Callable[[str], None] = print,
) -> tuple[str, str]:
    """Execute a command on a machine and block until completion.

    Retries up to `retries` times on execution_runner_interrupted, which is a
    transient Dedalus platform error (execution controller restarted mid-run).
    """
    for attempt in range(retries + 1):
        kwargs: dict = {
            "machine_id": machine_id,
            "command": command,
            "timeout_ms": timeout_ms,
        }
        if stdin is not None:
            kwargs["stdin"] = stdin
        exc = client.machines.executions.create(**kwargs)
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

        if exc.error_code == "execution_runner_interrupted" and attempt < retries:
            wait_s = 10 * (attempt + 1)
            log(
                f"execution_runner_interrupted — retrying in {wait_s}s "
                f"(attempt {attempt + 1}/{retries})..."
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


# ---------------------------------------------------------------------------
# Bundle build + deploy
# ---------------------------------------------------------------------------


def _build_bundle(spec: MachineSpec) -> tuple[bytes, str]:
    """Build the deploy bundle as a deterministic tar.gz; returns (bytes, sha256).

    The bundle is the machine's entire server_setup/ directory. Deterministic
    (sorted entries, zeroed mtimes/owners) so the sha is stable across runs —
    the registry compares it to decide whether a machine's files are stale.
    """
    entries: dict[str, bytes] = {}
    for path in sorted(spec.server_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(spec.server_dir)
        if any(part in _BUNDLE_EXCLUDE for part in rel.parts):
            continue
        entries[rel.as_posix()] = path.read_bytes()
    for required in ("server.py", "setup.sh"):
        if required not in entries:
            raise RuntimeError(f"{spec.server_dir} must contain {required}")

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for arcname in sorted(entries):
            data = entries[arcname]
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if arcname.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    bundle = buf.getvalue()
    return bundle, hashlib.sha256(bundle).hexdigest()


def _upload_bytes(
    client: Dedalus,
    machine_id: str,
    data: bytes,
    remote_path: str,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Upload bytes to the machine via execution stdin, then verify by sha256."""
    encoded = base64.b64encode(data).decode("ascii")
    run_command(
        client, machine_id,
        ["/bin/bash", "-c", f"base64 -d > {remote_path}"],
        stdin=encoded,
        timeout_ms=120_000,
        log=log,
    )
    expected = hashlib.sha256(data).hexdigest()
    out, _ = run_command(
        client, machine_id,
        ["/bin/bash", "-c", f"sha256sum {remote_path} | cut -d' ' -f1"],
        timeout_ms=30_000,
        log=log,
    )
    if out.strip() != expected:
        raise RuntimeError(
            f"Upload corrupted: sha256 mismatch for {remote_path} "
            f"(expected {expected}, got {out.strip()!r})"
        )


def _deploy_and_setup(
    client: Dedalus,
    machine_id: str,
    spec: MachineSpec,
    api_key: str,
    *,
    full_setup: bool,
    log: Callable[[str], None] = print,
) -> str:
    """Upload + extract the bundle; run setup.sh (full) or restart the service.

    Idempotent: setup.sh reinstalls the venv, rewrites the systemd unit, and
    `systemctl enable --now` is a no-op on the second run.
    Returns the deployed bundle's sha256 for the registry.
    """
    bundle, sha = _build_bundle(spec)
    log(f"Deploying bundle ({len(bundle)} bytes) to {spec.app_dir}...")
    run_command(
        client, machine_id,
        ["/bin/bash", "-c", f"mkdir -p {spec.app_dir}"],
        timeout_ms=10_000, log=log,
    )
    _upload_bytes(client, machine_id, bundle, f"{spec.app_dir}/bundle.tgz", log=log)
    run_command(
        client, machine_id,
        ["/bin/bash", "-c",
         f"tar -xzf {spec.app_dir}/bundle.tgz -C {spec.app_dir} && "
         f"rm {spec.app_dir}/bundle.tgz && chmod +x {spec.app_dir}/setup.sh"],
        timeout_ms=60_000, log=log,
    )

    if full_setup:
        log("Running setup script (this may take a few minutes)...")
        exports = " && ".join(
            f"export {k}='{v}'"
            for k, v in (("DEDALUS_API_KEY", api_key), *spec.env)
        )
        setup_stdout, _ = run_command(
            client, machine_id,
            ["/bin/bash", "-c", f"{exports} && bash {spec.app_dir}/setup.sh"],
            timeout_ms=spec.setup_timeout_ms, log=log,
        )
        log("Setup complete")
        if setup_stdout.strip():
            log(f"Setup output:\n{setup_stdout}")
    else:
        log(f"Restarting {spec.service} to pick up refreshed files...")
        run_command(
            client, machine_id,
            ["/bin/bash", "-c", f"systemctl restart {spec.service}"],
            timeout_ms=180_000, log=log,
        )
        # A cold restart right after a wake has to page the venv back in from
        # the storage snapshot, which can take minutes — wait for health here
        # so callers always get a listening server.
        log("Waiting for server health check...")
        run_command(
            client, machine_id,
            ["/bin/bash", "-c",
             f"for i in $(seq 1 48); do "
             f"curl -sf -m 5 http://localhost:{spec.port}/health >/dev/null && exit 0; "
             f"sleep 5; done; echo 'server failed health check'; exit 1"],
            timeout_ms=300_000, log=log,
        )
    return sha


# ---------------------------------------------------------------------------
# ensure_machine
# ---------------------------------------------------------------------------


def _wait_for_phase(
    client: Dedalus,
    machine_id: str,
    timeout_s: int,
    *,
    action: str,
    log: Callable[[str], None],
) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        m = client.machines.retrieve(machine_id=machine_id)
        phase = _phase(m)
        if phase == "running":
            log("Confirmed: phase='running'")
            return
        if phase in DEAD_PHASES:
            raise RuntimeError(
                f"Machine {machine_id} reached terminal phase '{phase}' while {action}"
            )
        time.sleep(POLL_INTERVAL_S)
    raise RuntimeError(f"Timed out after {timeout_s}s {action} {machine_id}")


def _ensure_preview(
    client: Dedalus,
    machine_id: str,
    spec: MachineSpec,
    *,
    log: Callable[[str], None],
) -> str:
    """Reuse a ready preview URL on the machine, or create one and poll."""
    page = client.machines.previews.list(machine_id=machine_id)
    if page is None:
        previews = []
    elif hasattr(page, "items"):
        previews = page.items or []
    else:
        previews = list(page) if page else []

    for p in previews:
        if getattr(p, "status", None) == "ready":
            log(f"Reusing preview: {p.url}")
            return p.url

    log("Creating new preview...")
    preview = client.machines.previews.create(
        machine_id=machine_id,
        port=spec.port,
        protocol="https",
        visibility=PREVIEW_VISIBILITY,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        p = client.machines.previews.retrieve(
            machine_id=machine_id, preview_id=preview.preview_id
        )
        status = getattr(p, "status", None)
        if status == "ready":
            log(f"Preview ready: {p.url}")
            return p.url
        if status in ("failed", "expired", "closed"):
            raise RuntimeError(f"Preview entered terminal status: {status}")
        retry_ms = getattr(p, "retry_after_ms", None)
        time.sleep((retry_ms / 1000) if retry_ms else POLL_INTERVAL_S)
    raise RuntimeError("Preview did not become ready within 90s")


def ensure_machine(spec: MachineSpec, *, log: Callable[[str], None] = print) -> MachineHandle:
    """Return a handle to a running, healthy, up-to-date machine for `spec`.

    First call:        create VM, deploy bundle, run full setup.
    Warm reuse:        machine running — verify venv + bundle sha, refresh if stale.
    After idle window: Dedalus auto-slept the VM; wake it.
    Machine gone:      fall back to create + setup.
    """
    if not spec.persistent:
        raise RuntimeError(
            f"Machine '{spec.name}' is ephemeral — use ephemeral_machine() instead"
        )

    api_key = _load_api_key()
    client = Dedalus(api_key=api_key)

    try:
        return _ensure_machine_impl(spec, client, api_key, log=log)
    except Exception as exc:
        _emit(spec.name, f"error: {exc}", phase="error")
        raise


def _ensure_machine_impl(
    spec: MachineSpec,
    client: Dedalus,
    api_key: str,
    *,
    log: Callable[[str], None],
) -> MachineHandle:
    entry = _registry_get(spec.name)
    machine_id: str | None = entry.get("machine_id")
    bundle_sha: str | None = entry.get("bundle_sha256")

    # 1. Reuse the persisted machine if it is still alive.
    if machine_id:
        log(f"Found persisted machine: {machine_id}")
        try:
            m = client.machines.retrieve(machine_id=machine_id)
            phase = _phase(m)
            log(f"Machine phase: {phase}")
            _emit(spec.name, f"found {machine_id} ({phase})",
                  phase=phase, machine_id=machine_id)

            if phase == "sleeping":
                log(f"Waking machine {machine_id}...")
                _emit(spec.name, "waking from snapshot…", phase="waking")
                wake_t0 = time.monotonic()
                client.machines.wake(machine_id=machine_id)
                _wait_for_phase(client, machine_id, spec.wake_timeout_s,
                                action="waking", log=log)
                wake_s = round(time.monotonic() - wake_t0, 1)
                _emit(spec.name, f"woke in {wake_s:.0f}s",
                      phase="running", wake_seconds=wake_s)
                time.sleep(5)  # let OS services settle
                m = client.machines.retrieve(machine_id=machine_id)
                phase = _phase(m)

            if phase == "running":
                if getattr(m, "autosleep_seconds", 0) != spec.autosleep_seconds:
                    try:
                        client.machines.update(
                            machine_id=machine_id, autosleep=spec.autosleep
                        )
                        log(f"Updated autosleep → {spec.autosleep}")
                    except Exception as exc:
                        log(f"Could not update autosleep: {exc}")
            else:
                log(f"Unusable phase '{phase}' — creating new machine")
                _emit(spec.name, f"unusable phase '{phase}' — recreating")
                forget_machine(spec.name)
                machine_id = None
        except Exception as exc:
            log(f"Could not retrieve machine ({exc}) — creating new machine")
            _emit(spec.name, "machine gone — recreating")
            forget_machine(spec.name)
            machine_id = None

    # 2. Create + bootstrap a fresh machine if needed.
    reused = machine_id is not None
    if machine_id is None:
        log("Creating new Dedalus machine...")
        _emit(spec.name, "creating machine…", phase="creating", machine_id=None)
        machine = client.machines.create(
            vcpu=spec.vcpu,
            memory_mib=spec.memory_mib,
            storage_gib=spec.storage_gib,
            autosleep=spec.autosleep,
        )
        machine_id = machine.machine_id
        log(f"Created: {machine_id}")
        _emit(spec.name, f"created {machine_id}", machine_id=machine_id)

        log("Waiting for machine to be running...")
        _wait_for_phase(client, machine_id, spec.create_timeout_s,
                        action="creating", log=log)
        time.sleep(10)  # let boot finish

        log("Setting up server on VM...")
        _emit(spec.name, "deploying bundle + running setup…", phase="setup")
        bundle_sha = _deploy_and_setup(
            client, machine_id, spec, api_key, full_setup=True, log=log
        )
        _registry_set(spec.name, machine_id, bundle_sha)
    else:
        # 3. Health check: systemd starts the server on every boot, but if
        # the app dir was wiped (Dedalus-side reset, inherited machine),
        # re-run the full setup. Otherwise refresh files when the local
        # bundle differs from what was last deployed.
        check_out, _ = run_command(
            client, machine_id,
            ["/bin/bash", "-c",
             f"test -x {spec.app_dir}/.venv/bin/python && echo OK || echo MISSING"],
            timeout_ms=10_000, log=log,
        )
        if "MISSING" in check_out:
            log("App directory missing — re-running full setup...")
            _emit(spec.name, "app dir missing — full setup…", phase="setup")
            bundle_sha = _deploy_and_setup(
                client, machine_id, spec, api_key, full_setup=True, log=log
            )
            _registry_set(spec.name, machine_id, bundle_sha)
        else:
            _, current_sha = _build_bundle(spec)
            if current_sha != bundle_sha:
                log("Server files changed — refreshing bundle...")
                _emit(spec.name, "refreshing server files…", phase="refreshing")
                bundle_sha = _deploy_and_setup(
                    client, machine_id, spec, api_key, full_setup=False, log=log
                )
                _registry_set(spec.name, machine_id, bundle_sha)

    base_url = _ensure_preview(client, machine_id, spec, log=log)
    _emit(spec.name, "ready", phase="running", machine_id=machine_id)

    return MachineHandle(
        spec=spec,
        machine_id=machine_id,
        base_url=base_url,
        client=client,
        api_key=api_key,
        reused=reused,
    )


# ---------------------------------------------------------------------------
# Ephemeral machines
# ---------------------------------------------------------------------------


def _destroy_machine(client: Dedalus, machine_id: str) -> None:
    """Delete a machine (no-op if already destroyed)."""
    m = client.machines.retrieve(machine_id=machine_id)
    if _phase(m) == "destroyed":
        return
    rev = getattr(getattr(m, "status", None), "revision", "")
    client.machines.delete(machine_id=machine_id, extra_headers={"If-Match": rev})


def _retire_registry_machine(
    name: str, client: Dedalus, *, log: Callable[[str], None] = print
) -> None:
    """One-time migration: destroy + forget a machine persisted under the old
    ensure_machine lifecycle for a spec that is now ephemeral."""
    machine_id = _registry_get(name).get("machine_id")
    if not machine_id:
        return
    log(f"Retiring persisted {name} machine {machine_id} (now ephemeral)...")
    try:
        _destroy_machine(client, machine_id)
        log(f"Destroyed old machine {machine_id}")
    except Exception as exc:
        log(f"Could not destroy old machine {machine_id}: {exc}")
    forget_machine(name)
    _emit(name, f"retired persisted machine {machine_id}", machine_id=None)


@contextlib.contextmanager
def ephemeral_machine(
    spec: MachineSpec, *, log: Callable[[str], None] = print
) -> Iterator[MachineHandle]:
    """Create a throwaway machine for one job and destroy it afterwards.

    The sandbox lifecycle: the machine exists only for the duration of the
    `with` block — create, bootstrap, run the job, destroy. Nothing persists
    and nothing is reused, so untrusted code executed on it has no lasting
    blast radius. Never touches the registry.
    """
    api_key = _load_api_key()
    client = Dedalus(api_key=api_key)
    _retire_registry_machine(spec.name, client, log=log)

    log("Creating ephemeral sandbox machine...")
    _emit(spec.name, "creating ephemeral sandbox…", phase="creating", machine_id=None)
    try:
        machine = client.machines.create(
            vcpu=spec.vcpu,
            memory_mib=spec.memory_mib,
            storage_gib=spec.storage_gib,
            autosleep=spec.autosleep,
        )
    except Exception as exc:
        _emit(spec.name, f"error: {exc}", phase="error")
        raise
    machine_id = machine.machine_id
    log(f"Created: {machine_id}")
    _emit(spec.name, f"created {machine_id}", machine_id=machine_id)

    try:
        _wait_for_phase(client, machine_id, spec.create_timeout_s,
                        action="creating", log=log)
        time.sleep(10)  # let boot finish

        log("Setting up server on sandbox...")
        _emit(spec.name, "deploying bundle + running setup…", phase="setup")
        _deploy_and_setup(client, machine_id, spec, api_key, full_setup=True, log=log)

        base_url = _ensure_preview(client, machine_id, spec, log=log)
        _emit(spec.name, "ready", phase="running")
        yield MachineHandle(
            spec=spec,
            machine_id=machine_id,
            base_url=base_url,
            client=client,
            api_key=api_key,
            reused=False,
        )
    finally:
        log(f"Destroying ephemeral machine {machine_id}...")
        _emit(spec.name, f"destroying {machine_id}…", phase="destroying")
        try:
            _destroy_machine(client, machine_id)
            log("Sandbox destroyed — nothing persists")
            _emit(spec.name, "destroyed — nothing persists",
                  phase="destroyed", machine_id=None)
        except Exception as exc:
            log(f"WARNING: could not destroy {machine_id}: {exc}")
            _emit(spec.name, f"destroy failed: {exc}", phase="error")


# ---------------------------------------------------------------------------
# HTTP to the machine's server
# ---------------------------------------------------------------------------


def request(
    handle: MachineHandle,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    data: dict | None = None,
    files: list | None = None,
    timeout: float | None = None,
    log: Callable[[str], None] = print,
) -> httpx.Response:
    """HTTP request to the machine's server with the warmup/5xx retry ladder.

    Tight retries on transient errors (ConnectError + gateway 502/503/504
    while uvicorn warms up); conservative exponential on 500 (real bug).
    """
    url = f"{handle.base_url}{path}"
    request_timeout = timeout if timeout is not None else handle.spec.request_timeout_s

    TRANSIENT_5XX = {502, 503, 504}
    WARMUP_RETRY_DELAY_S = 1.0
    MAX_WARMUP_RETRIES = 60
    MAX_500_RETRIES = 5
    warmup_attempts = 0
    http500_attempts = 0

    while True:
        try:
            response = httpx.request(
                method, url,
                json=json_body,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {handle.api_key}"},
                timeout=request_timeout,
            )
            response.raise_for_status()
            return response
        except httpx.ConnectError:
            warmup_attempts += 1
            if warmup_attempts == 1:
                log("Waiting for uvicorn to accept connections...")
            if warmup_attempts >= MAX_WARMUP_RETRIES:
                raise
            time.sleep(WARMUP_RETRY_DELAY_S)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in TRANSIENT_5XX:
                warmup_attempts += 1
                if warmup_attempts == 1:
                    log(f"Gateway returned {code} — waiting for upstream to come up...")
                if warmup_attempts >= MAX_WARMUP_RETRIES:
                    raise
                time.sleep(WARMUP_RETRY_DELAY_S)
            elif code == 500:
                http500_attempts += 1
                if http500_attempts >= MAX_500_RETRIES:
                    raise
                delay = 2.0 * (2 ** (http500_attempts - 1))
                log(
                    f"Server returned 500 — retrying in {delay:.0f}s "
                    f"({http500_attempts}/{MAX_500_RETRIES})..."
                )
                time.sleep(delay)
            else:
                raise


def tail_server_log(
    handle: MachineHandle,
    lines: int = 100,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Print the tail of the machine server's log (best effort, post-mortem)."""
    try:
        out, err = run_command(
            handle.client, handle.machine_id,
            ["/bin/bash", "-c",
             f"cat {handle.spec.app_dir}/server.log 2>/dev/null | tail -{lines} "
             f"|| echo '(no log file)'"],
            timeout_ms=30_000, log=log,
        )
        if out.strip():
            log(f"Server log:\n{out}")
        if err.strip():
            log(f"Server log stderr:\n{err}")
    except Exception as exc:
        log(f"Could not fetch server log: {exc}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def delete_all_machines() -> list[str]:
    """Destroy all non-destroyed machines in the org and clear the registry."""
    print("Deleting all machines...")
    client = Dedalus(api_key=_load_api_key())
    deleted: list[str] = []
    try:
        page = client.machines.list()
        machines = page.items if hasattr(page, "items") else page
        for machine in machines:
            if _phase(machine) != "destroyed":
                rev = getattr(getattr(machine, "status", None), "revision", "")
                client.machines.delete(
                    machine_id=machine.machine_id,
                    extra_headers={"If-Match": rev},
                )
                deleted.append(machine.machine_id)
                print(f"Deleted: {machine.machine_id}")
    except Exception as exc:
        print(f"Warning during delete: {exc}")

    reg = _registry_load()
    reg["machines"] = {}
    _registry_save(reg)

    print(f"Deleted {len(deleted)} machine(s)")
    return deleted
