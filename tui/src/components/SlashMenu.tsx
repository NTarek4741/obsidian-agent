import { Box, Text } from "ink";
import SelectInput from "ink-select-input";
import { ALL_SLASH_COMMANDS } from "../hooks/useCommands.js";
import { tokens } from "../styles/theme.js";
import type { SlashCmd } from "../types/index.js";

export interface SlashMenuProps {
  input: string;
  onSelect: (cmd: SlashCmd) => void;
  width: number;
}

export function getSlashHits(input: string): SlashCmd[] {
  if (!input.startsWith("/")) return [];
  const lo = input.toLowerCase().trim();
  return ALL_SLASH_COMMANDS.filter((sc) => sc.cmd.toLowerCase().startsWith(lo));
}

const CMD_COL = 20;

function buildItemComponent(menuW: number) {
  // ink-select-input renders our component with { isSelected, label }; label
  // is the SlashCmd.cmd. We look up the original entry by exact-match.
  const inner = Math.max(4, menuW - 4); // box border (2) + paddingX (2)
  return function SlashItem({ label, isSelected }: { label: string; isSelected?: boolean }) {
    const sc = ALL_SLASH_COMMANDS.find((c) => c.cmd === label);
    const cmdText = (sc?.cmd ?? label).padEnd(CMD_COL);
    const metaText = sc?.meta ?? "";
    if (isSelected) {
      // Pad the selected row so the purple highlight bar reaches the menu's
      // right edge — otherwise the terminal wallpaper bleeds through the gap.
      const visibleLen = 2 /* caret */ + cmdText.length + 2 + metaText.length;
      const pad = Math.max(0, inner - visibleLen);
      return (
        <Text bold color={tokens.textPrimary} backgroundColor={tokens.cursorBg}>
          {"❯ "}{cmdText}{"  "}{metaText}{" ".repeat(pad)}
        </Text>
      );
    }
    return (
      <Text wrap="truncate-end">
        <Text color={tokens.textDimmer}>{"  "}</Text>
        <Text color={tokens.accent}>{cmdText}</Text>
        <Text color={tokens.textDim}>{"  "}{metaText}</Text>
      </Text>
    );
  };
}

const NullIndicator = () => null;

export function SlashMenu({ input, onSelect, width }: SlashMenuProps) {
  const hits = getSlashHits(input);
  if (hits.length === 0) return null;

  const items = hits.map((sc) => ({
    key: sc.cmd,
    label: sc.cmd,
    value: sc,
  }));

  return (
    <Box
      width={width}
      flexDirection="column"
      borderStyle="round"
      borderColor={tokens.boxBorder}
      borderLeftColor={tokens.accent}
    >
      <SelectInput<SlashCmd>
        items={items}
        limit={7}
        isFocused
        onSelect={(it) => onSelect(it.value)}
        itemComponent={buildItemComponent(width)}
        indicatorComponent={NullIndicator}
      />
    </Box>
  );
}
