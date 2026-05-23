import { Box } from "ink";
import type { JobPanel } from "../types/index.js";
import { ToolFooter } from "./ToolFooter.js";

// JobPanel → ToolFooter mapping. Done jobs surface the first useful result
// field as a detail; failed jobs show the error preview; running jobs show
// the last progress line.
function detailFor(job: JobPanel): string | undefined {
  if (job.status === "running") {
    return job.progress[job.progress.length - 1];
  }
  if (job.status === "failed") return job.error;
  if (job.status === "done" && job.result) {
    for (const key of ["path", "file", "file_path", "note_path", "mind_map_path", "output"]) {
      const v = job.result[key];
      if (typeof v === "string") return v.slice(0, 80);
    }
  }
  return undefined;
}

export function JobBox({ job, width }: { job: JobPanel; width: number }) {
  return (
    <Box width={width}>
      <ToolFooter
        width={width}
        startEpoch={job.start}
        footer={{
          agent: job.submittedCmd?.replace(/^\//, "").split(/\s+/).slice(0, 2).join("-") || job.kind,
          model: undefined,
          elapsedMs: Date.now() - job.start,
          status: job.status === "pending" ? "running" : job.status,
          detail: detailFor(job),
        }}
      />
    </Box>
  );
}
