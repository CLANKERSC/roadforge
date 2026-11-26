import type { Job, LogEntry, Place, Plan, RouteResult, WorkerEvent } from "./types";
import type { LonLat, SelectionPayload } from "./selection";

export interface WorkerInfo {
  url: string;
  token: string;
}

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Find the local worker. Inside the desktop app, the Rust shell started it and knows
 * its random port and token. In a plain browser (developer mode) they come from
 * app/.env.local, written by scripts/dev_worker.py.
 */
export async function getWorkerInfo(): Promise<WorkerInfo> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<WorkerInfo>("worker_info");
  }
  const url = import.meta.env.VITE_WORKER_URL as string | undefined;
  const token = import.meta.env.VITE_WORKER_TOKEN as string | undefined;
  if (!url || !token) {
    throw new Error("Worker address unknown. Run: python scripts/dev_worker.py");
  }
  return { url, token };
}

export class WorkerClient {
  constructor(private readonly info: WorkerInfo) {}

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(this.info.url + path, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.info.token}`,
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (!res.ok) {
      const detail = await res
        .json()
        .then((b: { detail?: unknown }) => (typeof b.detail === "string" ? b.detail : ""))
        .catch(() => "");
      throw new Error(detail || `${init.method ?? "GET"} ${path} failed: ${res.status}`);
    }
    return (await res.json()) as T;
  }

  health() {
    return fetch(this.info.url + "/health").then((r) => r.json() as Promise<{ status: string; version: string }>);
  }
  listJobs() {
    return this.req<Job[]>("/jobs");
  }
  logs(jobId: string) {
    return this.req<LogEntry[]>(`/jobs/${jobId}/logs`);
  }
  createDemoJob(name: string) {
    return this.req<Job>("/jobs", { method: "POST", body: JSON.stringify({ kind: "demo", name, params: {} }) });
  }
  plan(selection: SelectionPayload, name: string) {
    return this.req<Plan>("/plan", { method: "POST", body: JSON.stringify({ selection, name: name || "My map" }) });
  }
  search(query: string) {
    return this.req<{ places: Place[] }>(`/search?q=${encodeURIComponent(query)}`).then((r) => r.places);
  }
  route(waypoints: LonLat[]) {
    return this.req<RouteResult>("/route", { method: "POST", body: JSON.stringify({ waypoints }) });
  }
  createBuild(name: string, selection: SelectionPayload) {
    return this.req<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify({ kind: "build", name, params: { selection, name } }),
    });
  }
  /** A result file of a build (3D preview, reports). */
  async fileBytes(jobId: string, name: string): Promise<ArrayBuffer> {
    const res = await fetch(`${this.info.url}/jobs/${jobId}/files/${name}`, {
      headers: { Authorization: `Bearer ${this.info.token}` },
    });
    if (!res.ok) throw new Error(`${name} is not available (${res.status})`);
    return await res.arrayBuffer();
  }
  fileJson<T>(jobId: string, name: string) {
    return this.req<T>(`/jobs/${jobId}/files/${name}`);
  }
  cancel(jobId: string) {
    return this.req<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
  }
  rebuild(jobId: string) {
    return this.req<Job>(`/jobs/${jobId}/rebuild`, { method: "POST" });
  }

  /** Live updates via Server-Sent Events. Returns a function that closes the stream. */
  subscribe(onEvent: (e: WorkerEvent) => void, onStatus: (connected: boolean) => void): () => void {
    const es = new EventSource(`${this.info.url}/events?token=${encodeURIComponent(this.info.token)}`);
    const handle = (msg: MessageEvent<string>) => onEvent(JSON.parse(msg.data) as WorkerEvent);
    es.addEventListener("job", handle);
    es.addEventListener("log", handle);
    es.onopen = () => onStatus(true);
    es.onerror = () => onStatus(false); // EventSource reconnects by itself
    return () => es.close();
  }
}
