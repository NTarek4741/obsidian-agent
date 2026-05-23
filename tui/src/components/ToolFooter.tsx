import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import { tokens } from "../styles/theme.js";
import type { ToolFooterModel } from "../types/index.js";

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const SPINNER_FRAMES = ["■", "□"];

export interface ToolFooterProps {
  footer: ToolFooterModel;
  width: number;
  startEpoch?: number; // when provided, recompute elapsed against now
}

export function ToolFooter({ footer, width, startEpoch }: ToolFooterProps) {
  const [, forceTick] = useState(0);

  useEffect(() => {
    if (footer.status !== "running") return;
    const id = setInterval(() => forceTick((t) => t + 1), 250);
    return () => clearInterval(id);
  }, [footer.status]);

  const liveElapsed = startEpoch ? Date.now() - startEpoch : footer.elapsedMs;
  const elapsed = formatElapsed(liveElapsed);

  // Pick the leading glyph + its color from status.
  let glyph: string;
  let glyphColor: string;
  if (footer.status === "running") {
    glyph = SPINNER_FRAMES[Math.floor(Date.now() / 500) % SPINNER_FRAMES.length];
    glyphColor = tokens.accent;
  } else if (footer.status === "failed") {
    glyph = "✗";
    glyphColor = tokens.statusRed;
  } else {
    glyph = "□";
    glyphColor = tokens.textDim;
  }

  const detail = footer.detail
    ? ` ${footer.detail}`
    : footer.status === "running"
      ? " running…"
      : "";

  return (
    <Box width={width}>
      <Text wrap="truncate-end">
        <Text color={glyphColor}>{glyph}</Text>
        <Text color={tokens.accent}>{" "}{footer.agent}</Text>
        {footer.model && (
          <>
            <Text color={tokens.textDimmer}>{" · "}</Text>
            <Text color={tokens.textDim}>{footer.model}</Text>
          </>
        )}
        <Text color={tokens.textDimmer}>{" · "}</Text>
        <Text color={tokens.textDim}>{elapsed}</Text>
        {detail && <Text color={tokens.textDim}>{detail}</Text>}
      </Text>
    </Box>
  );
}
