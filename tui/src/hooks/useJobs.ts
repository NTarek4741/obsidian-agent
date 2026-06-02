import { useState, useEffect, useRef, useCallback } from "react";
import type { APIClient } from "../api/client.js";
import type { JobPanel } from "../types/index.js";

// Lines emitted by setup/install scripts that are noise to the user. We collapse
// long apt-get / dpkg runs into a single "installing system packages…" line.
const APT_NOISE_RE = /^(Reading database|Unpacking |Preparing to unpack|Selecting previously|Setting up |Processing triggers|\(Reading|Get:|Hit:|Fetched|Inst |Conf )/;
// Maximum length of any single progress line shown to the user.
const MAX_LINE_LEN = 160;

function filterProgress(incoming: string[], existing: string[]): string[] {
  const out: string[] = [...existing];
  let bufferedNoise = false;

  for (let raw of incoming) {
    const line = raw.replace(/\s+$/, "");
    if (!line) continue;
    if (APT_NOISE_RE.test(line)) {
      if (!bufferedNoise) {
        const summary = "Installing system packages…";
        if (out[out.length - 1] !== summary) out.push(summary);
        bufferedNoise = true;
      }
      continue;
    }
    bufferedNoise = false;
    const trimmed =
      line.length > MAX_LINE_LEN ? line.slice(0, MAX_LINE_LEN - 1) + "…" : line;
    if (!out.includes(trimmed)) out.push(trimmed);
  }

  return out;
}

// Result-text extraction shared by JobBox and App.tsx. Tries common keys in
// order, falls back to pretty-JSON.
export function extractResultText(result: Record<string, unknown> | undefined): string {
  if (!result) return "";
  for (const key of ["text", "result", "message", "output", "content"]) {
    const v = result[key];
    if (typeof v === "string" && v.trim().length > 0) return v;
  }
  // Surface a path field as a markdown link-ish line.
  for (const key of ["path", "file", "filepath", "file_path", "note_path", "mind_map_path"]) {
    const v = result[key];
    if (typeof v === "string") return `Saved to \`${v}\``;
  }
  try {
    return "```json\n" + JSON.stringify(result, null, 2) + "\n```";
  } catch {
    return String(result);
  }
}

export interface UseJobsOptions {
  // Called the first time a job transitions to `done` or `failed`. The handler
  // is responsible for rendering the result (e.g. pushing an assistant_message).
  onJobResolved?: (job: JobPanel) => void;
}

export function useJobs(client: APIClient, options: UseJobsOptions = {}) {
  const [jobsByID, setJobsByID] = useState<Record<string, JobPanel>>({});
  const [activeJobID, setActiveJobID] = useState<string>("");
  const pollTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const resolvedRef = useRef<Set<string>>(new Set());
  // Hold the latest onJobResolved in a ref so updateJob is stable.
  const onResolvedRef = useRef<typeof options.onJobResolved>(options.onJobResolved);
  useEffect(() => { onResolvedRef.current = options.onJobResolved; }, [options.onJobResolved]);

  const addJob = useCallback((kind: string, id: string, submittedCmd?: string) => {
    const key = id || `_${kind}`;
    const panel: JobPanel = {
      id: key,
      kind,
      status: "running",
      progress: [],
      start: Date.now(),
      submittedCmd,
    };
    setJobsByID((prev) => ({ ...prev, [key]: panel }));
    setActiveJobID(key);
    return key;
  }, []);

  const updateJob = useCallback(
    (jobID: string, status: JobPanel["status"], progress?: string[], error?: string, result?: Record<string, unknown>) => {
      setJobsByID((prev) => {
        const existing = prev[jobID];
        if (!existing) return prev;
        const mergedProgress = progress
          ? filterProgress(progress, existing.progress)
          : existing.progress;
        return {
          ...prev,
          [jobID]: {
            ...existing,
            status,
            progress: mergedProgress,
            error,
            result,
          },
        };
      });

      if (status === "done" || status === "failed") {
        setActiveJobID((active) => (active === jobID ? "" : active));
        if (pollTimersRef.current[jobID]) {
          clearTimeout(pollTimersRef.current[jobID]);
          delete pollTimersRef.current[jobID];
        }
        // Notify the caller exactly once per job.
        if (!resolvedRef.current.has(jobID)) {
          resolvedRef.current.add(jobID);
          // Build the latest panel snapshot for the callback.
          setJobsByID((prev) => {
            const final = prev[jobID];
            if (final && onResolvedRef.current) {
              // Defer so the setState completes before the consumer pushes new items.
              setTimeout(() => onResolvedRef.current!(final), 0);
            }
            return prev;
          });
        }
      }
    },
    []
  );

  const pollJob = useCallback(
    async (jobID: string) => {
      try {
        const resp = await client.pollJob(jobID);
        updateJob(jobID, resp.status as JobPanel["status"], resp.progress, resp.error || undefined, resp.result || undefined);

        if (resp.status !== "done" && resp.status !== "failed") {
          pollTimersRef.current[jobID] = setTimeout(() => pollJob(jobID), 2000);
        }
      } catch {
        pollTimersRef.current[jobID] = setTimeout(() => pollJob(jobID), 2000);
      }
    },
    [client, updateJob]
  );

  const startPolling = useCallback(
    (jobID: string) => {
      if (pollTimersRef.current[jobID]) return;
      pollJob(jobID);
    },
    [pollJob]
  );

  const stopAllPolling = useCallback(() => {
    for (const timer of Object.values(pollTimersRef.current)) {
      clearTimeout(timer);
    }
    pollTimersRef.current = {};
  }, []);

  useEffect(() => {
    return () => stopAllPolling();
  }, [stopAllPolling]);

  const fixJobID = useCallback(
    (oldKey: string, realID: string) => {
      setJobsByID((prev) => {
        const panel = prev[oldKey];
        if (!panel) return prev;
        const next = { ...prev };
        delete next[oldKey];
        next[realID] = { ...panel, id: realID };
        return next;
      });
      setActiveJobID((active) => (active === oldKey ? realID : active));
      startPolling(realID);
    },
    [startPolling]
  );

  const recentJobs = Object.values(jobsByID).sort((a, b) => b.start - a.start);

  return {
    jobsByID,
    activeJobID,
    recentJobs,
    addJob,
    updateJob,
    startPolling,
    fixJobID,
    stopAllPolling,
  };
}
