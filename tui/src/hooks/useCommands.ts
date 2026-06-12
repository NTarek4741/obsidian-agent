import { useCallback } from "react";
import type { APIClient } from "../api/client.js";
import type { MachinesResp, SlashCmd } from "../types/index.js";

export const ALL_SLASH_COMMANDS: SlashCmd[] = [
  { cmd: "/chat", meta: "<question>       Ask your synced agent vault" },
  { cmd: "/podcast", meta: "<note-path>      Generate WAV podcast from note" },
  { cmd: "/flashcard", meta: "<note-path>      Generate Anki deck (ephemeral sandbox)" },
  { cmd: "/transcribe", meta: "<file-path>      Transcribe audio/video file" },
  { cmd: "/transcribe yt", meta: "<url>            Transcribe YouTube video" },
  { cmd: "/transcribe live", meta: "                 Start microphone recording" },
  { cmd: "/research fast", meta: "<topic>          Quick research note" },
  { cmd: "/research deep", meta: "<topic>          Deep research (plan + build)" },
  { cmd: "/mindmap", meta: "<note-path>      Create mind map from note" },
  { cmd: "/machines", meta: "                 Show Dedalus machine status" },
  { cmd: "/config", meta: "                 Setup API key + vault path" },
  { cmd: "/clear", meta: "                 Clear the output area" },
  { cmd: "/help", meta: "                 Show all commands" },
  { cmd: "/quit", meta: "                 Quit" },
];

function formatMachines(resp: MachinesResp): string {
  const lines: string[] = ["**Dedalus machines**", "", "```"];
  for (const m of resp.machines) {
    lines.push(
      `${m.name.padEnd(10)} ${m.lifecycle.padEnd(11)} ${m.phase.padEnd(11)} ${m.resources}`
    );
    let detail = `           id: ${m.machine_id ?? "—"}   autosleep: ${m.autosleep}`;
    if (m.wake_seconds != null) detail += `   last wake: ${m.wake_seconds}s`;
    lines.push(detail);
    if (m.last_event) lines.push(`           ${m.last_event}`);
  }
  lines.push("```");
  const events = resp.events.slice(0, 8);
  if (events.length > 0) {
    lines.push("", "**Recent events**", "", "```");
    for (const e of events) {
      const t = new Date(e.ts * 1000).toTimeString().slice(0, 8);
      lines.push(`${t}  ${e.machine.padEnd(10)} ${e.event}`);
    }
    lines.push("```");
  }
  return lines.join("\n");
}

export type CommandResult =
  | { type: "message"; text: string }
  | { type: "job_start"; kind: string; jobID: string; realID?: string }
  | { type: "config" }
  | { type: "clear" }
  | { type: "help" }
  | { type: "quit" }
  | { type: "transcribe_live" };

export function useCommands(
  client: APIClient,
  activeJobID: string,
) {
  const dispatch = useCallback(
    async (input: string): Promise<CommandResult[]> => {
      const trimmed = input.trim();
      if (!trimmed.startsWith("/")) {
        return [{ type: "message", text: "Commands start with /  — type / to see the list." }];
      }

      const parts = trimmed.slice(1).split(" ");
      const verb = parts[0].toLowerCase();
      const args = parts.slice(1).join(" ").trim();

      // Block while job is active (except quit)
      if (activeJobID && verb !== "quit") {
        return [
          { type: "message", text: `Job [${activeJobID}] is running — only /quit is available.` },
        ];
      }

      const results: CommandResult[] = [];

      switch (verb) {
        case "chat": {
          if (!args) return [{ type: "message", text: "Usage: /chat <question>" }];
          try {
            const resp = await client.startChat(args);
            results.push({ type: "job_start", kind: "chat", jobID: "", realID: resp.job_id });
          } catch (e) {
            results.push({ type: "message", text: `Chat failed: ${e}` });
          }
          break;
        }

        case "machines": {
          try {
            const resp = await client.getMachines();
            results.push({ type: "message", text: formatMachines(resp) });
          } catch (e) {
            results.push({ type: "message", text: `Failed to fetch machines: ${e}` });
          }
          break;
        }

        case "podcast": {
          if (!args) return [{ type: "message", text: "Usage: /podcast <note-path>" }];
          try {
            const resp = await client.startPodcast(args);
            results.push({ type: "job_start", kind: "podcast", jobID: "", realID: resp.job_id });
          } catch (e) {
            results.push({ type: "message", text: `Failed to start podcast: ${e}` });
          }
          break;
        }

        case "flashcard": {
          if (!args) return [{ type: "message", text: "Usage: /flashcard <note-path>" }];
          try {
            const resp = await client.startFlashcard(args);
            results.push({ type: "job_start", kind: "flashcard", jobID: "", realID: resp.job_id });
          } catch (e) {
            results.push({ type: "message", text: `Failed to start flashcard: ${e}` });
          }
          break;
        }

        case "transcribe": {
          const subParts = args.split(" ");
          const sub = subParts[0].toLowerCase();
          const subArgs = subParts.slice(1).join(" ").trim();

          if (sub === "live") {
            results.push({ type: "transcribe_live" });
          } else if (sub === "yt") {
            if (!subArgs) return [{ type: "message", text: "Usage: /transcribe yt <url>" }];
            try {
              const resp = await client.startTranscribe(subArgs);
              results.push({ type: "job_start", kind: "transcribe", jobID: "", realID: resp.job_id });
            } catch (e) {
              results.push({ type: "message", text: `Transcribe failed: ${e}` });
            }
          } else {
            const content = args.trim();
            if (!content) return [{ type: "message", text: "Usage: /transcribe <file|url>  |  /transcribe yt <url>" }];
            try {
              const resp = await client.startTranscribe(content);
              results.push({ type: "job_start", kind: "transcribe", jobID: "", realID: resp.job_id });
            } catch (e) {
              results.push({ type: "message", text: `Transcribe failed: ${e}` });
            }
          }
          break;
        }

        case "research": {
          const rParts = args.split(" ");
          const mode = rParts[0].toLowerCase();
          const topic = rParts.slice(1).join(" ").trim();
          if (!topic) return [{ type: "message", text: "Usage: /research fast <topic>   or   /research deep <topic>" }];

          if (mode === "fast") {
            try {
              const resp = await client.startFastResearch(topic);
              results.push({ type: "job_start", kind: "research fast", jobID: "", realID: resp.job_id });
            } catch (e) {
              results.push({ type: "message", text: `Failed to start research: ${e}` });
            }
          } else if (mode === "deep") {
            try {
              const resp = await client.startDeepResearch(topic);
              results.push({ type: "job_start", kind: "research deep", jobID: "", realID: resp.job_id });
            } catch (e) {
              results.push({ type: "message", text: `Failed to start deep research: ${e}` });
            }
          } else {
            results.push({ type: "message", text: `Unknown mode "${mode}". Use fast or deep.` });
          }
          break;
        }

        case "mindmap": {
          if (!args) return [{ type: "message", text: "Usage: /mindmap <note-path>" }];
          try {
            const resp = await client.startMindMap(args);
            results.push({ type: "job_start", kind: "mindmap", jobID: "", realID: resp.job_id });
          } catch (e) {
            results.push({ type: "message", text: `Failed to start mind map: ${e}` });
          }
          break;
        }

        case "config": {
          results.push({ type: "config" });
          break;
        }

        case "clear": {
          results.push({ type: "clear" });
          break;
        }

        case "help": {
          results.push({ type: "help" });
          break;
        }

        case "quit": {
          results.push({ type: "quit" });
          break;
        }

        default: {
          results.push({ type: "message", text: `Unknown command: /${verb}  — type / to see the list.` });
        }
      }

      return results;
    },
    [client, activeJobID]
  );

  return { dispatch };
}
