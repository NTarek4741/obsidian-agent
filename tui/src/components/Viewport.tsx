import { Box, Text } from "ink";
import type { ContentItem, JobPanel } from "../types/index.js";
import { JobBox } from "./JobBox.js";
import { MessageBox } from "./MessageBox.js";
import { MarkdownText } from "./MarkdownText.js";
import { ThinkingBlock } from "./ThinkingBlock.js";
import { ThinkingDots } from "./ThinkingDots.js";
import { ToolFooter } from "./ToolFooter.js";
import { tokens } from "../styles/theme.js";

export interface ViewportProps {
  items: ContentItem[];
  jobsByID: Record<string, JobPanel>;
  width: number;
  height: number;
  scrollOffset: number;
}

function estimateRows(item: ContentItem, job: JobPanel | undefined, width: number): number {
  if (item.kind === "job") {
    if (!job) return 0;
    return 1 + 1; // ToolFooter row + marginBottom
  }
  if (item.kind === "tool_footer") return 1 + 1;
  if (item.kind === "thinking_anim") return 1 + 1;
  if (item.kind === "thinking") {
    const lines = Math.ceil(item.text.length / Math.max(20, width - 6));
    return Math.max(1, lines) + 1;
  }
  if (item.kind === "user_message") {
    const lines = Math.ceil(item.text.length / Math.max(20, width - 8));
    return 2 + Math.max(1, lines) + 1; // borders top+bottom + content + spacing
  }
  // assistant_message
  const lines = item.text.split("\n").reduce((acc, l) => acc + Math.max(1, Math.ceil(l.length / Math.max(20, width - 4))), 0);
  return Math.max(1, lines) + 1;
}

function sliceForViewport(
  items: ContentItem[],
  jobsByID: Record<string, JobPanel>,
  width: number,
  height: number,
  scrollOffset: number
): ContentItem[] {
  if (items.length === 0) return [];

  // Phase 1: standard bottom-up fill.
  let skipped = 0;
  let filled = 0;
  let firstVisibleIdx = items.length;
  const visibleReversed: ContentItem[] = [];
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    const job = it.kind === "job" ? jobsByID[it.jobID] : undefined;
    if (it.kind === "job" && !job) continue;
    const rows = estimateRows(it, job, width);
    if (skipped < scrollOffset) {
      skipped += rows;
      continue;
    }
    if (filled + rows > height && visibleReversed.length > 0) break;
    visibleReversed.push(it);
    filled += rows;
    firstVisibleIdx = i;
    if (filled >= height) break;
  }
  let visible = visibleReversed.reverse();

  // Phase 2 — pair preservation: if the first visible item isn't a
  // user_message but the immediately-preceding item is, pull that
  // user_message in (dropping bottom items if needed) so the user always sees
  // the request that started the visible thread.
  if (
    visible.length > 0 &&
    firstVisibleIdx > 0 &&
    visible[0].kind !== "user_message" &&
    items[firstVisibleIdx - 1].kind === "user_message"
  ) {
    const um = items[firstVisibleIdx - 1];
    const umRows = estimateRows(um, undefined, width);
    // Drop trailing items until there's room for the user_message at top.
    while (visible.length > 1 && filled + umRows > height) {
      const dropped = visible.pop()!;
      filled -= estimateRows(
        dropped,
        dropped.kind === "job" ? jobsByID[dropped.jobID] : undefined,
        width,
      );
    }
    visible = [um, ...visible];
  }

  return visible;
}

export function Viewport({ items, jobsByID, width, height, scrollOffset }: ViewportProps) {
  const innerW = Math.max(10, width - 4);
  const visible = sliceForViewport(items, jobsByID, width, height, scrollOffset);

  return (
    <Box
      width={width}
      height={height}
      flexDirection="column"
      paddingX={2}
      paddingTop={1}
      overflow="hidden"
    >
      {scrollOffset > 0 && (
        <Box width={innerW}>
          <Text wrap="truncate-end">
            <Text color={tokens.textDim}>{`  ↑ scrolled ${scrollOffset} rows  ·  PgDn to return`}</Text>
          </Text>
        </Box>
      )}

      {visible.map((item, i) => {
        const key = `${item.kind}-${i}`;
        switch (item.kind) {
          case "user_message":
            return (
              <Box key={key} width={width} marginBottom={1}>
                <MessageBox text={item.text} width={innerW} />
              </Box>
            );
          case "assistant_message":
            return (
              <Box key={key} width={width} marginBottom={1}>
                <MarkdownText source={item.text} width={innerW} />
              </Box>
            );
          case "thinking":
            return (
              <Box key={key} width={width} marginBottom={1}>
                <ThinkingBlock text={item.text} width={innerW} />
              </Box>
            );
          case "thinking_anim":
            return (
              <Box key={key} width={width} marginBottom={1}>
                <ThinkingDots width={innerW} />
              </Box>
            );
          case "tool_footer":
            return (
              <Box key={key} width={width} marginBottom={1}>
                <ToolFooter footer={item.footer} width={innerW} />
              </Box>
            );
          case "job": {
            const job = jobsByID[item.jobID];
            if (!job) return null;
            return (
              <Box key={key} width={width} marginBottom={1}>
                <JobBox job={job} width={innerW} />
              </Box>
            );
          }
        }
      })}
    </Box>
  );
}
