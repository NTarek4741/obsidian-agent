"""
Generic agent runner for the utility server.

Reads credentials and the vault path from the environment (populated by
/etc/utility-server.env on the machine), binds tools, executes silently.
All output is deferred to the caller via the returned result object.
"""

import os
from functools import wraps
from pathlib import Path

from dedalus_labs import AsyncDedalus, DedalusRunner


class Persona:
    """A task-specific persona for the Obsidian Agent."""

    def __init__(
        self,
        *,
        id: str,
        name: str,
        description: str,
        model: str,
        system_prompt: str,
        tools: list = None,
        mcp_servers: list = None,
        temperature: float = 0.1,
        max_steps: int = 50,
        max_tokens: int = 8192,
        json_schema: dict = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.mcp_servers = mcp_servers or []
        self.temperature = temperature
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.json_schema = json_schema


AGENT_TIMEOUT = int(os.getenv("OBSIDIAN_AGENT_TIMEOUT", "1800"))


def _api_key() -> str:
    key = os.environ.get("DEDALUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEDALUS_API_KEY must be set in the environment")
    return key


def agent_dir() -> str:
    """The agent sandbox: <OBSIDIAN_VAULT_PATH>/agent (created on demand)."""
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault:
        raise RuntimeError("OBSIDIAN_VAULT_PATH must be set in the environment")
    d = Path(vault) / "agent"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _bind_tools(tools: list, vault_path: str):
    """Bind vault_path as the first argument of every tool function."""

    def _bind(fn, vault_path):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(vault_path, *args, **kwargs)

        return wrapper

    return [_bind(fn, vault_path) for fn in tools]


async def run_persona(persona: Persona, messages: list):
    """
    Run a single task with the specified persona.

    Returns the Dedalus runner result for post-processing.
    """

    bound_tools = _bind_tools(persona.tools, agent_dir())

    client = AsyncDedalus(api_key=_api_key(), timeout=AGENT_TIMEOUT)
    runner = DedalusRunner(client)

    run_kwargs = {
        "model": persona.model,
        "instructions": persona.system_prompt,
        "tools": bound_tools,
        "mcp_servers": persona.mcp_servers,
        "max_steps": persona.max_steps,
        "temperature": persona.temperature,
        "max_tokens": persona.max_tokens,
        "stream": False,
        "verbose": False,
        "messages": messages,
    }
    if persona.json_schema:
        run_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": persona.id,
                "schema": persona.json_schema,
                "strict": True,
            },
        }

    return await runner.run(**run_kwargs)
