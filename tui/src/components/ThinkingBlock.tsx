import { Box, Text } from "ink";
import { tokens } from "../styles/theme.js";

export interface ThinkingBlockProps {
  text: string;
  width: number;
}

// "Thinking:" prefix in italic amber, body in dim text — no border, no padding.
export function ThinkingBlock({ text, width }: ThinkingBlockProps) {
  return (
    <Box width={width}>
      <Text wrap="wrap">
        <Text italic color={tokens.thinkingAmber}>Thinking: </Text>
        <Text color={tokens.textDim}>{text}</Text>
      </Text>
    </Box>
  );
}
