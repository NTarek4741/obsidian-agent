import { useState, useCallback } from "react";
import type { APIClient } from "../api/client.js";
import type { SlashCmd, JobPanel } from "../types/index.js";

export const ALL_SLASH_COMMANDS: SlashCmd[] = [
  { cmd: "/podcast", meta: "<note-path>      Generate M4A podcast from note" },
  { cmd: "/flashcard", meta: "<note-path>      Generate Anki deck from note" },
  { cmd: "/transcribe", meta: "<file-path>      Transcribe audio/video file" },
  { cmd: "/transcribe yt", meta: "<url>            Transcribe YouTube video" },
  { cmd: "/transcribe live", meta: "                 Start microphone recording" },
  { cmd: "/research fast", meta: "<topic>          Quick research note" },
  { cmd: "/research deep", meta: "<topic>          Deep research (plan + build)" },
  { cmd: "/mindmap", meta: "<note-path>      Create mind map from note" },
  { cmd: "/config", meta: "                 Setup API key + vault path" },
  { cmd: "/jobs", meta: "                 Show active/recent jobs" },
  { cmd: "/clear", meta: "                 Clear the output area" },
  { cmd: "/help", meta: "                 Show all commands" },
  { cmd: "/quit", meta: "                 Quit" },
];

export type CommandResult =
  | { type: "message"; text: string }
  | { type: "job_start"; kind: string; jobID: string; realID?: string }
  | { type: "direct_start"; kind: string }
  | { type: "config" }
  | { type: "list_jobs" }
  | { type: "clear" }
  | { type: "help" }
  | { type: "quit" }
  | { type: "transcribe_live" };

export function useCommands(
  client: APIClient,
  activeJobID: string,
  onStartJob: (kind: string, jobID: string) => void,
  onStartDirect: (kind: string) => void,
  onDirectResult: (kind: string, result: string, err?: string) => void
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

      // Block while job is active (except quit/jobs)
      if (activeJobID && verb !== "quit" && verb !== "jobs") {
        return [
          { type: "message", text: `Job [${activeJobID}] is running — only /quit and /jobs available.` },
        ];
      }

      const results: CommandResult[] = [];

      switch (verb) {
        case "podcast": {
          if (!args) return [{ type: "message", text: "Usage: /podcast <note-path>" }];
          try {
            const resp = await client.startPodcast(args);
            results.push({ type: "job_start", kind: "podcast", jobID: "", realID: resp.job_id });
          } catch (e) {
            onStartDirect("podcast");
            onDirectResult("podcast", "", String(e));
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
            onStartDirect("flashcard");
            onDirectResult("flashcard", "", String(e));
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
              onStartDirect("transcribe");
              onDirectResult("transcribe", "", String(e));
              results.push({ type: "message", text: `Transcribe failed: ${e}` });
            }
          } else {
            const content = args.trim();
            if (!content) return [{ type: "message", text: "Usage: /transcribe <file|url>  |  /transcribe yt <url>" }];
            try {
              const resp = await client.startTranscribe(content);
              results.push({ type: "job_start", kind: "transcribe", jobID: "", realID: resp.job_id });
            } catch (e) {
              onStartDirect("transcribe");
              onDirectResult("transcribe", "", String(e));
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
            onStartDirect("research fast");
            try {
              const resp = await client.fastResearch(topic);
              results.push({ type: "message", text: `Research complete: ${resp.result}` });
              onDirectResult("research fast", resp.result);
            } catch (e) {
              onDirectResult("research fast", "", String(e));
              results.push({ type: "message", text: `Research failed: ${e}` });
            }
          } else if (mode === "deep") {
            try {
              const resp = await client.startDeepResearch(topic);
              results.push({ type: "job_start", kind: "research deep", jobID: "", realID: resp.job_id });
            } catch (e) {
              onStartDirect("research deep");
              onDirectResult("research deep", "", String(e));
              results.push({ type: "message", text: `Failed to start deep research: ${e}` });
            }
          } else {
            results.push({ type: "message", text: `Unknown mode "${mode}". Use fast or deep.` });
          }
          break;
        }

        case "mindmap": {
          if (!args) return [{ type: "message", text: "Usage: /mindmap <note-path>" }];
          onStartDirect("mindmap");
          try {
            const resp = await client.mindMap(args);
            results.push({ type: "message", text: `Mind map saved: ${resp.mind_map_path}` });
            onDirectResult("mindmap", resp.mind_map_path);
          } catch (e) {
            onDirectResult("mindmap", "", String(e));
            results.push({ type: "message", text: `Mind map failed: ${e}` });
          }
          break;
        }

        case "config": {
          results.push({ type: "config" });
          break;
        }

        case "jobs": {
          results.push({ type: "list_jobs" });
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
    [client, activeJobID, onStartJob, onStartDirect, onDirectResult]
  );

  return { dispatch };
}
