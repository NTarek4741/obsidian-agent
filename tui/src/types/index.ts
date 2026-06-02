export interface SlashCmd {
  cmd: string;
  meta: string;
}

export interface HealthResp {
  status: string;
  configured: boolean;
  vault: string | null;
}

export interface ConfigReq {
  api_key: string;
  vault_path: string;
}

export interface ConfigResp {
  status: string;
  vault: string;
}

export interface JobResp {
  job_id: string;
}

export interface TopicReq {
  topic: string;
}

export interface NotePathReq {
  note_path: string;
}

export interface FilePathReq {
  file_path: string;
}

export interface UrlReq {
  url: string;
}

export interface JobStatusResp {
  job_id: string;
  kind: string;
  status: string;
  progress: string[];
  result: Record<string, unknown> | null;
  error: string | null;
}

export type JobKind =
  | "podcast"
  | "flashcard"
  | "transcribe"
  | "transcribe yt"
  | "transcribe live"
  | "research fast"
  | "research deep"
  | "mindmap";

export interface JobPanel {
  id: string;
  kind: string;
  status: "pending" | "running" | "done" | "failed";
  progress: string[];
  start: number; // epoch ms
  error?: string;
  result?: Record<string, unknown>;
  submittedCmd?: string;
}

// ─── Chat-area content items ──────────────────────────────────────────────
// Each viewport row dispatches on `kind`. This is OpenCode-style: there are no
// implicit shapes — every renderable thing has its own variant.

export interface ToolFooterModel {
  agent: string;          // e.g. "transcribe-live", "podcast"
  model?: string;         // optional secondary label
  elapsedMs: number;      // wall clock since start
  status: "running" | "done" | "failed";
  detail?: string;        // running: short status; failed: error preview
}

export type ContentItem =
  | { kind: "user_message"; text: string }
  | { kind: "assistant_message"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "thinking_anim"; id: string }
  | { kind: "tool_footer"; footer: ToolFooterModel }
  | { kind: "job"; jobID: string };

export interface AppState {
  backendURL: string;
  autoStartBackend: boolean;
}
