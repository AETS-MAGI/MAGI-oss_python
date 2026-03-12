#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _lib import (
    append_jsonl,
    atomic_write_json,
    canonical_json,
    load_tasks,
    make_run_id,
    run_capture,
    run_ssh,
    spec_hash8,
    utc_now_rfc3339,
    write_csv,
)


def _shell_q(value: str) -> str:
    return shlex.quote(value)


def _parse_ssh_parts(ssh_str: str) -> tuple[str, list[str]]:
    """Parse an ssh string like 'root@host -p 13771 -i /path/key' into (user_host, extra_opts)."""
    parts = shlex.split(ssh_str)
    user_host = parts[0]
    extra_opts = parts[1:]
    return user_host, extra_opts


def _scp_cmd(ssh_str: str, src: str, remote_path: str) -> list[str]:
    """Build scp command that respects extra SSH options like -P and -i."""
    user_host, extra_opts = _parse_ssh_parts(ssh_str)
    cmd = ["scp", "-o", "StrictHostKeyChecking=no"]
    for i, opt in enumerate(extra_opts):
        if opt == "-p":
            # scp uses -P for port
            cmd.extend(["-P", extra_opts[i + 1]])
        elif i > 0 and extra_opts[i - 1] == "-p":
            continue  # already consumed
        else:
            cmd.append(opt)
    cmd.extend([src, f"{user_host}:{remote_path}"])
    return cmd


def _rsync_cmd(ssh_str: str, remote_path: str, local_path: str) -> list[str]:
    """Build rsync command that respects extra SSH options."""
    user_host, extra_opts = _parse_ssh_parts(ssh_str)
    ssh_cmd = "ssh -o StrictHostKeyChecking=no " + " ".join(shlex.quote(o) for o in extra_opts)
    return ["rsync", "-a", "-e", ssh_cmd, f"{user_host}:{remote_path}", local_path]


def remote_runner_script(
    remote_run_dir: str,
    model_tag: str,
    timeout_sec: int,
    limit_tasks: int,
    preflight_timeout_sec: int,
    preflight_retries: int,
    sleep_between_retries_sec: int,
    preflight_prompt: str,
) -> str:
    return f"""\
set -euo pipefail
unset MAGI_OLLAMA_STUB
python3 - <<'PY'
import json, subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

run_dir = Path({remote_run_dir!r})
model_tag = {model_tag!r}
timeout_sec = int({timeout_sec})
limit_tasks = int({limit_tasks})
preflight_timeout_sec = int({preflight_timeout_sec})
preflight_retries = int({preflight_retries})
sleep_between_retries_sec = int({sleep_between_retries_sec})
preflight_prompt = {preflight_prompt!r}
tasks = json.loads((run_dir / "tasks.json").read_text(encoding="utf-8"))
if limit_tasks > 0:
    tasks = tasks[:limit_tasks]
responses_path = run_dir / "responses.jsonl"
stderr_ok_path = run_dir / "compute.stderr.ok.log"
stderr_fail_path = run_dir / "compute.stderr.log"
exit_path = run_dir / "compute.exit.json"

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    tmp.replace(path)

failures = []
stderr_lines = []
# preflight: ollama responsiveness with retries (cold start safe)
preflight_ok = False
last_preflight_issue = "unknown"
for attempt in range(1, max(preflight_retries, 1) + 1):
    pre_t0 = time.monotonic()
    try:
        pre = subprocess.run(
            ["ollama", "run", model_tag, preflight_prompt],
            capture_output=True,
            text=True,
            timeout=preflight_timeout_sec,
            check=False,
        )
        pre_ms = int((time.monotonic() - pre_t0) * 1000)
        stderr_lines.append(
            f"[preflight] attempt={{attempt}}/{{preflight_retries}} cmd='ollama run {{model_tag}} <prompt>' exit={{pre.returncode}} elapsed_ms={{pre_ms}} stub_mode=off"
        )
        if pre.stderr.strip():
            stderr_lines.append(f"[preflight-stderr] {{pre.stderr.strip()}}")
        if pre.returncode == 0:
            preflight_ok = True
            break
        last_preflight_issue = f"preflight nonzero exit={{pre.returncode}} elapsed_ms={{pre_ms}}"
    except subprocess.TimeoutExpired:
        pre_ms = int((time.monotonic() - pre_t0) * 1000)
        stderr_lines.append(
            f"[preflight] attempt={{attempt}}/{{preflight_retries}} cmd='ollama run {{model_tag}} <prompt>' exit=timeout elapsed_ms={{pre_ms}} stub_mode=off"
        )
        last_preflight_issue = f"preflight timeout ({{preflight_timeout_sec}}s)"
    if not preflight_ok and attempt < max(preflight_retries, 1) and sleep_between_retries_sec > 0:
        time.sleep(sleep_between_retries_sec)

if not preflight_ok:
    exit_doc = {{
        "schema_version": "magi-compute-exit-v0",
        "run_id": run_dir.name,
        "created_at": now(),
        "failed_stage": "inference",
        "status": "failed_with_evidence",
        "reason": "ollama_unresponsive",
        "issues": [{{
            "kind": "semantic",
            "path": "/runner/inference/preflight",
            "message": last_preflight_issue,
        }}],
    }}
    write_text_atomic(stderr_fail_path, "\\n".join(stderr_lines))
    write_json_atomic(exit_path, exit_doc)
    raise SystemExit(1)

rows = []
for task in tasks:
    item_id = str(task.get("item_id", ""))
    prompt = str(task.get("prompt", ""))
    t0 = time.monotonic()
    cmd = ["ollama", "run", model_tag, prompt]
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        out = cp.stdout.strip()
        err = cp.stderr.strip()
        stderr_lines.append(
            f"[{{item_id}}] cmd='ollama run {model_tag} <prompt>' exit={{cp.returncode}} elapsed_ms={{elapsed_ms}} stub_mode=off"
        )
        if err:
            stderr_lines.append(f"[{{item_id}}-stderr] {{err}}")
        if cp.returncode != 0:
            failures.append(
                {{
                    "item_id": item_id,
                    "type": "nonzero_exit",
                    "exit_code": cp.returncode,
                    "elapsed_ms": elapsed_ms,
                }}
            )
            continue
        row = {{
            "schema_version": "magi-responses-v0",
            "run_id": run_dir.name,
            "item_id": item_id,
            "prompt": prompt,
            "raw_output": out,
            "created_at": now(),
        }}
        rows.append(row)
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        stderr_lines.append(
            f"[{{item_id}}] cmd='ollama run {model_tag} <prompt>' exit=timeout elapsed_ms={{elapsed_ms}} stub_mode=off"
        )
        failures.append(
            {{
                "item_id": item_id,
                "type": "timeout",
                "elapsed_ms": elapsed_ms,
            }}
        )

resp_tmp = responses_path.with_suffix(".jsonl.new")
with resp_tmp.open("w", encoding="utf-8") as rf:
    for row in rows:
        rf.write(json.dumps(row, ensure_ascii=False) + "\\n")
resp_tmp.replace(responses_path)

if failures:
    write_text_atomic(stderr_fail_path, "\\n".join(stderr_lines))
    exit_doc = {{
        "schema_version": "magi-compute-exit-v0",
        "run_id": run_dir.name,
        "created_at": now(),
        "failed_stage": "inference",
        "status": "failed_with_evidence",
        "reason": "one or more tasks failed",
        "issues": [
            {{
                "kind": "semantic",
                "path": "/runner/inference",
                "message": f"item_id={{m['item_id']}} type={{m['type']}} elapsed_ms={{m['elapsed_ms']}} exit={{m.get('exit_code') if 'exit_code' in m else 'n/a'}}",
            }}
            for m in failures
        ],
    }}
    write_json_atomic(exit_path, exit_doc)
    raise SystemExit(1)
else:
    write_text_atomic(stderr_ok_path, "\\n".join(stderr_lines))
PY
"""


def build_units(plan: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for node in plan["nodes"]:
        for temp in plan["temps"]:
            label = temp["label"]
            model_tag = plan["model_tags"][label]
            for epoch in range(1, int(plan["epochs"]) + 1):
                for rep in range(1, int(plan["replicates"]) + 1):
                    units.append(
                        {
                            "node_id": node["node_id"],
                            "ssh": node["ssh"],
                            "temp_label": label,
                            "temperature": temp["temperature"],
                            "model_tag": model_tag,
                            "epoch": epoch,
                            "replicate": rep,
                            "tasks_count": len(tasks),
                        }
                    )
    return units


def ensure_unique_run_id(hash8: str, runs_root: Path) -> str:
    for _ in range(5):
        rid = make_run_id(hash8)
        if not (runs_root / rid).exists():
            return rid
        time.sleep(1.1)
    raise RuntimeError("failed to allocate unique run_id")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    ap.add_argument(
        "--only",
        default="",
        help="Comma-separated temp labels to run (e.g. t0p1 or t0p0,t0p1)",
    )
    ap.add_argument(
        "--only-temp",
        default="",
        help="Alias of --only",
    )
    ap.add_argument(
        "--only-node",
        default="",
        help="Comma-separated node_id filter (e.g. zorya or zorya,eve)",
    )
    ap.add_argument(
        "--epochs",
        type=int,
        default=0,
        help="Override epochs for this execution (0 means use plan value)",
    )
    ap.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Cap total run units for quick smoke checks (0 means unlimited)",
    )
    ap.add_argument(
        "--limit-units",
        type=int,
        default=0,
        help="Alias of --max-runs",
    )
    ap.add_argument(
        "--limit-tasks",
        type=int,
        default=0,
        help="Limit tasks per run for smoke tests (0 means all tasks)",
    )
    ap.add_argument(
        "--timeout-sec-per-task",
        type=int,
        default=0,
        help="Override timeout_sec_per_task for this execution (0 means use plan value)",
    )
    ap.add_argument(
        "--preflight-timeout-sec",
        type=int,
        default=0,
        help="Override preflight timeout seconds (0 means use plan value)",
    )
    ap.add_argument(
        "--preflight-retries",
        type=int,
        default=0,
        help="Override preflight retries (0 means use plan value)",
    )
    ap.add_argument(
        "--sleep-between-retries",
        type=int,
        default=0,
        help="Sleep seconds between preflight retries (0 means use plan/default value)",
    )
    ap.add_argument(
        "--max-parallel-per-node",
        type=int,
        default=0,
        help="Reserved for future per-node fanout. Current runner executes per-node sequentially; must be 0 or 1.",
    )
    args = ap.parse_args()

    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = load_tasks(Path(plan["tasks_file"]))

    artifact_root = Path(plan["artifact_root"])
    plan_dir = artifact_root / "plans" / plan["plan_id"]
    runs_root = artifact_root / "runs"
    plan_dir.mkdir(parents=True, exist_ok=True)
    runs_root.mkdir(parents=True, exist_ok=True)

    atomic_write_json(plan_dir / "plan.json", plan)
    effective_plan = dict(plan)
    if args.epochs and args.epochs > 0:
        effective_plan["epochs"] = args.epochs
    if args.timeout_sec_per_task and args.timeout_sec_per_task > 0:
        effective_plan["timeout_sec_per_task"] = int(args.timeout_sec_per_task)
    if args.preflight_timeout_sec and args.preflight_timeout_sec > 0:
        effective_plan["preflight_timeout_sec"] = int(args.preflight_timeout_sec)
    if args.preflight_retries and args.preflight_retries > 0:
        effective_plan["preflight_retries"] = int(args.preflight_retries)
    if args.sleep_between_retries and args.sleep_between_retries > 0:
        effective_plan["sleep_between_retries"] = int(args.sleep_between_retries)
    if args.max_parallel_per_node not in (0, 1):
        raise ValueError("--max-parallel-per-node currently supports only 0 or 1")
    units = build_units(effective_plan, tasks)
    only_temp = args.only_temp.strip() or args.only.strip()
    if only_temp:
        selected = {x.strip() for x in only_temp.split(",") if x.strip()}
        units = [u for u in units if u["temp_label"] in selected]
    if args.only_node.strip():
        selected_nodes = {x.strip() for x in args.only_node.split(",") if x.strip()}
        units = [u for u in units if u["node_id"] in selected_nodes]
    unit_cap = args.max_runs if args.max_runs > 0 else args.limit_units
    if unit_cap and unit_cap > 0:
        units = units[:unit_cap]
    dispatch_table = plan_dir / "dispatch.table.jsonl"
    dispatch_exec = plan_dir / "dispatch.exec.jsonl"
    run_map_rows: list[dict[str, Any]] = []
    lock = threading.Lock()
    progress = {"done": 0}

    dispatch_table.write_text("", encoding="utf-8")
    for unit in units:
        append_jsonl(dispatch_table, unit)

    units_by_node: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        units_by_node.setdefault(unit["node_id"], []).append(unit)

    def worker(node_id: str, node_units: list[dict[str, Any]]) -> None:
        for idx, unit in enumerate(node_units, start=1):
            hashable_spec = {
                "dataset": Path(plan["tasks_file"]).name,
                "model_tag": unit["model_tag"],
                "temperature": unit["temperature"],
                "gen_params": effective_plan["gen_params"],
                "tasks_digest": spec_hash8(tasks),
            }
            rid = ensure_unique_run_id(spec_hash8(hashable_spec), runs_root)
            run_dir = runs_root / rid
            run_dir.mkdir(parents=True, exist_ok=True)

            spec_doc = {
                "run_id": rid,
                "spec": {
                    "dataset": Path(plan["tasks_file"]).name,
                    "model_id": unit["model_tag"],
                    "gen_params": {
                        **effective_plan["gen_params"],
                        "temperature": unit["temperature"],
                    },
                    "epoch": unit["epoch"],
                    "replicate": unit["replicate"],
                    "tasks_count": len(tasks) if args.limit_tasks <= 0 else min(len(tasks), args.limit_tasks),
                    "node_id": unit["node_id"],
                },
            }
            env_doc = {
                "schema_version": "magi-env-v0",
                "run_id": rid,
                "created_at": utc_now_rfc3339(),
                "runner": {"name": "py-batch-runner", "version": "stage01", "revision": "manual"},
                "backend_effective": {"engine": "ollama", "accel": "rocm"},
                "node_id": unit["node_id"],
                "node_ssh": unit["ssh"],
            }
            atomic_write_json(run_dir / "spec.json", spec_doc)
            atomic_write_json(run_dir / "env.json", env_doc)
            atomic_write_json(run_dir / "tasks.json", tasks)

            start = utc_now_rfc3339()
            rec = {
                "phase": "start",
                "run_id": rid,
                "plan_id": plan["plan_id"],
                "node_id": unit["node_id"],
                "host": unit["ssh"],
                "temp_label": unit["temp_label"],
                "temperature": unit["temperature"],
                "epoch": unit["epoch"],
                "replicate": unit["replicate"],
                "model_tag": unit["model_tag"],
                "run_dir": str(run_dir),
                "started_at": start,
                "status": "started",
            }
            with lock:
                append_jsonl(dispatch_exec, rec)

            shared_check = f"test -d {_shell_q(str(run_dir))} && test -w {_shell_q(str(run_dir))}"
            chk_code, _, chk_err = run_ssh(unit["ssh"], shared_check)
            remote_mode = "shared"
            remote_run_dir = str(run_dir)
            if chk_code != 0:
                remote_mode = "fallback_tmp"
                remote_run_dir = f"/tmp/magi_runs/{rid}"
                mk_cmd = f"mkdir -p {_shell_q(remote_run_dir)}"
                mk_code, _, mk_err = run_ssh(unit["ssh"], mk_cmd)
                if mk_code != 0:
                    end = utc_now_rfc3339()
                    with lock:
                        progress["done"] += 1
                        done = progress["done"]
                        total = len(units)
                        pct = (done / total * 100.0) if total else 100.0
                    rec_end = {
                        **rec,
                        "phase": "end",
                        "ended_at": end,
                        "duration_sec": 0.0,
                        "exit_code": 1,
                        "status": "failed",
                        "remote_mode": remote_mode,
                        "error": (
                            "remote run_dir not writable and fallback mkdir failed: "
                            f"cmd={mk_cmd!r} chk_err={chk_err[:200]} mk_err={mk_err[:200]}"
                        ),
                    }
                    with lock:
                        append_jsonl(dispatch_exec, rec_end)
                        print(
                            f"[progress] {done}/{total} ({pct:.1f}%) "
                            f"node={unit['node_id']} temp={unit['temp_label']} epoch={unit['epoch']} "
                            f"node_seq={idx}/{len(node_units)} run_id={rid} exit=1",
                            flush=True,
                        )
                    continue
                # copy tasks.json/spec/env to remote temp dir
                fallback_copy_failed = False
                for name in ("tasks.json", "spec.json", "env.json"):
                    src = run_dir / name
                    scp = _scp_cmd(unit["ssh"], str(src), f"{remote_run_dir}/{name}")
                    cp_code, _, cp_err = run_capture(scp, timeout=60)
                    if cp_code != 0:
                        end = utc_now_rfc3339()
                        with lock:
                            progress["done"] += 1
                            done = progress["done"]
                            total = len(units)
                            pct = (done / total * 100.0) if total else 100.0
                        rec_end = {
                            **rec,
                            "phase": "end",
                            "ended_at": end,
                            "duration_sec": 0.0,
                            "exit_code": 1,
                            "status": "failed",
                            "remote_mode": remote_mode,
                            "error": f"scp to remote fallback failed for {name}: {cp_err[:200]}",
                        }
                        with lock:
                            append_jsonl(dispatch_exec, rec_end)
                            print(
                                f"[progress] {done}/{total} ({pct:.1f}%) "
                                f"node={unit['node_id']} temp={unit['temp_label']} epoch={unit['epoch']} "
                                f"node_seq={idx}/{len(node_units)} run_id={rid} exit=1",
                                flush=True,
                            )
                        fallback_copy_failed = True
                        break
                if fallback_copy_failed:
                    continue

            t0 = time.monotonic()
            script = remote_runner_script(
                remote_run_dir,
                unit["model_tag"],
                int(effective_plan["timeout_sec_per_task"]),
                int(args.limit_tasks),
                int(effective_plan.get("preflight_timeout_sec", 60)),
                int(effective_plan.get("preflight_retries", 2)),
                int(effective_plan.get("sleep_between_retries", 0)),
                str(effective_plan.get("preflight_prompt", "Respond with OK only.")),
            )
            code, out, err = run_ssh(unit["ssh"], script)
            duration = round(time.monotonic() - t0, 3)

            if remote_mode == "fallback_tmp":
                # copy back known artifacts (best effort, non-destructive to directory layout)
                for name in (
                    "responses.jsonl",
                    "compute.stderr.ok.log",
                    "compute.stderr.log",
                    "compute.exit.json",
                ):
                    rsync = _rsync_cmd(unit["ssh"], f"{remote_run_dir}/{name}", f"{run_dir}/")
                    run_capture(rsync, timeout=60)

            end = utc_now_rfc3339()
            rec_end = {
                **rec,
                "phase": "end",
                "ended_at": end,
                "duration_sec": duration,
                "exit_code": code,
                "status": "ok" if code == 0 else "failed",
                "remote_mode": remote_mode,
                "stdout_head": out[:2000],
                "stderr_head": err[:2000],
            }
            with lock:
                append_jsonl(dispatch_exec, rec_end)
                run_map_rows.append(
                    {
                        "plan_id": plan["plan_id"],
                        "run_id": rid,
                        "node_id": unit["node_id"],
                        "temp_label": unit["temp_label"],
                        "temperature": unit["temperature"],
                        "epoch": unit["epoch"],
                        "replicate": unit["replicate"],
                        "model_tag": unit["model_tag"],
                        "exit_code": code,
                        "status": rec_end["status"],
                    }
                )
                progress["done"] += 1
                done = progress["done"]
                total = len(units)
                pct = (done / total * 100.0) if total else 100.0
                print(
                    f"[progress] {done}/{total} ({pct:.1f}%) "
                    f"node={unit['node_id']} temp={unit['temp_label']} epoch={unit['epoch']} "
                    f"node_seq={idx}/{len(node_units)} run_id={rid} exit={code}",
                    flush=True,
                )

    max_workers = max(1, len(units_by_node))
    print(f"[progress] start units={len(units)} nodes={len(units_by_node)}", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(worker, nid, n_units) for nid, n_units in units_by_node.items()]
        for f in futures:
            f.result()

    write_csv(
        plan_dir / "run_map.csv",
        run_map_rows,
        [
            "plan_id",
            "run_id",
            "node_id",
            "temp_label",
            "temperature",
            "epoch",
            "replicate",
            "model_tag",
            "exit_code",
            "status",
        ],
    )
    print(f"done: {plan['plan_id']} units={len(units)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
