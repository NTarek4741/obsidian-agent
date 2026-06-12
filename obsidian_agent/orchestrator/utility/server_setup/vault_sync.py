"""Manifest helpers shared by the machine server and the local sync client.

The sync protocol mirrors the local vault's agent/ folder onto the machine:
flat {relpath: sha256} manifests on both sides, compared via a single root
hash. The root hash is a depth-1 merkle tree — one cheap GET answers
"already in sync?" in a single round trip, and a full sync needs only two.

Pure stdlib on purpose: this file ships in the utility machine bundle AND is
importlib-loaded by obsidian_agent/sync.py locally, so both ends are
guaranteed to compute identical manifests.
"""

import hashlib
from pathlib import Path

# Top-level folder inside the sync domain used for ad-hoc input staging
# (machine path: vault/agent/uploads). Excluded — inputs are transient, not
# corpus, and are already excluded from job deliverables.
EXCLUDED_TOP_LEVEL = {"uploads"}


def is_synced_path(rel_parts: tuple) -> bool:
    """Whether a path (parts relative to the sync root) participates in sync.

    Dotfiles/dirs are skipped (.obsidian, .DS_Store, .trash, ...) along with
    the transient uploads/ staging area.
    """
    if not rel_parts:
        return False
    if any(part.startswith(".") for part in rel_parts):
        return False
    if rel_parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    return True


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_sync_files(root: Path):
    """Yield (relpath_posix, absolute_path) for every synced file under root."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if not is_synced_path(rel.parts):
            continue
        yield rel.as_posix(), path


def build_manifest(root: Path) -> dict:
    """{relpath: sha256} for every synced file under root."""
    return {rel: file_sha256(path) for rel, path in iter_sync_files(root)}


def root_hash(manifest: dict) -> str:
    """Order-independent digest of a manifest; equal roots ⇒ trees in sync."""
    h = hashlib.sha256()
    for rel in sorted(manifest):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(manifest[rel].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()
