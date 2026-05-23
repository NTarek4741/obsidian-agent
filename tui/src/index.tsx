#!/usr/bin/env node
import React from "react";
import { render } from "ink";
import { App } from "./components/App.js";
import { loadEnv, getBackendURL } from "./hooks/useEnv.js";

// Switch the terminal to the alternate screen buffer so we get a clean
// full-screen canvas (no scrollback bleed-through) and the user's prior shell
// session is restored intact on exit.
const ALT_ON = "\x1b[?1049h\x1b[H";
const ALT_OFF = "\x1b[?1049l";

let restored = false;
function restore() {
  if (restored) return;
  restored = true;
  try { process.stdout.write(ALT_OFF); } catch { /* ignore */ }
}

process.stdout.write(ALT_ON);

const env = loadEnv();
const backendURL = getBackendURL(env);
const app = render(<App backendURL={backendURL} />);

process.on("exit", restore);
process.on("SIGINT", () => { restore(); process.exit(130); });
process.on("SIGTERM", () => { restore(); process.exit(143); });
app.waitUntilExit().then(restore, restore);
