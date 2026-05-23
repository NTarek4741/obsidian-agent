import { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { tokens } from "../styles/theme.js";

export interface ThinkingDotsProps {
  width: number;
  label?: string;
}

// One-row animated indicator: italic amber "Thinking" + cycling dots.
// Lives in the items array; the App removes it when the first concrete
// response (job or assistant_message) arrives.
export function ThinkingDots({ width, label = "Thinking" }: ThinkingDotsProps) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 400);
    return () => clearInterval(id);
  }, []);
  const dots = ".".repeat((tick % 3) + 1);
  return (
    <Box width={width}>
      <Text wrap="truncate-end">
        <Text italic color={tokens.thinkingAmber}>{label}</Text>
        <Text color={tokens.textDim}>{dots}</Text>
      </Text>
    </Box>
  );
}
