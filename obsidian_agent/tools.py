"""
Sandboxed filesystem tools for Obsidian vault management.

All paths are relative to the vault root. Directory traversal (..) and absolute
paths are rejected. Tools use pure Python pathlib for speed and reliability.
"""

import difflib
import json
import re
from pathlib import Path
from typing import List

import yaml


def _suggest_files(vault_path: str, bad_name: str, top_n: int = 3) -> List[str]:
    """
    Return the closest filename matches in the vault using fuzzy matching.
    """
    root = Path(vault_path).resolve()
    all_names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    close = difflib.get_close_matches(bad_name, all_names, n=top_n, cutoff=0.3)
    return close


def _suggest_folders(vault_path: str, bad_name: str, top_n: int = 3) -> List[str]:
    """
    Return the closest folder name matches in the vault using fuzzy matching.
    """
    root = Path(vault_path).resolve()
    all_names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()]
    close = difflib.get_close_matches(bad_name, all_names, n=top_n, cutoff=0.3)
    return close


def _resolve(vault_path: str, user_path: str) -> Path:
    """
    Resolve a user-provided relative path inside the vault root.

    Rules:
      - Reject absolute paths.
      - Reject any '..' component.
      - Max path length: 4096 chars.
      - Max depth: 20 levels.
      - Reject symlinks (defense-in-depth).
      - Resolve to an absolute path and ensure it lives under vault_root.
    """
    root = Path(vault_path).resolve()
    user = Path(user_path)

    if user.is_absolute():
        raise PermissionError(f"Absolute paths are not allowed: {user_path}")

    if len(str(user_path)) > 4096:
        raise PermissionError(f"Path exceeds max length of 4096: {user_path[:50]}...")

    parts = user.parts
    if len(parts) > 20:
        raise PermissionError(f"Path exceeds max depth of 20 levels: {user_path}")

    for part in parts:
        if part == "..":
            raise PermissionError(f"Directory traversal is not allowed: {user_path}")

    target = (root / user).resolve()

    # Ensure target is inside root (handles symlinks, etc.)
    try:
        target.relative_to(root)
    except ValueError:
        raise PermissionError(f"Path escapes vault root: {user_path}")

    # Defense-in-depth: reject symlinks
    if target.is_symlink() or target.parent.is_symlink():
        raise PermissionError(f"Symlinks are not allowed: {user_path}")

    return target


# =============================================================================
# BASIC CRUD
# =============================================================================


def vault_read(vault_path: str, path: str) -> str:
    """
    Read the contents of a file inside the vault.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path inside the vault (e.g. "Notes/Lecture 11.md").
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            suggestions = _suggest_files(vault_path, path)
            msg = f'Error: File "{path}" not found.'
            if suggestions:
                msg += "\nDid you mean:\n  - " + "\n  - ".join(suggestions)
            return msg
        if not target.is_file():
            return f'Error: "{path}" is not a file.'
        return target.read_text(encoding="utf-8")
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_write(vault_path: str, path: str, content: str) -> str:
    """
    Write content to a file inside the vault. Creates parent folders as needed.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path inside the vault.
        content: Text content to write.
    """
    try:
        target = _resolve(vault_path, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f'Success: Wrote "{path}" ({len(content)} chars).'
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_append(vault_path: str, path: str, content: str) -> str:
    """
    Append content to the end of a file inside the vault.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path inside the vault.
        content: Text to append.
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            return f'Error: File "{path}" not found.'
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return f'Success: Appended to "{path}".'
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_delete(vault_path: str, path: str) -> str:
    """
    Delete a file inside the vault.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path inside the vault.
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            return f'Error: File "{path}" not found.'
        if not target.is_file():
            return f'Error: "{path}" is not a file.'
        target.unlink()
        return f'Success: Deleted "{path}".'
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_list(vault_path: str, path: str = "") -> str:
    """
    List files and folders at a relative path inside the vault.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative folder path (default "" = vault root).
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            suggestions = _suggest_folders(vault_path, path)
            msg = f'Error: Folder "{path}" not found.'
            if suggestions:
                msg += "\nDid you mean:\n  - " + "\n  - ".join(suggestions)
            return msg
        if not target.is_dir():
            return f'Error: "{path}" is not a folder.'
        items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = []
        for item in items:
            suffix = "/" if item.is_dir() else ""
            lines.append(f"{item.name}{suffix}")
        return "\n".join(lines) if lines else "(empty)"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_exists(vault_path: str, path: str) -> str:
    """
    Check whether a relative path exists inside the vault.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path inside the vault.
    """
    try:
        target = _resolve(vault_path, path)
        return "true" if target.exists() else "false"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# =============================================================================
# SEARCH & METADATA
# =============================================================================


def vault_search(vault_path: str, query: str) -> str:
    """
    Fuzzy-search all filenames in the vault.

    Args:
        vault_path: Absolute path to the vault root.
        query: Search term (case-insensitive substring match).
    """
    try:
        root = Path(vault_path).resolve()
        query_lower = query.lower()
        matches = []
        for p in root.rglob("*"):
            if p.is_file() and query_lower in p.name.lower():
                rel = p.relative_to(root).as_posix()
                matches.append(rel)
        return "\n".join(matches) if matches else f"No files matching '{query}'."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_grep(vault_path: str, query: str) -> str:
    """
    Search for a term inside all markdown files in the vault.

    Returns matching lines with filename and line number.

    Args:
        vault_path: Absolute path to the vault root.
        query: Search term (case-insensitive).
    """
    try:
        root = Path(vault_path).resolve()
        query_lower = query.lower()
        matches = []
        for p in root.rglob("*.md"):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            rel = p.relative_to(root).as_posix()
            for i, line in enumerate(text.splitlines(), start=1):
                if query_lower in line.lower():
                    snippet = line.strip()[:120]
                    matches.append(f"{rel}:{i}: {snippet}")
        return "\n".join(matches) if matches else f"No matches for '{query}'."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_get_metadata(vault_path: str, path: str) -> str:
    """
    Parse YAML frontmatter from a markdown file.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path to a .md file.
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            return f'Error: File "{path}" not found.'
        text = target.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return "{}"
        parts = text.split("---", 2)
        if len(parts) < 3:
            return "{}"
        front = yaml.safe_load(parts[1])
        return json.dumps(front, default=str, indent=2) if front else "{}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_get_tags(vault_path: str, path: str = "") -> str:
    """
    Extract #tags from markdown files.

    If path is empty, scans the entire vault. Otherwise scans one file.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path to a .md file, or "" for all files.
    """
    try:
        root = Path(vault_path).resolve()
        tag_pattern = re.compile(r"#([A-Za-z0-9_\-/]+)")
        results = {}

        if path:
            target = _resolve(vault_path, path)
            files = [target] if target.is_file() else []
        else:
            files = [p for p in root.rglob("*.md") if p.is_file()]

        for f in files:
            text = f.read_text(encoding="utf-8")
            tags = sorted(set(tag_pattern.findall(text)))
            if tags:
                rel = f.relative_to(root).as_posix()
                results[rel] = tags

        return json.dumps(results, indent=2) if results else "{}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_get_links(vault_path: str, path: str) -> str:
    """
    Extract all [[wikilinks]] from a markdown file.

    Args:
        vault_path: Absolute path to the vault root.
        path: Relative path to a .md file.
    """
    try:
        target = _resolve(vault_path, path)
        if not target.exists():
            return f'Error: File "{path}" not found.'
        text = target.read_text(encoding="utf-8")
        # Match [[Note]] and [[Note|alias]]
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)
        return json.dumps(sorted(set(links)), indent=2)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def vault_get_backlinks(vault_path: str, target_note: str) -> str:
    """
    Find all files that link to a given note name.

    Args:
        vault_path: Absolute path to the vault root.
        target_note: Note name to search for (e.g. "Lecture 11").
    """
    try:
        root = Path(vault_path).resolve()
        pattern = re.compile(rf"\[\[{re.escape(target_note)}(?:\|[^\]]+)?\]\]")
        results = []
        for p in root.rglob("*.md"):
            if p.is_file() and pattern.search(p.read_text(encoding="utf-8")):
                results.append(p.relative_to(root).as_posix())
        return json.dumps(sorted(results), indent=2)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# =============================================================================
# CANVAS
# =============================================================================


def vault_write_canvas(vault_path: str, path: str, canvas_data) -> str:
    """
    Write a .canvas JSON file into the vault.

    Parameters (exact names — do not change):
        vault_path: Absolute path to the vault root (auto-filled by the runner).
        path: Relative path inside the vault. MUST end in .canvas. Example: "Diagrams/My Map.canvas".
        canvas_data: The canvas data. Accepts EITHER:
                     - A JSON string: '{"nodes": [...], "edges": [...]}'
                     - A dict object: {"nodes": [...], "edges": [...]}
                     Must include "nodes" and "edges" arrays.
    """
    try:
        target = _resolve(vault_path, path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Accept both dict and JSON string
        if isinstance(canvas_data, dict):
            parsed = canvas_data
        elif isinstance(canvas_data, str):
            parsed = json.loads(canvas_data)
        else:
            return (
                f"Error: canvas_data must be a JSON string or a dict object. "
                f"You passed a {type(canvas_data).__name__}. "
                f"Please pass a valid canvas object like "
                f'canvas_data=\'{{"nodes": [], "edges": []}}\''
            )

        target.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        return f'Success: Wrote canvas "{path}".'
    except PermissionError as e:
        return f"Error: {e}"
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON in canvas_data: {e}. Make sure it is a valid JSON string with double quotes."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"




def canvas_critique_verdict(approved: bool, critique: str) -> str:
    """
    Return a structured critique verdict.

    Parameters (exact names — do not change):
        approved: True if the canvas passes all quality metrics, False otherwise.
        critique: Detailed critique text. If approved=True, write "APPROVED". If False, list specific issues.

    Returns:
        Verdict string.
    """
    status = "APPROVED" if approved else "NEEDS_FIX"
    return json.dumps({"status": status, "critique": critique}, indent=2)
