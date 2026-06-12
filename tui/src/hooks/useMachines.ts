import { useEffect, useState } from "react";
import type { APIClient } from "../api/client.js";
import type { MachineInfo } from "../types/index.js";

// The backend serves its in-process snapshot (no Dedalus calls on our poll
// path — it rate-limits real phase refreshes itself), so 5s is cheap.
const POLL_MS = 5000;

export function useMachines(client: APIClient, connected: boolean): MachineInfo[] {
  const [machines, setMachines] = useState<MachineInfo[]>([]);

  useEffect(() => {
    if (!connected) return;
    let alive = true;
    const tick = async () => {
      try {
        const resp = await client.getMachines();
        if (alive) setMachines(resp.machines);
      } catch {
        // backend briefly unavailable — keep the last snapshot
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [client, connected]);

  return machines;
}
