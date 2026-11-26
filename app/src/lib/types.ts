export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  kind: string;
  name: string;
  params: Record<string, unknown>;
  state: JobState;
  stages: string[];
  completed_stages: string[];
  stage: string | null;
  stage_progress: number;
  progress: number;
  message: string;
  error: string | null;
  cancel_requested: boolean;
  created_at: number;
  started_at: number | null;
  updated_at: number;
  finished_at: number | null;
}

export interface LogEntry {
  id: number;
  job_id: string;
  ts: number;
  level: "info" | "warning" | "error" | string;
  message: string;
}

export type WorkerEvent = { type: "job"; job: Job } | { type: "log"; log: LogEntry };

export interface SourceDesc {
  id: string;
  name: string;
  resolution_m: number | null;
  licence: string;
  licence_url: string;
  attribution: string;
  coverage: string;
  note: string;
  fallback: boolean;
}

export interface Plan {
  ok: boolean;
  problems: string[];
  mode?: "route" | "area";
  area_km2?: number;
  route_km?: number | null;
  size_km?: [number, number];
  layers: Partial<Record<"elevation" | "imagery" | "vectors", { chosen: SourceDesc; fallbacks: SourceDesc[] }>>;
  est_download_mb?: number;
  est_minutes?: number;
  ids?: { ac: string; beamng: string };
  outline?: GeoJSON.FeatureCollection;
}

export interface Place {
  name: string;
  lat: number;
  lon: number;
  bbox: [number, number, number, number] | null;
}

export interface RouteResult {
  coords: [number, number][];
  km: number;
  routed: boolean;
  warning: string;
}
