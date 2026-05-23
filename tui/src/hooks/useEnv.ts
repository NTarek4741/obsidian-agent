import { readFileSync, readdirSync } from "fs";
import { resolve } from "path";

export interface EnvVars {
  DEDALUS_API_KEY?: string;
  OBSIDIAN_VAULT_PATH?: string;
  OBSIDIAN_AGENT_BACKEND_URL?: string;
}

function findProjectRoot(): string {
  // Try common locations: start from cwd, go up
  let dir = process.cwd();
  for (let i = 0; i < 5; i++) {
    try {
      const files = new Set(readdirSync(dir));
      if (files.has(".env") && files.has("pyproject.toml")) {
        return dir;
      }
    } catch {
      // ignore
    }
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

export function loadEnv(projectRoot?: string): EnvVars {
  const root = projectRoot || findProjectRoot();
  const envPath = resolve(root, ".env");
  const result: EnvVars = {};

  try {
    const text = readFileSync(envPath, "utf-8");
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      let value = trimmed.slice(eq + 1).trim();
      // Strip quotes
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      if (key === "DEDALUS_API_KEY") result.DEDALUS_API_KEY = value;
      if (key === "OBSIDIAN_VAULT_PATH") result.OBSIDIAN_VAULT_PATH = value;
      if (key === "OBSIDIAN_AGENT_BACKEND_URL") result.OBSIDIAN_AGENT_BACKEND_URL = value;
    }
  } catch {
    // .env not found
  }

  return result;
}

export function getBackendURL(env?: EnvVars): string {
  return env?.OBSIDIAN_AGENT_BACKEND_URL || "http://localhost:8000";
}
