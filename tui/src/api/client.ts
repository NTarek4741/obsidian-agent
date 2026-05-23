import type {
  HealthResp,
  ConfigReq,
  ConfigResp,
  JobResp,
  TopicReq,
  NotePathReq,
  DirectResultResp,
  MindMapResp,
  JobStatusResp,
  JobListItemResp,
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

  fastResearch(topic: string): Promise<DirectResultResp> {
    return this.postJSON<DirectResultResp>("/research/fast", { topic } as TopicReq);
  }

  mindMap(notePath: string): Promise<MindMapResp> {
    return this.postJSON<MindMapResp>("/mind-map", { note_path: notePath } as NotePathReq);
  }

  startTranscribe(content: string): Promise<JobResp> {
    return this.postJSON<JobResp>("/transcribe", { content });
  }

  pollJob(jobID: string): Promise<JobStatusResp> {
    return this.getJSON<JobStatusResp>(`/jobs/${jobID}`);
  }

  listJobs(): Promise<JobListItemResp[]> {
    return this.getJSON<JobListItemResp[]>("/jobs");
  }
}
