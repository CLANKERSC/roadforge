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
