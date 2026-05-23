import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, statSync, unlink } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ffmpegInstaller from "@ffmpeg-installer/ffmpeg";

export type RecorderStatus = "recording" | "stopped" | "failed";

export interface RecorderHandle {
  filePath: string;
  stop(): Promise<void>;
  cancel(): void;
  status(): RecorderStatus;
  error: Error | null;
}

function buildArgv(filePath: string): string[] {
  // 16 kHz mono PCM WAV — what the transcription backend prefers.
  if (process.platform === "darwin") {
    return [
      "-hide_banner", "-loglevel", "error",
      "-y",
      "-f", "avfoundation",
      "-i", ":0",         // default audio input
      "-ac", "1",
      "-ar", "16000",
      filePath,
    ];
  }
  if (process.platform === "linux") {
    return [
      "-hide_banner", "-loglevel", "error",
      "-y",
      "-f", "alsa",
      "-i", "default",
      "-ac", "1",
      "-ar", "16000",
      filePath,
    ];
  }
  // Unreachable — startLiveRecording errors out before this is called.
  return [];
}

export function startLiveRecording(): RecorderHandle {
  const id = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 6);
  const filePath = join(tmpdir(), `obsidian-agent-recording-${id}.wav`);

  const handle: RecorderHandle = {
    filePath,
    stop: async () => {},
    cancel: () => {},
    status: () => "failed",
    error: null,
  };

  if (process.platform !== "darwin" && process.platform !== "linux") {
    handle.error = new Error("Live recording is only supported on Linux and macOS.");
    return handle;
  }

  const binPath = ffmpegInstaller?.path;
  if (!binPath) {
    handle.error = new Error(
      "Bundled ffmpeg binary is missing. Run `npm install` inside the tui directory to reinstall.",
    );
    return handle;
  }

  const args = buildArgv(filePath);
  let child: ChildProcess;
  try {
    child = spawn(binPath, args, { stdio: ["ignore", "ignore", "pipe"] });
  } catch (e) {
    handle.error = e as Error;
    return handle;
  }

  let st: RecorderStatus = "recording";
  let stderrTail = "";
  const exited: Promise<void> = new Promise((resolve) => {
    child.on("exit", (code) => {
      st = st === "recording" ? "stopped" : st;
      if (code !== 0 && code !== null && st !== "stopped") {
        handle.error = new Error(`ffmpeg exited ${code}: ${stderrTail.slice(-200)}`);
        st = "failed";
      }
      resolve();
    });
    child.on("error", (err) => {
      handle.error = err;
      st = "failed";
      resolve();
    });
  });

  if (child.stderr) {
    child.stderr.on("data", (chunk: Buffer) => {
      stderrTail += chunk.toString("utf8");
      if (stderrTail.length > 4096) stderrTail = stderrTail.slice(-2048);
    });
  }

  handle.status = () => st;
  handle.stop = async () => {
    if (st !== "recording") return;
    try { child.kill("SIGINT"); } catch { /* ignore */ }
    const escalate = setTimeout(() => { try { child.kill("SIGKILL"); } catch { /* ignore */ } }, 2000);
    await exited;
    clearTimeout(escalate);
  };
  handle.cancel = () => {
    try { child.kill("SIGKILL"); } catch { /* ignore */ }
    st = "stopped";
    unlink(filePath, () => { /* ignore */ });
  };

  return handle;
}

export function fileLooksValid(filePath: string): boolean {
  try {
    if (!existsSync(filePath)) return false;
    return statSync(filePath).size > 1024;
  } catch {
    return false;
  }
}

export function cleanupRecording(filePath: string, delayMs = 30_000): void {
  setTimeout(() => unlink(filePath, () => { /* ignore */ }), delayMs);
}
