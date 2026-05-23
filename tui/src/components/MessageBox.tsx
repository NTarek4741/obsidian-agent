import { Box, Text } from "ink";
import { tokens } from "../styles/theme.js";

export interface MessageBoxProps {
  text: string;
  width: number;
}

// User-message box: rounded dim border on top/right/bottom, bright blue stripe
// on the left. Matches OpenCode's chat input box and submitted-message style.
export function MessageBox({ text, width }: MessageBoxProps) {
  return (
    <Box
      borderStyle="round"
      borderColor={tokens.boxBorder}
      borderLeftColor={tokens.accent}
      paddingX={2}
      width={width}
    >
      <Text color={tokens.textPrimary} wrap="wrap">{text}</Text>
    </Box>
  );
}
