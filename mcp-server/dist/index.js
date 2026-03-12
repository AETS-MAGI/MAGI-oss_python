import crypto from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { URL, fileURLToPath } from "node:url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { getRun, insertRun, listRuns, markRunCompleted, markRunFailed, markRunRunning } from "./db.js";
const MCP_SERVER_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ARTIFACT_BASE_ROOT = path.resolve(MCP_SERVER_DIR, "../../tank/artifacts");
const ARTIFACT_RUNS_ROOT = path.join(ARTIFACT_BASE_ROOT, "runs");
const ONE_PAGER_PATH = path.resolve(MCP_SERVER_DIR, "../PRESENTATION_ONE_PAGER.md");
const NODES_CONFIG_PATH = path.resolve(MCP_SERVER_DIR, "../nodes.yaml");
const DOCS_REF_ROOT = path.resolve(MCP_SERVER_DIR, "../../tank/docs-ref");
function canonicalJson(value) {
    if (Array.isArray(value)) {
        return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
    }
    if (value && typeof value === "object") {
        const entries = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
        return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(",")}}`;
    }
    return JSON.stringify(value);
}
function makeSpecHash(spec) {
    const digest = crypto.createHash("sha256").update(canonicalJson(spec)).digest("hex");
    return digest.slice(0, 8);
}
function isString(value) {
    return typeof value === "string";
}
function isNumber(value) {
    return typeof value === "number";
}
function toRunId(specHash) {
    const date = new Date();
    const iso = date.toISOString();
    const ymd = iso.slice(0, 10).replaceAll("-", "");
    const hms = iso.slice(11, 19).replaceAll(":", "");
    return `${ymd}-${hms}-${specHash}`;
}
function asText(payload) {
    return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}
function parseNodesYaml(input) {
    const nodes = {};
    const lines = input.split(/\r?\n/);
    let inNodes = false;
    let currentNodeId = null;
    for (const rawLine of lines) {
        const line = rawLine.replace(/\t/g, "    ");
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) {
            continue;
        }
        if (!inNodes) {
            if (/^nodes:\s*$/.test(trimmed)) {
                inNodes = true;
            }
            continue;
        }
        const nodeMatch = line.match(/^\s{2}([A-Za-z0-9_.-]+):\s*$/);
        if (nodeMatch) {
            currentNodeId = nodeMatch[1];
            nodes[currentNodeId] = {};
            continue;
        }
        const propertyMatch = line.match(/^\s{4}([A-Za-z0-9_]+):\s*(.+)\s*$/);
        if (propertyMatch && currentNodeId) {
            const key = propertyMatch[1];
            let value = propertyMatch[2].trim();
            value = value.replace(/^"(.*)"$/, "$1");
            value = value.replace(/^'(.*)'$/, "$1");
            if (key === "port") {
                const asNumber = Number(value);
                if (!Number.isNaN(asNumber)) {
                    nodes[currentNodeId].port = asNumber;
                }
            }
            else if (key === "host" || key === "user" || key === "workdir" || key === "compute_runner" || key === "datasets_dir" || key === "models_dir" || key === "backend") {
                nodes[currentNodeId][key] = value;
            }
            else if (key === "aliases") {
                nodes[currentNodeId].aliases = value.split(/[\s,]+/).filter(Boolean);
            }
        }
    }
    const validated = {};
    for (const [nodeId, item] of Object.entries(nodes)) {
        if (item.host && item.user && item.workdir && item.compute_runner) {
            validated[nodeId] = {
                host: item.host,
                user: item.user,
                workdir: item.workdir,
                compute_runner: item.compute_runner,
                port: item.port ?? 22,
                datasets_dir: item.datasets_dir,
                models_dir: item.models_dir,
                aliases: item.aliases,
                backend: item.backend
            };
        }
    }
    // Fail-fast on duplicate aliases
    const aliasIndex = new Map();
    for (const [nodeId, config] of Object.entries(validated)) {
        for (const alias of config.aliases ?? []) {
            if (validated[alias]) {
                throw new Error(`alias '${alias}' in node '${nodeId}' conflicts with existing node id '${alias}'`);
            }
            if (aliasIndex.has(alias)) {
                throw new Error(`duplicate alias '${alias}' found in nodes '${aliasIndex.get(alias)}' and '${nodeId}'`);
            }
            aliasIndex.set(alias, nodeId);
        }
    }
    return validated;
}
function shellSingleQuote(value) {
    return `'${value.replaceAll("'", "'\\''")}'`;
}
function runFixedCommand(command, args, timeoutMs = 600_000) {
    return execFileSync(command, args, {
        encoding: "utf-8",
        timeout: timeoutMs,
        stdio: ["ignore", "pipe", "pipe"]
    }).trim();
}
function timeoutFromEnv(name, defaultMs) {
    const raw = process.env[name];
    if (!raw) {
        return defaultMs;
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return defaultMs;
    }
    return Math.floor(parsed);
}
function isRunIdConflictError(error) {
    if (!(error instanceof Error)) {
        return false;
    }
    const maybeCode = error.code;
    if (maybeCode === "SQLITE_CONSTRAINT_PRIMARYKEY" || maybeCode === "SQLITE_CONSTRAINT_UNIQUE") {
        return true;
    }
    return /UNIQUE constraint failed: runs\.run_id/.test(error.message);
}
function sleepMs(ms) {
    const shared = new SharedArrayBuffer(4);
    const view = new Int32Array(shared);
    Atomics.wait(view, 0, 0, ms);
}
function runCapture(command, args, timeoutMs = 600_000) {
    const result = spawnSync(command, args, {
        encoding: "utf-8",
        timeout: timeoutMs,
        stdio: ["ignore", "pipe", "pipe"]
    });
    return {
        stdout: (result.stdout ?? "").trim(),
        stderr: (result.stderr ?? "").trim(),
        exitCode: result.status ?? 1,
        signal: result.signal ?? null,
        spawnError: result.error?.message ?? null
    };
}
function stageLogEntry(stageName, r) {
    const parts = [`=== ${stageName} (exit ${r.exitCode}) ===`];
    if (r.stdout)
        parts.push(`--- stdout ---\n${r.stdout}`);
    if (r.stderr)
        parts.push(`--- stderr ---\n${r.stderr}`);
    if (r.signal)
        parts.push(`signal: ${r.signal}`);
    if (r.spawnError)
        parts.push(`spawn_error: ${r.spawnError}`);
    return parts.join("\n") + "\n\n";
}
function resolveNodeId(nodes, nodeId) {
    if (nodes[nodeId])
        return { resolvedId: nodeId, config: nodes[nodeId] };
    for (const [id, config] of Object.entries(nodes)) {
        if (config.aliases?.includes(nodeId))
            return { resolvedId: id, config };
    }
    return undefined;
}
function ensureSafeRunId(runId) {
    if (!/^\d{8}-\d{6}-[0-9a-f]{8}$/i.test(runId)) {
        throw new Error(`invalid run_id format: ${runId}`);
    }
}
function canonicalRunArtifactDir(runId) {
    return path.join(ARTIFACT_RUNS_ROOT, runId);
}
function legacyRunArtifactDir(runId) {
    return path.join(ARTIFACT_BASE_ROOT, runId);
}
function resolveRunArtifactDir(runId) {
    const canonicalDir = canonicalRunArtifactDir(runId);
    if (fs.existsSync(canonicalDir)) {
        return canonicalDir;
    }
    const legacyDir = legacyRunArtifactDir(runId);
    if (fs.existsSync(legacyDir)) {
        return legacyDir;
    }
    return canonicalDir;
}
function ensureLegacyCompatSymlink(runId) {
    const canonicalDir = canonicalRunArtifactDir(runId);
    const legacyDir = legacyRunArtifactDir(runId);
    if (legacyDir === canonicalDir) {
        return;
    }
    if (fs.existsSync(legacyDir)) {
        return;
    }
    const relativeTarget = path.relative(path.dirname(legacyDir), canonicalDir);
    try {
        fs.symlinkSync(relativeTarget, legacyDir, "dir");
    }
    catch {
        // Compatibility link is best-effort; canonical path remains source of truth.
    }
}
function loadNodesConfig() {
    if (!fs.existsSync(NODES_CONFIG_PATH)) {
        throw new Error(`nodes.yaml not found: ${NODES_CONFIG_PATH}`);
    }
    return parseNodesYaml(fs.readFileSync(NODES_CONFIG_PATH, "utf-8"));
}
function resolveDocsRefPath(relPath) {
    const normalized = path.normalize(relPath || ".").replace(/^[/\\]+/, "");
    const fullPath = path.join(DOCS_REF_ROOT, normalized);
    const resolvedRoot = path.resolve(DOCS_REF_ROOT);
    const resolvedFull = path.resolve(fullPath);
    if (!resolvedFull.startsWith(`${resolvedRoot}/`) && resolvedFull !== resolvedRoot) {
        throw new Error(`path escapes docs-ref root: ${relPath}`);
    }
    return resolvedFull;
}
function createResearchServer() {
    const server = new Server({
        name: "research-mcp-server",
        version: "0.1.0"
    }, {
        capabilities: {
            tools: {}
        }
    });
    server.setRequestHandler(ListToolsRequestSchema, async () => {
        return {
            tools: [
                {
                    name: "submit_experiment",
                    description: "Register an experiment spec and return deterministic run_id.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            spec: {
                                type: "object",
                                properties: {
                                    model_id: { type: "string" },
                                    dataset: { type: "string" },
                                    compute_node: { type: "string" },
                                    mode: { type: "string", enum: ["moe", "dense"] },
                                    quantization: { type: "string" },
                                    seed: { type: "number" },
                                    gen_params: {
                                        type: "object",
                                        properties: {
                                            temperature: { type: "number" },
                                            top_p: { type: "number" },
                                            top_k: { type: ["number", "null"] },
                                            max_new_tokens: { type: "number" },
                                            seed: { type: ["number", "null"] },
                                            deterministic_intent: { type: "boolean" },
                                            min_p: { type: ["number", "null"] },
                                            stop: { type: ["array", "null"], items: { type: "string" } },
                                            repetition_penalty: { type: "number" },
                                            presence_penalty: { type: "number" },
                                            frequency_penalty: { type: "number" }
                                        },
                                        required: ["temperature", "top_p", "max_new_tokens", "deterministic_intent"],
                                        additionalProperties: false
                                    },
                                    tasks: {
                                        type: "array",
                                        items: {
                                            type: "object",
                                            properties: {
                                                task_id: { type: "string" },
                                                constraints: { type: "object" }
                                            },
                                            required: ["task_id"],
                                            additionalProperties: true
                                        }
                                    }
                                },
                                required: ["model_id", "dataset"]
                            }
                        },
                        required: ["spec"]
                    }
                },
                {
                    name: "get_status",
                    description: "Get job status by run_id.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            run_id: { type: "string" }
                        },
                        required: ["run_id"]
                    }
                },
                {
                    name: "fetch_artifacts",
                    description: "Load result.json for run_id from artifact storage.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            run_id: { type: "string" }
                        },
                        required: ["run_id"]
                    }
                },
                {
                    name: "list_runs",
                    description: "List registered runs with optional filtering.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            model_id: { type: "string" },
                            dataset: { type: "string" },
                            status: { type: "string" },
                            limit: { type: "number" }
                        },
                        additionalProperties: false
                    }
                },
                {
                    name: "get_one_pager",
                    description: "Get one-page presentation summary (implementation, verification, evidence).",
                    inputSchema: {
                        type: "object",
                        properties: {},
                        additionalProperties: false
                    }
                },
                {
                    name: "run_compute",
                    description: "Run fixed compute-runner commands on a compute node over SSH and integrate artifacts.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            run_id: { type: "string" },
                            node_id: { type: "string" }
                        },
                        required: ["run_id", "node_id"],
                        additionalProperties: false
                    }
                },
                {
                    name: "list_docs_ref",
                    description: "List files and directories under tank/docs-ref.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            subpath: { type: "string" }
                        },
                        additionalProperties: false
                    }
                },
                {
                    name: "read_docs_ref",
                    description: "Read a file under tank/docs-ref with optional head/tail bytes.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            path: { type: "string" },
                            head_bytes: { type: "number" },
                            tail_bytes: { type: "number" }
                        },
                        required: ["path"],
                        additionalProperties: false
                    }
                }
            ]
        };
    });
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
        const name = request.params.name;
        const args = (request.params.arguments ?? {});
        if (name === "submit_experiment") {
            const spec = args.spec;
            if (!spec || !spec.model_id || !spec.dataset) {
                return asText({ error: "spec.model_id and spec.dataset are required" });
            }
            const specHash = makeSpecHash(spec);
            const maxAttempts = 5;
            let runId = "";
            let createdAt = "";
            let inserted = false;
            for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
                runId = toRunId(specHash);
                createdAt = new Date().toISOString();
                const artifactDir = canonicalRunArtifactDir(runId);
                fs.mkdirSync(artifactDir, { recursive: true });
                fs.writeFileSync(path.join(artifactDir, "spec.json"), `${canonicalJson(spec)}\n`, "utf-8");
                ensureLegacyCompatSymlink(runId);
                try {
                    insertRun(runId, specHash, spec, createdAt);
                    inserted = true;
                    break;
                }
                catch (error) {
                    if (!isRunIdConflictError(error) || attempt === maxAttempts) {
                        throw error;
                    }
                    sleepMs(1100);
                }
            }
            if (!inserted) {
                return asText({ error: "failed to allocate unique run_id" });
            }
            return asText({
                run_id: runId,
                spec_hash: specHash,
                status: "queued",
                created_at: createdAt,
                control_plane: {
                    hostname: os.hostname()
                }
            });
        }
        if (name === "get_status") {
            const runId = args.run_id;
            if (typeof runId !== "string") {
                return asText({ error: "run_id is required" });
            }
            const run = getRun(runId);
            if (!run) {
                return asText({ error: `run_id not found: ${runId}` });
            }
            return asText({
                run_id: run.run_id,
                status: run.status,
                started_at: run.started_at,
                completed_at: run.completed_at,
                created_at: run.created_at
            });
        }
        if (name === "fetch_artifacts") {
            const runId = args.run_id;
            if (typeof runId !== "string") {
                return asText({ error: "run_id is required" });
            }
            try {
                ensureSafeRunId(runId);
            }
            catch (error) {
                return asText({ error: String(error) });
            }
            const resultPath = path.join(resolveRunArtifactDir(runId), "result.json");
            if (!fs.existsSync(resultPath)) {
                return asText({ error: `result.json not found: ${resultPath}` });
            }
            const result = JSON.parse(fs.readFileSync(resultPath, "utf-8"));
            return asText({ run_id: runId, result });
        }
        if (name === "list_runs") {
            const modelId = typeof args.model_id === "string" ? args.model_id : undefined;
            const dataset = typeof args.dataset === "string" ? args.dataset : undefined;
            const status = typeof args.status === "string" ? args.status : undefined;
            const limit = typeof args.limit === "number" ? args.limit : 50;
            const runs = listRuns()
                .map((row) => ({
                run_id: row.run_id,
                spec_hash: row.spec_hash,
                status: row.status,
                created_at: row.created_at,
                spec: JSON.parse(row.spec_json)
            }))
                .filter((row) => (modelId ? row.spec.model_id === modelId : true))
                .filter((row) => (dataset ? row.spec.dataset === dataset : true))
                .filter((row) => (status ? row.status === status : true))
                .slice(0, Math.max(1, Math.min(500, limit)));
            return asText({ runs });
        }
        if (name === "get_one_pager") {
            if (!fs.existsSync(ONE_PAGER_PATH)) {
                return asText({ error: `one-pager not found: ${ONE_PAGER_PATH}` });
            }
            const markdown = fs.readFileSync(ONE_PAGER_PATH, "utf-8");
            return asText({
                title: "IEICE発表用 1ページ要約（実装・検証・証拠）",
                path: ONE_PAGER_PATH,
                markdown
            });
        }
        if (name === "run_compute") {
            const runId = args.run_id;
            const nodeId = args.node_id;
            if (typeof runId !== "string" || typeof nodeId !== "string") {
                return asText({ error: "run_id and node_id are required" });
            }
            const tsStart = new Date().toISOString();
            let stderrLog = `# compute.stderr.log  run_id=${runId}  node_id=${nodeId}  ts_start=${tsStart}\n\n`;
            const stageExits = {};
            const stageSignals = {};
            const stageSpawnErrors = {};
            let failedStage = "";
            let localRunDir = "";
            let resolvedNodeId = nodeId;
            const recordStage = (name, r) => {
                stageExits[name] = r.exitCode;
                stageSignals[name] = r.signal;
                stageSpawnErrors[name] = r.spawnError;
                stderrLog += stageLogEntry(name.toUpperCase(), r);
            };
            const writeEvidence = () => {
                if (!localRunDir)
                    return;
                try {
                    fs.writeFileSync(path.join(localRunDir, "compute.stderr.log"), stderrLog, "utf-8");
                    fs.writeFileSync(path.join(localRunDir, "compute.exit.json"), `${JSON.stringify({
                        run_id: runId,
                        requested_node_id: nodeId,
                        resolved_node_id: resolvedNodeId,
                        ts_start: tsStart,
                        ts_end: new Date().toISOString(),
                        stage_exits: stageExits,
                        stage_signals: stageSignals,
                        stage_spawn_errors: stageSpawnErrors,
                        failed_stage: failedStage || null
                    }, null, 2)}\n`, "utf-8");
                }
                catch { /* best-effort */ }
            };
            const writeFailureResult = (errorSummary) => {
                if (!localRunDir)
                    return;
                try {
                    const failureResult = {
                        status: "failed",
                        run_id: runId,
                        requested_node_id: nodeId,
                        resolved_node_id: resolvedNodeId,
                        failed_stage: failedStage || null,
                        stage_exits: stageExits,
                        error_summary: errorSummary,
                        ts_start: tsStart,
                        ts_end: new Date().toISOString(),
                        artifact_dir: localRunDir,
                        compute_exit_path: path.join(localRunDir, "compute.exit.json"),
                        stderr_log_path: path.join(localRunDir, "compute.stderr.log")
                    };
                    fs.writeFileSync(path.join(localRunDir, "result.json"), `${JSON.stringify(failureResult, null, 2)}\n`, "utf-8");
                }
                catch { /* best-effort */ }
            };
            try {
                // Set up artifact dir before ensureSafeRunId so failure artifacts work even on invalid format.
                // Guard with the same regex so we never create a dir for a path-traversal input.
                if (/^\d{8}-\d{6}-[0-9a-f]{8}$/i.test(runId)) {
                    localRunDir = resolveRunArtifactDir(runId);
                    fs.mkdirSync(localRunDir, { recursive: true });
                }
                ensureSafeRunId(runId);
                const run = getRun(runId);
                if (!run) {
                    failedStage = "precheck_run";
                    writeEvidence();
                    writeFailureResult(`run_id not found: ${runId}`);
                    return asText({ error: `run_id not found: ${runId}` });
                }
                const nodes = loadNodesConfig();
                const resolution = resolveNodeId(nodes, nodeId);
                if (!resolution) {
                    failedStage = "precheck_node";
                    writeEvidence();
                    writeFailureResult(`node_id not found in nodes.yaml: ${nodeId}`);
                    return asText({ error: `node_id not found in nodes.yaml: ${nodeId}` });
                }
                const { resolvedId, config: node } = resolution;
                resolvedNodeId = resolvedId;
                const localSpecPath = path.join(localRunDir, "spec.json");
                if (!fs.existsSync(localSpecPath)) {
                    failedStage = "precheck_spec";
                    writeEvidence();
                    writeFailureResult(`spec.json not found for run: ${localSpecPath}`);
                    return asText({ error: `spec.json not found for run: ${localSpecPath}` });
                }
                ensureLegacyCompatSymlink(runId);
                const startedAt = new Date().toISOString();
                markRunRunning(runId, startedAt);
                const target = `${node.user}@${node.host}`;
                const remoteDir = `${node.workdir}/${runId}`;
                const remoteSpec = `${remoteDir}/spec.json`;
                const remoteEnv = `${remoteDir}/env.json`;
                const remoteResponses = `${remoteDir}/responses.jsonl`;
                const sshPort = String(node.port);
                const timeoutSshPrep = timeoutFromEnv("RUN_COMPUTE_TIMEOUT_SSH_PREP_MS", 120_000);
                const timeoutScp = timeoutFromEnv("RUN_COMPUTE_TIMEOUT_SCP_MS", 300_000);
                const timeoutEnv = timeoutFromEnv("RUN_COMPUTE_TIMEOUT_ENV_MS", 180_000);
                const timeoutRun = timeoutFromEnv("RUN_COMPUTE_TIMEOUT_RUN_MS", 7_200_000);
                const timeoutIntegrate = timeoutFromEnv("RUN_COMPUTE_TIMEOUT_INTEGRATE_MS", 600_000);
                // ── Stage 0: probe (test -x + --help) ─────────────────────────────
                {
                    const r = runCapture("ssh", ["-p", sshPort, target,
                        `test -x ${shellSingleQuote(node.compute_runner)} && ${shellSingleQuote(node.compute_runner)} --help`
                    ], timeoutSshPrep);
                    recordStage("probe", r);
                    if (r.exitCode !== 0) {
                        failedStage = "probe";
                        writeEvidence();
                        writeFailureResult(`compute_runner probe failed (exit ${r.exitCode})`);
                        markRunFailed(runId, new Date().toISOString());
                        return asText({ run_id: runId, node_id: nodeId, status: "failed", failed_stage: failedStage, error: `compute_runner probe failed (exit ${r.exitCode})` });
                    }
                }
                // ── Stage 1: mkdir + SCP spec ──────────────────────────────────────
                {
                    const r = runCapture("ssh", ["-p", sshPort, target, `mkdir -p ${shellSingleQuote(remoteDir)}`], timeoutSshPrep);
                    recordStage("mkdir", r);
                    if (r.exitCode !== 0) {
                        failedStage = "ssh_prep";
                        writeEvidence();
                        writeFailureResult(`mkdir failed (exit ${r.exitCode})`);
                        markRunFailed(runId, new Date().toISOString());
                        return asText({ run_id: runId, node_id: nodeId, status: "failed", failed_stage: failedStage, error: `mkdir failed (exit ${r.exitCode})` });
                    }
                }
                {
                    const r = runCapture("scp", ["-P", sshPort, localSpecPath, `${target}:${remoteSpec}`], timeoutScp);
                    recordStage("scp_spec", r);
                    if (r.exitCode !== 0) {
                        failedStage = "scp_spec";
                        writeEvidence();
                        writeFailureResult(`scp spec failed (exit ${r.exitCode})`);
                        markRunFailed(runId, new Date().toISOString());
                        return asText({ run_id: runId, node_id: nodeId, status: "failed", failed_stage: failedStage, error: `scp spec failed (exit ${r.exitCode})` });
                    }
                }
                // ── Stage 2: env + immediate SCP of env.json ──────────────────────
                {
                    const r = runCapture("ssh", ["-p", sshPort, target,
                        `${shellSingleQuote(node.compute_runner)} env --out ${shellSingleQuote(remoteEnv)}`
                    ], timeoutEnv);
                    recordStage("env", r);
                    // Immediately copy env.json back (best-effort, even if env failed)
                    const se = runCapture("scp", ["-P", sshPort, `${target}:${remoteEnv}`, path.join(localRunDir, "env.json")], timeoutScp);
                    recordStage("scp_env", se);
                    if (r.exitCode !== 0) {
                        failedStage = "env";
                        writeEvidence();
                        writeFailureResult(`compute_runner env failed (exit ${r.exitCode})`);
                        markRunFailed(runId, new Date().toISOString());
                        return asText({ run_id: runId, node_id: nodeId, status: "failed", failed_stage: failedStage, error: `compute_runner env failed (exit ${r.exitCode})` });
                    }
                }
                // ── Stage 3: run (capture exit code, never throw) ─────────────────
                let runExitCode;
                {
                    const runCmd = [
                        `${shellSingleQuote(node.compute_runner)} run`,
                        `--run-id ${shellSingleQuote(runId)}`,
                        `--spec ${shellSingleQuote(remoteSpec)}`,
                        `--outdir ${shellSingleQuote(remoteDir)}`
                    ];
                    if (node.datasets_dir)
                        runCmd.push(`--datasets-dir ${shellSingleQuote(node.datasets_dir)}`);
                    if (node.models_dir)
                        runCmd.push(`--models-dir ${shellSingleQuote(node.models_dir)}`);
                    const r = runCapture("ssh", ["-p", sshPort, target, runCmd.join(" ")], timeoutRun);
                    recordStage("run", r);
                    runExitCode = r.exitCode;
                    if (r.exitCode !== 0) {
                        failedStage = "run";
                    }
                }
                // ── Stage 4: SCP responses.jsonl (blocks integrate on failure) ────
                {
                    const r = runCapture("scp", ["-P", sshPort, `${target}:${remoteResponses}`, path.join(localRunDir, "responses.jsonl")], timeoutScp);
                    recordStage("scp_responses", r);
                    if (runExitCode === 0 && r.exitCode !== 0) {
                        failedStage = "scp_responses";
                        writeEvidence();
                        writeFailureResult(`scp responses.jsonl failed (exit ${r.exitCode})`);
                        markRunFailed(runId, new Date().toISOString());
                        return asText({
                            run_id: runId,
                            node_id: nodeId,
                            status: "failed",
                            failed_stage: failedStage,
                            error: `scp responses.jsonl failed (exit ${r.exitCode})`,
                            artifact_dir: localRunDir
                        });
                    }
                }
                // Always write evidence before proceeding
                writeEvidence();
                // ── Stage 5: integrate or write failure result.json ───────────────
                if (runExitCode !== 0) {
                    writeFailureResult(`compute_runner run exited with code ${runExitCode}`);
                    markRunFailed(runId, new Date().toISOString());
                    return asText({
                        run_id: runId,
                        node_id: nodeId,
                        status: "failed",
                        failed_stage: failedStage,
                        run_exit_code: runExitCode,
                        artifact_dir: localRunDir
                    });
                }
                const runnerPath = path.resolve(MCP_SERVER_DIR, "../.venv/bin/runner");
                if (!fs.existsSync(runnerPath)) {
                    throw new Error(`runner executable not found: ${runnerPath}`);
                }
                runFixedCommand(runnerPath, ["integrate", runId], timeoutIntegrate);
                markRunCompleted(runId, new Date().toISOString());
                return asText({
                    run_id: runId,
                    node_id: nodeId,
                    status: "completed",
                    artifact_dir: localRunDir
                });
            }
            catch (error) {
                writeEvidence();
                writeFailureResult(String(error));
                if (typeof runId === "string" && /^\d{8}-\d{6}-[0-9a-f]{8}$/i.test(runId)) {
                    markRunFailed(runId, new Date().toISOString());
                }
                return asText({
                    run_id: runId,
                    node_id: nodeId,
                    status: "failed",
                    error: String(error)
                });
            }
        }
        if (name === "list_docs_ref") {
            const subpath = isString(args.subpath) ? args.subpath : "";
            let targetDir;
            try {
                targetDir = resolveDocsRefPath(subpath);
            }
            catch (error) {
                return asText({ error: String(error) });
            }
            if (!fs.existsSync(targetDir)) {
                return asText({ error: `path not found: ${targetDir}` });
            }
            const stat = fs.statSync(targetDir);
            if (!stat.isDirectory()) {
                return asText({ error: `not a directory: ${targetDir}` });
            }
            const entries = fs.readdirSync(targetDir, { withFileTypes: true }).map((entry) => ({
                name: entry.name,
                type: entry.isDirectory() ? "dir" : "file"
            }));
            return asText({
                root: DOCS_REF_ROOT,
                subpath,
                entries
            });
        }
        if (name === "read_docs_ref") {
            const relPathRaw = args.path;
            if (!isString(relPathRaw)) {
                return asText({ error: "path is required" });
            }
            const relPath = relPathRaw;
            const headBytesRaw = args.head_bytes;
            const tailBytesRaw = args.tail_bytes;
            let headBytes = 2048;
            let tailBytes = 0;
            if (isNumber(headBytesRaw) && headBytesRaw > 0) {
                headBytes = Math.floor(headBytesRaw);
            }
            if (isNumber(tailBytesRaw) && tailBytesRaw > 0) {
                tailBytes = Math.floor(tailBytesRaw);
            }
            let fullPath;
            try {
                fullPath = resolveDocsRefPath(relPath);
            }
            catch (error) {
                return asText({ error: String(error) });
            }
            if (!fs.existsSync(fullPath)) {
                return asText({ error: `not found: ${fullPath}` });
            }
            const stat = fs.statSync(fullPath);
            if (stat.isDirectory()) {
                return asText({ error: `path is a directory: ${fullPath}` });
            }
            const size = stat.size;
            const isBinary = [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar", ".gz", ".bz2", ".xz"].some((ext) => fullPath.toLowerCase().endsWith(ext));
            if (isBinary) {
                return asText({
                    path: relPath,
                    full_path: fullPath,
                    size_bytes: size,
                    note: "binary file; content not inlined"
                });
            }
            if (size <= headBytes + tailBytes || tailBytes === 0) {
                const content = fs.readFileSync(fullPath, "utf-8");
                return asText({
                    path: relPath,
                    full_path: fullPath,
                    size_bytes: size,
                    content,
                    truncated: false
                });
            }
            const fd = fs.openSync(fullPath, "r");
            const headBuffer = Buffer.alloc(headBytes);
            const headRead = fs.readSync(fd, headBuffer, 0, headBytes, 0);
            let tailRead = 0;
            let tailText = "";
            if (tailBytes > 0 && size > tailBytes) {
                const tailBuffer = Buffer.alloc(tailBytes);
                tailRead = fs.readSync(fd, tailBuffer, 0, tailBytes, Math.max(0, size - tailBytes));
                tailText = tailBuffer.slice(0, tailRead).toString("utf-8");
            }
            fs.closeSync(fd);
            return asText({
                path: relPath,
                full_path: fullPath,
                size_bytes: size,
                head_bytes: headRead,
                head: headBuffer.slice(0, headRead).toString("utf-8"),
                tail_bytes: tailRead,
                tail: tailText,
                truncated: true
            });
        }
        return asText({ error: `unknown tool: ${name}` });
    });
    return server;
}
function sendJson(res, statusCode, body) {
    res.statusCode = statusCode;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.end(`${JSON.stringify(body)}\n`);
}
async function readJsonBody(req) {
    const chunks = [];
    for await (const chunk of req) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    if (chunks.length === 0) {
        return undefined;
    }
    const raw = Buffer.concat(chunks).toString("utf-8");
    return JSON.parse(raw);
}
async function startHttpServer(port, bindHost) {
    const streamableServer = createResearchServer();
    const streamableTransport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => crypto.randomUUID()
    });
    await streamableServer.connect(streamableTransport);
    const sseTransports = new Map();
    const httpServer = createServer(async (req, res) => {
        try {
            const method = req.method ?? "GET";
            const parsedUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? "127.0.0.1"}`);
            if (method === "GET" && parsedUrl.pathname === "/healthz") {
                sendJson(res, 200, { status: "ok" });
                return;
            }
            if (method === "GET" && parsedUrl.pathname === "/sse") {
                const sseServer = createResearchServer();
                const sseTransport = new SSEServerTransport("/messages", res);
                sseTransport.onclose = () => {
                    sseTransports.delete(sseTransport.sessionId);
                };
                await sseServer.connect(sseTransport);
                sseTransports.set(sseTransport.sessionId, sseTransport);
                await sseTransport.start();
                // Wait until the connection is closed
                await new Promise((resolve) => { res.on("close", resolve); });
                return;
            }
            if (method === "POST" && parsedUrl.pathname === "/messages") {
                const sessionId = parsedUrl.searchParams.get("sessionId") ?? "";
                const transport = sseTransports.get(sessionId);
                if (!transport) {
                    sendJson(res, 202, {
                        ok: true,
                        ignored: true,
                        reason: `stale or unknown sessionId: ${sessionId}`
                    });
                    return;
                }
                await transport.handlePostMessage(req, res);
                return;
            }
            if ((method === "GET" || method === "POST" || method === "DELETE") && parsedUrl.pathname === "/mcp") {
                const parsedBody = method === "POST" ? await readJsonBody(req) : undefined;
                await streamableTransport.handleRequest(req, res, parsedBody);
                return;
            }
            sendJson(res, 404, { error: "not found" });
        }
        catch (error) {
            if (!res.headersSent) {
                sendJson(res, 500, { error: String(error) });
            }
            else {
                res.end();
            }
        }
    });
    await new Promise((resolve, reject) => {
        httpServer.once("error", reject);
        httpServer.listen(port, bindHost, () => {
            process.stdout.write(`research-mcp-server listening on http://${bindHost}:${port}\n`);
            resolve();
        });
    });
}
async function main() {
    fs.mkdirSync(ARTIFACT_BASE_ROOT, { recursive: true });
    fs.mkdirSync(ARTIFACT_RUNS_ROOT, { recursive: true });
    const transportMode = process.env.MCP_TRANSPORT ?? "stdio";
    if (transportMode === "http") {
        const port = Number(process.env.MCP_PORT ?? "8080");
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
            throw new Error(`invalid MCP_PORT: ${String(process.env.MCP_PORT)}`);
        }
        const bindHost = process.env.MCP_BIND_HOST ?? "127.0.0.1";
        if (!bindHost.trim()) {
            throw new Error("invalid MCP_BIND_HOST: empty value");
        }
        await startHttpServer(port, bindHost);
        return;
    }
    if (transportMode !== "stdio") {
        throw new Error(`unsupported MCP_TRANSPORT: ${transportMode}`);
    }
    const server = createResearchServer();
    const transport = new StdioServerTransport();
    await server.connect(transport);
}
main().catch((error) => {
    process.stderr.write(`${String(error)}\n`);
    process.exit(1);
});
