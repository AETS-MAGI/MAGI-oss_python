import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
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
export function insertRun(runId, specHash, spec, createdAt) {
    insertRunStmt.run(runId, specHash, JSON.stringify(spec), createdAt);
}
export function getRun(runId) {
    return selectRunStmt.get(runId);
}
export function listRuns() {
    return listRunsBaseStmt.all();
}
export function markRunRunning(runId, startedAt) {
    markRunningStmt.run(startedAt, runId);
}
export function markRunCompleted(runId, completedAt) {
    markCompletedStmt.run(completedAt, runId);
}
export function markRunFailed(runId, completedAt) {
    markFailedStmt.run(completedAt, runId);
}
