import { useState, useCallback } from "react";
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { homedir } from "os";

const HISTORY_DIR = join(homedir(), ".config", "obsidian-agent");
const HISTORY_FILE = join(HISTORY_DIR, "history");

function loadHistory(): string[] {
  try {
    const text = readFileSync(HISTORY_FILE, "utf-8");
    return text.split("\n").filter((l) => l.trim() !== "");
  } catch {
    return [];
  }
}

function saveHistory(history: string[]) {
  try {
    mkdirSync(HISTORY_DIR, { recursive: true });
    writeFileSync(HISTORY_FILE, history.join("\n") + "\n", "utf-8");
  } catch {
    // ignore
  }
}

export function useHistory() {
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [historyIdx, setHistoryIdx] = useState<number>(loadHistory().length);

  const append = useCallback((cmd: string) => {
    setHistory((prev) => {
      const next = [...prev, cmd];
      saveHistory(next);
      return next;
    });
    setHistoryIdx((prev) => prev + 1);
  }, []);

  const prev = useCallback((): string | null => {
    let idx = historyIdx;
    if (idx > 0) idx -= 1;
    if (idx < 0 || idx >= history.length) return null;
    setHistoryIdx(idx);
    return history[idx];
  }, [history, historyIdx]);

  const next = useCallback((): string | null => {
    let idx = historyIdx;
    if (idx < history.length - 1) {
      idx += 1;
      setHistoryIdx(idx);
      return history[idx];
    }
    setHistoryIdx(history.length);
    return "";
  }, [history, historyIdx]);

  const reset = useCallback(() => {
    setHistoryIdx(history.length);
  }, [history.length]);

  return { history, append, prev, next, reset };
}
