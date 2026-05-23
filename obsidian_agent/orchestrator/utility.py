"""
Environment configuration and vault path management for the Obsidian Agent.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key


def _ensure_vault_path() -> str:
    """
    Load OBSIDIAN_VAULT_PATH from .env.
    If missing or invalid, prompt the user and persist it.
    Returns the agent sandbox folder (vault_root/agent/).
    """
    load_dotenv()
    vault = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()

    if vault:
        resolved = Path(vault).expanduser().resolve()
        if resolved.exists() and resolved.is_dir():
            return _ensure_agent_folder(resolved)
        print(f"Warning: Saved vault path does not exist: {resolved}")

    while True:
        vault = input("Enter the absolute path to your Obsidian vault: ").strip()
        if not vault:
            print("Path cannot be empty.")
            continue
        resolved = Path(vault).expanduser().resolve()
        if not resolved.exists():
            print(f"Path does not exist: {resolved}")
            continue
        if not resolved.is_dir():
            print(f"Path is not a directory: {resolved}")
            continue
        break

    # Persist to .env
    env_path = Path(".env")
    if env_path.exists():
        set_key(env_path, "OBSIDIAN_VAULT_PATH", str(resolved))
    else:
        env_path.write_text(f'OBSIDIAN_VAULT_PATH="{resolved}"\n', encoding="utf-8")

    return _ensure_agent_folder(resolved)


def _ensure_agent_folder(vault_root: Path) -> str:
    """Create and return the agent sandbox folder inside the vault."""
    agent_dir = vault_root / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    return str(agent_dir)


def _load_api_key() -> str:
    """Load DEDALUS_API_KEY from environment. Exit if missing."""
    load_dotenv()
    key = os.getenv("DEDALUS_API_KEY", "").strip()
    if not key:
        print("Error: DEDALUS_API_KEY not found. Please set it in your .env file.")
        sys.exit(1)
    return key
