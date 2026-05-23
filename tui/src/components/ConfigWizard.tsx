import { Box, Text, useInput } from "ink";
import { useState } from "react";
import type { APIClient } from "../api/client.js";

export interface ConfigWizardProps {
  client: APIClient;
  onDone: (success: boolean, vault?: string) => void;
}

export function ConfigWizard({ client, onDone }: ConfigWizardProps) {
  const [step, setStep] = useState<0 | 1>(0);
  const [apiKey, setApiKey] = useState("");
  const [vaultPath, setVaultPath] = useState("");

  useInput((input, key) => {
    if (key.escape) {
      if (step === 0) {
        onDone(false);
      } else {
        setStep(0);
      }
      return;
    }

    if (key.return) {
      if (step === 0) {
        if (apiKey.trim()) {
          setStep(1);
        }
      } else {
        if (vaultPath.trim()) {
          client
            .saveConfig({ api_key: apiKey, vault_path: vaultPath })
            .then((resp) => onDone(true, resp.vault))
            .catch(() => onDone(false));
        }
      }
      return;
    }

    if (key.backspace || key.delete) {
      if (step === 0) {
        setApiKey((prev) => prev.slice(0, -1));
      } else {
        setVaultPath((prev) => prev.slice(0, -1));
      }
      return;
    }

    if (!key.ctrl && !key.meta && input) {
      if (step === 0) {
        setApiKey((prev) => prev + input);
      } else {
        setVaultPath((prev) => prev + input);
      }
    }
  });

  return (
    <Box flexDirection="column" padding={2}>
      <Text bold color="#a78bfa">
        {"  "}obsidian agent{" "}
      </Text>
      <Text color="#9895b5"> setup</Text>
      <Text> </Text>

      {step === 0 ? (
        <>
          <Text color="#9895b5"> Dedalus API key</Text>
          <Text color="#c9c5d9">
            {"  "}
            {apiKey.length > 0 ? "•".repeat(apiKey.length) : "_"}
          </Text>
          <Text> </Text>
          <Text color="#9895b5"> enter to continue   esc to cancel</Text>
        </>
      ) : (
        <>
          <Text color="#9895b5"> Obsidian vault path</Text>
          <Text color="#c9c5d9">
            {"  "}
            {vaultPath || "_"}
          </Text>
          <Text> </Text>
          <Text color="#9895b5"> enter to save   esc to go back</Text>
        </>
      )}
    </Box>
  );
}
