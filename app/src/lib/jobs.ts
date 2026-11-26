import type { Job, LogEntry, WorkerEvent } from "./types";

export interface JobsState {
  jobs: Record<string, Job>;
  logs: Record<string, LogEntry[]>;
}

export const emptyJobsState: JobsState = { jobs: {}, logs: {} };

const MAX_LOGS_PER_JOB = 500;

/** Apply one live event from the worker. Pure, so it is easy to test. */
export function applyEvent(state: JobsState, event: WorkerEvent): JobsState {
  if (event.type === "job") {
    const prev = state.jobs[event.job.id];
    if (prev && prev.updated_at > event.job.updated_at) return state; // stale, out of order
    return { ...state, jobs: { ...state.jobs, [event.job.id]: event.job } };
  }
  const existing = state.logs[event.log.job_id] ?? [];
  if (existing.some((l) => l.id === event.log.id)) return state;
  const next = [...existing, event.log].slice(-MAX_LOGS_PER_JOB);
  return { ...state, logs: { ...state.logs, [event.log.job_id]: next } };
}

export function withJobs(state: JobsState, jobs: Job[]): JobsState {
  const merged = { ...state.jobs };
  for (const job of jobs) {
    const prev = merged[job.id];
    if (!prev || prev.updated_at <= job.updated_at) merged[job.id] = job;
  }
  return { ...state, jobs: merged };
}

export function sortedJobs(state: JobsState): Job[] {
  return Object.values(state.jobs).sort((a, b) => b.created_at - a.created_at);
}

/** Rough time left in seconds from progress so far, or null when unknown. */
export function etaSeconds(job: Job, nowSec: number): number | null {
  if (job.state !== "running" || !job.started_at || job.progress <= 0.02) return null;
  const elapsed = nowSec - job.started_at;
  return Math.max(0, (elapsed / job.progress) * (1 - job.progress));
}

export function formatDuration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
