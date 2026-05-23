import { useEffect, useState } from "react";
import os from "node:os";
import { execSync } from "node:child_process";
import path from "node:path";
import { Box, Text } from "ink";
import { tokens } from "../styles/theme.js";
import type { JobPanel } from "../types/index.js";

const APP_VERSION = "0.3.0";

function shortHome(p: string): string {
  const home = os.homedir();
  if (p.startsWith(home)) return "~" + p.slice(home.length);
  return p;
}

function detectBranch(cwd: string): string {
  try {
    const out = execSync("git -C " + JSON.stringify(cwd) + " rev-parse --abbrev-ref HEAD", {
      stdio: ["ignore", "pipe", "ignore"],
      encoding: "utf8",
    });
    return out.trim() || "main";
  } catch {
    return "";
  }
}

interface Seg {
  text: string;
  color?: string;
  bold?: boolean;
}

export interface SidebarProps {
  connected: boolean;
  vault: string | null;
  recentJobs: JobPanel[];
  width: number;
  height: number;
  sessionStartedAt: number;
}

export function Sidebar({ connected, vault, recentJobs, width, height, sessionStartedAt }: SidebarProps) {
  const SB = tokens.sidebarBg;
  const padL = 2;
  const inner = Math.max(2, width - padL);

  // Paint one row to exactly `width` cells with SB as background. Trailing
  // whitespace is emitted as an explicit Text child so Ink colors those cells
  // too (otherwise the terminal wallpaper bleeds through after the last
  // printable character).
  function paintRow(segs: Seg[], key: React.Key): JSX.Element {
    let used = 0;
    const out: JSX.Element[] = [];
    out.push(
      <Text key="lpad" backgroundColor={SB}>{" ".repeat(padL)}</Text>
    );
    used += padL;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      const remaining = Math.max(0, width - used);
      const txt = s.text.slice(0, remaining);
      out.push(
        <Text key={i} backgroundColor={SB} color={s.color} bold={s.bold}>
          {txt}
        </Text>
      );
      used += txt.length;
      if (used >= width) break;
    }
    if (used < width) {
      out.push(
        <Text key="rpad" backgroundColor={SB}>{" ".repeat(width - used)}</Text>
      );
    }
    return (
      <Text key={key} backgroundColor={SB}>
        {out}
      </Text>
    );
  }

  function blankRow(key: React.Key): JSX.Element {
    return paintRow([], key);
  }

  const sessionISO = new Date(sessionStartedAt).toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
  const vaultName = vault ? path.basename(vault.replace(/\/+$/, "")) : "no vault";
  const cwd = shortHome(process.cwd());
  const [branch, setBranch] = useState<string>("");
  useEffect(() => { setBranch(detectBranch(process.cwd())); }, []);

  const totalJobs = recentJobs.length;
  const runningJobs = recentJobs.filter((j) => j.status === "running").length;

  const topRows: JSX.Element[] = [];
  topRows.push(paintRow([{ text: "New session", color: tokens.textPrimary, bold: true }], "ns-h"));
  topRows.push(paintRow([{ text: sessionISO, color: tokens.textDim }], "ns-1"));
  topRows.push(blankRow("ns-blank"));

  topRows.push(paintRow([{ text: "Context", color: tokens.textPrimary, bold: true }], "ctx-h"));
  topRows.push(paintRow(
    [{ text: `${totalJobs} job${totalJobs === 1 ? "" : "s"}`, color: tokens.textDim }],
    "ctx-1",
  ));
  topRows.push(paintRow([{ text: `${runningJobs} active`, color: tokens.textDim }], "ctx-2"));
  topRows.push(blankRow("ctx-blank"));

  topRows.push(paintRow([{ text: "Vault", color: tokens.textPrimary, bold: true }], "v-h"));
  topRows.push(paintRow([{ text: vaultName.slice(0, inner - 1), color: tokens.textDim }], "v-1"));
  topRows.push(blankRow("v-blank"));

  topRows.push(paintRow(
    [
      { text: "● ", color: connected ? tokens.statusGreen : tokens.statusRed },
      { text: connected ? "online" : "offline", color: connected ? tokens.textPrimary : tokens.textDim },
    ],
    "st",
  ));

  const bottomRows: JSX.Element[] = [];
  const cwdLabel = `${cwd}${branch ? ":" + branch : ""}`;
  bottomRows.push(paintRow([{ text: cwdLabel.slice(0, inner - 1), color: tokens.textDim }], "bot-cwd"));
  bottomRows.push(paintRow(
    [
      { text: "● ", color: tokens.statusGreen },
      { text: "obsidian-agent", color: tokens.textPrimary, bold: true },
      { text: " v" + APP_VERSION, color: tokens.textDim },
    ],
    "bot-ver",
  ));

  const used = topRows.length + bottomRows.length;
  const filler = Math.max(0, height - used);

  return (
    <Box width={width} height={height} flexDirection="column">
      {topRows}
      {Array.from({ length: filler }).map((_, i) => blankRow(`fill-${i}`))}
      {bottomRows}
    </Box>
  );
}
