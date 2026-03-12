import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";

export type RunRecord = {
  run_id: string;
  spec_hash: string;
  spec_json: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const DB_DIR = path.resolve(MODULE_DIR, "../../db");
const DB_PATH = path.join(DB_DIR, "runs.db");

fs.mkdirSync(DB_DIR, { recursive: true });

const db = new Database(DB_PATH);

db.exec(`
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  spec_hash TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);
`);

const insertRunStmt = db.prepare(`
  INSERT INTO runs (run_id, spec_hash, spec_json, status, created_at)
  VALUES (?, ?, ?, 'queued', ?)
`);

const selectRunStmt = db.prepare(`
  SELECT run_id, spec_hash, spec_json, status, created_at, started_at, completed_at
  FROM runs
  WHERE run_id = ?
`);

const listRunsBaseStmt = db.prepare(`
  SELECT run_id, spec_hash, spec_json, status, created_at, started_at, completed_at
  FROM runs
  ORDER BY created_at DESC
`);

const markRunningStmt = db.prepare(`
  UPDATE runs
  SET status = 'running', started_at = COALESCE(started_at, ?)
  WHERE run_id = ?
`);

const markCompletedStmt = db.prepare(`
  UPDATE runs
  SET status = 'completed', completed_at = ?
  WHERE run_id = ?
`);

const markFailedStmt = db.prepare(`
  UPDATE runs
  SET status = 'failed', completed_at = ?
  WHERE run_id = ?
`);

export function insertRun(runId: string, specHash: string, spec: unknown, createdAt: string): void {
  insertRunStmt.run(runId, specHash, JSON.stringify(spec), createdAt);
}

export function getRun(runId: string): RunRecord | undefined {
  return selectRunStmt.get(runId) as RunRecord | undefined;
}

export function listRuns(): RunRecord[] {
  return listRunsBaseStmt.all() as RunRecord[];
}

export function markRunRunning(runId: string, startedAt: string): void {
  markRunningStmt.run(startedAt, runId);
}

export function markRunCompleted(runId: string, completedAt: string): void {
  markCompletedStmt.run(completedAt, runId);
}

export function markRunFailed(runId: string, completedAt: string): void {
  markFailedStmt.run(completedAt, runId);
}