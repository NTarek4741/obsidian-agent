import { Box, Text, useInput } from "ink";
import { tokens } from "../styles/theme.js";

export interface InputBarProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onKeyUp: () => void;
  onKeyDown: () => void;
  onKeyEscape: () => void;
  onPgUp: () => void;
  onPgDown: () => void;
  slashMenuVisible: boolean;
  connected: boolean;
  width: number;
  isRecording?: boolean;
  cursorPos: number;
  onCursorChange: (pos: number) => void;
  statusJobs: number;
  statusVault: string | null;
}

export function InputBar({
  value,
  onChange,
  onSubmit,
  onKeyUp,
  onKeyDown,
  onKeyEscape,
  onPgUp,
  onPgDown,
  slashMenuVisible,
  connected,
  width,
  isRecording = false,
  cursorPos,
  onCursorChange,
  statusJobs,
  statusVault,
}: InputBarProps) {
  useInput((input, key) => {
    if (key.ctrl && input === "c") process.exit(0);
    if (key.ctrl && input === "q") process.exit(0);
    if (key.pageUp) { onPgUp(); return; }
    if (key.pageDown) { onPgDown(); return; }

    // While the slash-menu is visible, yield arrow & Enter to ink-select-input.
    if (slashMenuVisible) {
      if (key.upArrow || key.downArrow || key.return) return;
    }
    if (key.upArrow) { onKeyUp(); return; }
    if (key.downArrow) { onKeyDown(); return; }
    if (key.escape) { onKeyEscape(); return; }
    if (key.return) {
      onSubmit();
      onCursorChange(0);
      return;
    }
    if (key.backspace || key.delete) {
      if (cursorPos > 0) {
        onChange(value.slice(0, cursorPos - 1) + value.slice(cursorPos));
        onCursorChange(cursorPos - 1);
      }
      return;
    }
    if (key.leftArrow) { onCursorChange(Math.max(0, cursorPos - 1)); return; }
    if (key.rightArrow) { onCursorChange(Math.min(value.length, cursorPos + 1)); return; }
    if (!key.ctrl && !key.meta && input) {
      onChange(value.slice(0, cursorPos) + input + value.slice(cursorPos));
      onCursorChange(cursorPos + input.length);
    }
  });

  const before = value.slice(0, cursorPos);
  const at = value[cursorPos] || " ";
  const after = value.slice(cursorPos + 1);

  const vaultName = statusVault ? statusVault.split("/").filter(Boolean).pop() ?? statusVault : "no vault";
  const statusLeft = `${statusJobs} job${statusJobs === 1 ? "" : "s"} · ${vaultName}`;
  const hint = "ctrl+p commands";
  const innerStatusW = Math.max(0, width - 4);
  const padLen = Math.max(0, innerStatusW - statusLeft.length - hint.length - 2);
  const statusPad = " ".repeat(padLen);

  return (
    <Box width={width} flexDirection="column">
      <Box
        width={width}
        borderStyle="round"
        borderColor={tokens.boxBorder}
        borderLeftColor={tokens.accent}
        paddingX={2}
      >
        {isRecording ? (
          <Text>
            <Text color={tokens.statusRed} bold>● REC </Text>
            <Text color={tokens.textDim}>recording… press Enter to stop</Text>
          </Text>
        ) : (
          <Text wrap="truncate-end">
            <Text color={tokens.textDim}>{"❯ "}</Text>
            <Text color={tokens.textPrimary}>{before}</Text>
            <Text backgroundColor={tokens.cursorBg} color={tokens.textPrimary}>{at}</Text>
            <Text color={tokens.textPrimary}>{after}</Text>
          </Text>
        )}
      </Box>

      <Box width={width} paddingX={2}>
        <Text wrap="truncate-end">
          <Text color={tokens.textDim}>{statusLeft}</Text>
          <Text color={tokens.textDim}>{statusPad}  </Text>
          <Text color={connected ? tokens.accent : tokens.statusRed}>ctrl+p</Text>
          <Text color={tokens.textDim}>{" "}commands</Text>
        </Text>
      </Box>
    </Box>
  );
}
