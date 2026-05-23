import { spawn } from "child_process";
import { useState, useEffect, useRef, useCallback } from "react";
import { APIClient } from "../api/client.js";
import type { HealthResp } from "../types/index.js";

export interface BackendState {
  connected: boolean;
  configured: boolean;
  vault: string | null;
  checking: boolean;
}

export function useBackend(backendURL: string, autoStart: boolean) {
  const [state, setState] = useState<BackendState>({
    connected: false,
    configured: false,
    vault: null,
    checking: true,
  });

  const clientRef = useRef(new APIClient(backendURL));
  const backendStartedRef = useRef(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const checkHealth = useCallback(async () => {
    setState((s) => ({ ...s, checking: true }));
    try {
      const resp = await clientRef.current.healthCheck();
      setState({
        connected: true,
        configured: resp.configured,
        vault: resp.vault,
        checking: false,
      });
    } catch {
      setState((s) => ({ ...s, connected: false, checking: false }));
    }
  }, []);

  const startBackend = useCallback(() => {
    if (backendStartedRef.current || !autoStart) return;
    backendStartedRef.current = true;

    const cmd = spawn(
      "uv",
      ["run", "uvicorn", "api.app:app", "--port", "8000", "--log-level", "warning"],
      {
        cwd: "..",
        detached: true,
        stdio: "ignore",
      }
    );
    cmd.unref();

    // Retry health check after 3 seconds
    retryTimerRef.current = setTimeout(() => {
      checkHealth();
    }, 3000);
  }, [autoStart, checkHealth]);

  useEffect(() => {
    clientRef.current = new APIClient(backendURL);
    backendStartedRef.current = false;
    checkHealth();

    return () => {
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
  }, [backendURL, checkHealth]);

  useEffect(() => {
    if (!state.connected && autoStart && !backendStartedRef.current) {
      startBackend();
    }
  }, [state.connected, autoStart, startBackend]);

  return { state, checkHealth, client: clientRef.current };
}
