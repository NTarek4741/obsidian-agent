import { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useStdout } from "ink";

import { useBackend } from "../hooks/useBackend.js";
import { useJobs, extractResultText } from "../hooks/useJobs.js";
import { useCommands } from "../hooks/useCommands.js";
import { useHistory } from "../hooks/useHistory.js";
import { useMachines } from "../hooks/useMachines.js";
import { getSlashHits } from "../components/SlashMenu.js";
import { Viewport } from "../components/Viewport.js";
import { Sidebar } from "../components/Sidebar.js";
import { InputBar } from "../components/InputBar.js";
import { SlashMenu } from "../components/SlashMenu.js";
import { ConfigWizard } from "../components/ConfigWizard.js";
import {
  startLiveRecording,
  fileLooksValid,
  cleanupRecording,
  type RecorderHandle,
} from "../api/liveRecorder.js";
import { tokens } from "../styles/theme.js";
import type { ContentItem, ToolFooterModel } from "../types/index.js";

const APP_VERSION = "0.3.0";
// InputBar owns: 1 top-border + 1 input row + 1 bottom-border + 1 status row = 4
const BOTTOM_ROWS = 4;

// OpenCode's sidebar reads as ~22% of the terminal width. Clamp to 32..40 so
// the panel feels right on both narrow and ultrawide terminals.
function sidebarWidthFor(termW: number): number {
  return Math.min(40, Math.max(32, Math.floor(termW * 0.22)));
}

export interface AppProps {
  backendURL: string;
}

export function App({ backendURL }: AppProps) {
  const { stdout } = useStdout();
  const [width, setWidth] = useState(stdout.columns || 120);
  const [height, setHeight] = useState(stdout.rows || 40);

  const [items, setItems] = useState<ContentItem[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [slashMenuVisible, setSlashMenuVisible] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [cursorPos, setCursorPos] = useState(0);
  const sessionStartedAtRef = useRef(Date.now());

  // Active live recorder (only one at a time)
  const recorderRef = useRef<RecorderHandle | null>(null);
  const recordingStartRef = useRef<number>(0);
  // Index of the running tool_footer item for live recording, so we can update it in place
  const liveFooterIdxRef = useRef<number>(-1);

  const welcomeShownRef = useRef(false);

  const { state: backendState, checkHealth, client } = useBackend(backendURL, true);
  const machines = useMachines(client, backendState.connected);

  // ─── Item helpers (declared early so useJobs callback can use them) ───
  const pushItem = useCallback((item: ContentItem) => {
    setItems((prev) => [...prev, item]);
  }, []);
  const pushUser = useCallback((text: string) => pushItem({ kind: "user_message", text }), [pushItem]);
  const pushAssistant = useCallback((text: string) => pushItem({ kind: "assistant_message", text }), [pushItem]);
  const pushJob = useCallback((jobID: string) => pushItem({ kind: "job", jobID }), [pushItem]);

  // Remove all thinking_anim items (called when a concrete response arrives).
  const dropThinking = useCallback(() => {
    setItems((prev) => prev.filter((it) => it.kind !== "thinking_anim"));
  }, []);

  const { jobsByID, activeJobID, recentJobs, addJob, updateJob, startPolling } = useJobs(client, {
    onJobResolved: (job) => {
      dropThinking();
      if (job.status === "failed") {
        pushAssistant(`✗ **${job.kind}** failed: ${job.error ?? "unknown error"}`);
        return;
      }
      const text = extractResultText(job.result);
      if (text) pushAssistant(text);
    },
  });
  const { history: _history, append: appendHistory, prev: historyPrev, next: historyNext, reset: historyReset } = useHistory();
  const pushToolFooter = useCallback((footer: ToolFooterModel) => {
    setItems((prev) => {
      const next = [...prev, { kind: "tool_footer" as const, footer }];
      liveFooterIdxRef.current = next.length - 1;
      return next;
    });
  }, []);
  const updateToolFooter = useCallback((updater: (f: ToolFooterModel) => ToolFooterModel) => {
    setItems((prev) => {
      const idx = liveFooterIdxRef.current;
      if (idx < 0 || idx >= prev.length) return prev;
      const cur = prev[idx];
      if (cur.kind !== "tool_footer") return prev;
      const next = [...prev];
      next[idx] = { kind: "tool_footer", footer: updater(cur.footer) };
      return next;
    });
  }, []);

  const { dispatch } = useCommands(client, activeJobID);

  useEffect(() => {
    const onResize = () => {
      setWidth(stdout.columns || 120);
      setHeight(stdout.rows || 40);
    };
    stdout.on("resize", onResize);
    return () => { stdout.off("resize", onResize); };
  }, [stdout]);

  useEffect(() => {
    if (!welcomeShownRef.current) {
      welcomeShownRef.current = true;
      pushAssistant(
        `**Obsidian Agent** v${APP_VERSION}\n\nAI-powered tools for your Obsidian vault. Type \`/help\` for all commands.`
      );
    }
  }, [pushAssistant]);

  useEffect(() => {
    if (backendState.connected && !backendState.configured && !showConfig) {
      setShowConfig(true);
    }
  }, [backendState.connected, backendState.configured, showConfig]);

  useEffect(() => {
    if (inputValue.startsWith("/")) {
      setSlashMenuVisible(getSlashHits(inputValue).length > 0);
    } else {
      setSlashMenuVisible(false);
    }
  }, [inputValue]);

  const slashMenuH = slashMenuVisible ? Math.min(getSlashHits(inputValue).length, 7) + 2 : 0;
  const SIDEBAR_WIDTH = sidebarWidthFor(width);
  const mainW = Math.max(10, width - SIDEBAR_WIDTH);
  const viewportH = Math.max(1, height - BOTTOM_ROWS - slashMenuH);

  // ─── Scroll state ───────────────────────────────────────────────────
  const [scrollOffset, setScrollOffset] = useState(0);
  const atBottomRef = useRef(true);
  useEffect(() => { atBottomRef.current = scrollOffset === 0; }, [scrollOffset]);
  const prevItemsLenRef = useRef(items.length);
  useEffect(() => {
    if (items.length > prevItemsLenRef.current && atBottomRef.current) {
      setScrollOffset(0);
    }
    prevItemsLenRef.current = items.length;
  }, [items.length]);

  // ─── Tick to refresh elapsed time on running tool footers ───────────
  const [, forceTick] = useState(0);
  useEffect(() => {
    const hasRunning = items.some(
      (it) => it.kind === "tool_footer" && it.footer.status === "running"
    );
    if (!hasRunning) return;
    const id = setInterval(() => forceTick((t) => t + 1), 500);
    return () => clearInterval(id);
  }, [items]);

  // ─── Live recording flow ────────────────────────────────────────────
  const startLiveRecord = useCallback(() => {
    const handle = startLiveRecording();
    if (handle.error || handle.status() === "failed") {
      pushAssistant(`✗ Live recording failed: ${handle.error?.message ?? "unknown error"}`);
      return;
    }
    recorderRef.current = handle;
    recordingStartRef.current = Date.now();
    setIsRecording(true);
    pushToolFooter({
      agent: "transcribe-live",
      model: "microphone",
      elapsedMs: 0,
      status: "running",
      detail: "recording — press Enter to stop",
    });
  }, [pushAssistant, pushToolFooter]);

  const stopLiveRecord = useCallback(async () => {
    const handle = recorderRef.current;
    if (!handle) return;
    setIsRecording(false);
    updateToolFooter((f) => ({ ...f, detail: "processing…", elapsedMs: Date.now() - recordingStartRef.current }));
    try {
      await handle.stop();
    } catch {
      // continue — we'll validate the file below
    }
    if (!fileLooksValid(handle.filePath)) {
      updateToolFooter((f) => ({ ...f, status: "failed", detail: "no audio captured" }));
      recorderRef.current = null;
      return;
    }
    try {
      const resp = await client.startTranscribe(handle.filePath);
      const localKey = addJob("transcribe-live", resp.job_id, "/transcribe live");
      pushJob(localKey);
      startPolling(localKey);
      updateToolFooter((f) => ({
        ...f,
        status: "done",
        detail: `submitted (${resp.job_id})`,
        elapsedMs: Date.now() - recordingStartRef.current,
      }));
      cleanupRecording(handle.filePath, 60_000);
    } catch (e) {
      updateToolFooter((f) => ({ ...f, status: "failed", detail: `upload failed: ${e}` }));
    }
    recorderRef.current = null;
  }, [addJob, client, pushJob, startPolling, updateToolFooter]);

  // ─── Submit handler ─────────────────────────────────────────────────
  const handleSubmit = useCallback(async () => {
    if (isRecording) {
      await stopLiveRecord();
      return;
    }

    // A job is already running — silently swallow new submissions. The user
    // sees the input clear; no new request/thinking/error is rendered. The
    // current job's report will land when its poll completes.
    if (activeJobID) {
      setInputValue("");
      setCursorPos(0);
      setSlashMenuVisible(false);
      return;
    }

    const val = inputValue.trim();
    if (!val) return;

    appendHistory(val);
    setInputValue("");
    setCursorPos(0);
    setSlashMenuVisible(false);

    // Determine if this verb starts a "real" job — those clear the screen so
    // request + status + result form one tight thread.
    const verb = val.startsWith("/")
      ? val.slice(1).split(/\s+/)[0].toLowerCase()
      : "";
    const JOB_STARTING = new Set([
      "podcast", "flashcard", "transcribe", "research", "mindmap",
    ]);
    const clearsScreen = JOB_STARTING.has(verb);

    if (clearsScreen) {
      setItems([{ kind: "user_message", text: val }, { kind: "thinking_anim", id: String(Date.now()) }]);
    } else {
      pushUser(val);
    }

    const results = await dispatch(val);

    for (const res of results) {
      switch (res.type) {
        case "message":
          dropThinking();
          pushAssistant(res.text);
          break;
        case "job_start": {
          dropThinking();
          const key = addJob(res.kind, res.realID ?? res.jobID, val);
          pushJob(key);
          startPolling(key);
          break;
        }
        case "transcribe_live":
          dropThinking();
          startLiveRecord();
          break;
        case "config":
          setShowConfig(true);
          break;
        case "clear":
          setItems([]);
          break;
        case "help": {
          const cmds = [
            "/chat <question>          Ask your synced agent vault",
            "/podcast <note-path>      Generate WAV podcast from note",
            "/flashcard <note-path>    Generate Anki deck (ephemeral sandbox)",
            "/transcribe <file>        Transcribe audio/video file",
            "/transcribe yt <url>      Transcribe YouTube video",
            "/transcribe live          Start microphone recording",
            "/research fast <topic>    Quick research note",
            "/research deep <topic>    Deep research (plan + build)",
            "/mindmap <note-path>      Create mind map from note",
            "/machines                 Show Dedalus machine status",
            "/config                   Setup API key + vault path",
            "/clear                    Clear the output area",
            "/help                     Show all commands",
            "/quit                     Quit",
          ];
          pushAssistant(["**Commands**", "", "```", ...cmds, "```"].join("\n"));
          break;
        }
        case "quit":
          process.exit(0);
      }
    }
  }, [
    isRecording, stopLiveRecord, activeJobID, inputValue, appendHistory, dispatch,
    pushUser, pushAssistant, pushJob, addJob, client, startPolling, startLiveRecord,
    dropThinking,
  ]);

  const handleKeyUp = useCallback(() => {
    const val = historyPrev();
    if (val !== null) { setInputValue(val); setCursorPos(val.length); }
  }, [historyPrev]);

  const handleKeyDown = useCallback(() => {
    const val = historyNext();
    if (val !== null) { setInputValue(val); setCursorPos(val.length); }
  }, [historyNext]);

  const handleKeyEscape = useCallback(() => {
    if (slashMenuVisible) setSlashMenuVisible(false);
    else if (isRecording) {
      recorderRef.current?.cancel();
      recorderRef.current = null;
      setIsRecording(false);
      updateToolFooter((f) => ({ ...f, status: "failed", detail: "cancelled" }));
    } else { setInputValue(""); setCursorPos(0); historyReset(); }
  }, [slashMenuVisible, isRecording, historyReset, updateToolFooter]);

  const handlePgUp = useCallback(() => {
    setScrollOffset((prev) => prev + Math.max(1, viewportH - 2));
  }, [viewportH]);
  const handlePgDown = useCallback(() => {
    setScrollOffset((prev) => Math.max(0, prev - Math.max(1, viewportH - 2)));
  }, [viewportH]);

  const handleSlashSelect = useCallback((cmd: { cmd: string }) => {
    setInputValue(cmd.cmd + " ");
    setCursorPos(cmd.cmd.length + 1);
    setSlashMenuVisible(false);
  }, []);

  if (showConfig) {
    return (
      <Box width={width} height={height}>
        <ConfigWizard
          client={client}
          onDone={(success, vault) => {
            setShowConfig(false);
            if (success && vault) { pushAssistant("✓ Config saved."); checkHealth(); }
            else pushAssistant("✗ Config save failed.");
          }}
        />
      </Box>
    );
  }

  if (!backendState.connected && backendState.checking) {
    return (
      <Box width={width} height={height}>
        <Text color={tokens.textDim}>  connecting to backend…</Text>
      </Box>
    );
  }

  return (
    <Box width={width} height={height} flexDirection="row">
      {/* LEFT COLUMN */}
      <Box width={mainW} height={height} flexDirection="column">
        <Viewport
          items={items}
          jobsByID={jobsByID}
          width={mainW}
          height={viewportH}
          scrollOffset={scrollOffset}
        />

        {slashMenuVisible && (
          <Box width={mainW}>
            <SlashMenu
              input={inputValue}
              onSelect={handleSlashSelect}
              width={mainW}
            />
          </Box>
        )}

        <InputBar
          value={inputValue}
          onChange={setInputValue}
          onSubmit={handleSubmit}
          onKeyUp={handleKeyUp}
          onKeyDown={handleKeyDown}
          onKeyEscape={handleKeyEscape}
          onPgUp={handlePgUp}
          onPgDown={handlePgDown}
          slashMenuVisible={slashMenuVisible}
          connected={backendState.connected}
          width={mainW}
          isRecording={isRecording}
          cursorPos={cursorPos}
          onCursorChange={setCursorPos}
          statusJobs={recentJobs.length}
          statusVault={backendState.vault}
        />
      </Box>

      {/* SIDEBAR — full height, no divider; backgrounds carry the seam */}
      <Sidebar
        connected={backendState.connected}
        vault={backendState.vault}
        recentJobs={recentJobs}
        machines={machines}
        width={SIDEBAR_WIDTH}
        height={height}
        sessionStartedAt={sessionStartedAtRef.current}
      />
    </Box>
  );
}
