"""
Generic agent runner for the Obsidian Agent.

Loads credentials, resolves the vault path, binds tools, executes silently.
All output is deferred to the caller via the returned result object.
"""

import os
from functools import wraps

from dedalus_labs import AsyncDedalus, DedalusRunner
from pydantic_core.core_schema import json_schema

from .config import _ensure_vault_path, _load_api_key


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

    api_key = _load_api_key()
    vault_path = _ensure_vault_path()

    bound_tools = _bind_tools(persona.tools, vault_path)

    client = AsyncDedalus(api_key=api_key, timeout=AGENT_TIMEOUT)
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
        "verbose": True,
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
