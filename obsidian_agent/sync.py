"""Local → machine mirror sync of the vault's agent/ folder.

The machine's copy of <vault>/agent is made byte-identical to the local one
before any utility job runs: new/changed files are uploaded, machine-side
files that no longer exist locally are deleted. Local is the source of
truth; content flows the other way only through job deliverables (which are
written into the local vault and therefore match on the next root check).

Protocol (flat manifest + root hash — see server_setup/vault_sync.py):
    GET  /vault-manifest    → {"root": ..., "files": {rel: sha256}}
    POST /vault-sync        {"writes": {rel: b64}, "deletes": [rel, ...]}
Already in sync ⇒ one round trip. Out of sync ⇒ two. A flat manifest beats a
deeper merkle descent here: the manifest is a few KB while every HTTPS round
trip to the preview URL costs 100-300ms.

Local hashing is cached by (mtime_ns, size) in sync_cache.json so the steady
state costs a stat() walk, not a re-hash of the corpus.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import time
from pathlib import Path
from typing import Callable

from obsidian_agent.config import _ensure_vault_path
from obsidian_agent.machine import MachineHandle, _emit, request

_CACHE_FILE = Path(__file__).parent / "sync_cache.json"

# Load the manifest algorithm from the server bundle itself so local and
# machine manifests can never drift.
_VAULT_SYNC_PATH = (
    Path(__file__).parent / "orchestrator" / "utility" / "server_setup" / "vault_sync.py"
)
_vs_spec = importlib.util.spec_from_file_location("_obsidian_vault_sync", _VAULT_SYNC_PATH)
vault_sync = importlib.util.module_from_spec(_vs_spec)
_vs_spec.loader.exec_module(vault_sync)


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build_local_manifest(root: Path) -> dict[str, str]:
    """Manifest of the local agent folder, hash-cached by (mtime_ns, size)."""
    cache = _load_cache()
    fresh: dict[str, list] = {}
    manifest: dict[str, str] = {}
    for rel, path in vault_sync.iter_sync_files(root):
        st = path.stat()
        entry = cache.get(rel)
        if entry and entry[0] == st.st_mtime_ns and entry[1] == st.st_size:
            sha = entry[2]
        else:
            sha = vault_sync.file_sha256(path)
        manifest[rel] = sha
        fresh[rel] = [st.st_mtime_ns, st.st_size, sha]
    if fresh != cache:
        _CACHE_FILE.write_text(json.dumps(fresh), encoding="utf-8")
    return manifest


def sync_agent_folder(
    handle: MachineHandle, *, log: Callable[[str], None] = print
) -> dict:
    """Mirror <local vault>/agent onto the machine. Returns sync stats.

    No locking: sync only ever runs inside the single gated job.
    """
    name = handle.spec.name
    agent_root = Path(_ensure_vault_path())

    t0 = time.monotonic()
    local = build_local_manifest(agent_root)
    local_root = vault_sync.root_hash(local)

    resp = request(handle, "GET", "/vault-manifest", timeout=120.0, log=log)
    remote = resp.json()
    if remote.get("root") == local_root:
        took = round(time.monotonic() - t0, 2)
        log(f"● Vault in sync ({len(local)} files, {took:.1f}s)")
        _emit(name, f"vault in sync ({len(local)} files)",
              sync={"files": len(local), "uploaded": 0, "deleted": 0,
                    "took_s": took, "at": time.time()})
        return {"in_sync": True, "files": len(local), "uploaded": 0, "deleted": 0}

    remote_files: dict[str, str] = remote.get("files") or {}
    writes = {
        rel: base64.b64encode((agent_root / rel).read_bytes()).decode("ascii")
        for rel, sha in local.items()
        if remote_files.get(rel) != sha
    }
    deletes = sorted(set(remote_files) - set(local))

    log(f"● Syncing vault: ↑{len(writes)} changed, ✕{len(deletes)} removed…")
    resp = request(
        handle, "POST", "/vault-sync",
        json_body={"writes": writes, "deletes": deletes},
        timeout=600.0, log=log,
    )
    new_root = resp.json().get("root")
    if new_root != local_root:
        raise RuntimeError(
            f"Vault sync verification failed: machine root {new_root!r} "
            f"!= local root {local_root!r}"
        )
    took = round(time.monotonic() - t0, 2)
    log(f"● Vault synced ({len(local)} files, ↑{len(writes)}, ✕{len(deletes)}, {took:.1f}s)")
    _emit(name, f"synced ↑{len(writes)} ✕{len(deletes)} ({len(local)} files)",
          sync={"files": len(local), "uploaded": len(writes),
                "deleted": len(deletes), "took_s": took, "at": time.time()})
    return {"in_sync": False, "files": len(local),
            "uploaded": len(writes), "deleted": len(deletes)}
