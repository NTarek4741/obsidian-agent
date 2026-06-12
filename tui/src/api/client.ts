import type {
  HealthResp,
  ConfigReq,
  ConfigResp,
  JobResp,
  TopicReq,
  NotePathReq,
  JobStatusResp,
  MachinesResp,
} from "../types/index.js";

export class APIClient {
  baseURL: string;

  constructor(baseURL: string) {
    this.baseURL = baseURL.replace(/\/$/, "");
  }

  async postJSON<T>(path: string, body: unknown): Promise<T> {
    const resp = await fetch(`${this.baseURL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }
    return JSON.parse(text) as T;
  }

  async getJSON<T>(path: string): Promise<T> {
    const resp = await fetch(`${this.baseURL}${path}`);
    const text = await resp.text();
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }
    return JSON.parse(text) as T;
  }

  healthCheck(): Promise<HealthResp> {
    return this.getJSON<HealthResp>("/health");
  }

  saveConfig(req: ConfigReq): Promise<ConfigResp> {
    return this.postJSON<ConfigResp>("/config", req);
  }

  startPodcast(notePath: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/podcast", { note_path: notePath } as NotePathReq);
  }

  startFlashcard(notePath: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/flashcard", { note_path: notePath } as NotePathReq);
  }

  startDeepResearch(topic: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/research/deep", { topic } as TopicReq);
  }

  startFastResearch(topic: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/research/fast", { topic } as TopicReq);
  }

  startMindMap(notePath: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/mind-map", { note_path: notePath } as NotePathReq);
  }

  startTranscribe(content: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/transcribe", { content });
  }

  startChat(question: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/chat", { question });
  }

  getMachines(): Promise<MachinesResp> {
    return this.getJSON<MachinesResp>("/machines");
  }

  pollJob(jobID: string): Promise<JobStatusResp> {
    return this.getJSON<JobStatusResp>(`/jobs/${jobID}`);
  }
}
