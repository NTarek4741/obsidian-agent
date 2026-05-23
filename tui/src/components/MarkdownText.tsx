import { useMemo } from "react";
import { Box, Text } from "ink";
import { marked } from "marked";
import { markedTerminal } from "marked-terminal";
import chalk from "chalk";
import { tokens } from "../styles/theme.js";

// One-time renderer setup. marked.use() is global, so we configure once.
let configured = false;
function configureMarked() {
  if (configured) return;
  configured = true;
  const themedOptions = {
    heading: chalk.hex(tokens.mdHeading).bold,
    firstHeading: chalk.hex(tokens.mdHeading).bold,
    blockquote: chalk.hex(tokens.mdBlockquote).italic,
    hr: chalk.hex(tokens.mdHr),
    strong: chalk.hex(tokens.mdStrong).bold,
    em: chalk.hex(tokens.mdEm).italic,
    del: chalk.hex(tokens.textDim).strikethrough,
    codespan: chalk.hex(tokens.mdCode).bgHex(tokens.mdCodeBg),
    code: chalk.hex(tokens.mdCode).bgHex(tokens.mdCodeBg),
    link: chalk.hex(tokens.mdLink).underline,
    href: chalk.hex(tokens.mdLink).underline,
    listitem: (text: string) => `${chalk.hex(tokens.mdListBullet)("•")} ${text}`,
    paragraph: chalk.hex(tokens.textPrimary),
    reflowText: true,
    tab: 2,
    unescape: true,
    emoji: false,
  };
  // marked.use accepts a renderer-extension object; the @types/marked-terminal
  // declarations are stale for v7 (which returns a usable extension), so we
  // cast through unknown.
  marked.use(markedTerminal(themedOptions as any) as unknown as Parameters<typeof marked.use>[0]);
}

// Cache rendered ANSI strings per (source, width) to avoid re-parsing on every frame.
const cache = new Map<string, string>();
function cacheKey(source: string, width: number): string {
  return `${width}\x1f${source}`;
}

function renderMarkdown(source: string, width: number): string {
  configureMarked();
  const key = cacheKey(source, width);
  const hit = cache.get(key);
  if (hit !== undefined) return hit;

  // marked-terminal's `width` option controls wrapping for some elements; we
  // primarily rely on Ink's width-bounded Box to do the wrapping, but pass it
  // through for code blocks and tables.
  marked.use(
    markedTerminal({ width } as any) as unknown as Parameters<typeof marked.use>[0]
  );

  let out: string;
  try {
    out = marked.parse(source, { async: false }) as string;
  } catch {
    out = source;
  }

  // marked-terminal often emits a trailing newline; strip it so Ink doesn't
  // add a blank row at the bottom of every block.
  out = out.replace(/\n+$/, "");

  cache.set(key, out);
  return out;
}

export interface MarkdownTextProps {
  source: string;
  width: number;
}

export function MarkdownText({ source, width }: MarkdownTextProps) {
  const ansi = useMemo(() => renderMarkdown(source, width), [source, width]);
  return (
    <Box width={width}>
      <Text wrap="wrap">{ansi}</Text>
    </Box>
  );
}
