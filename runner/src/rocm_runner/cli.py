from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from rocm_runner import __version__


REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "db" / "runs.db"
ARTIFACT_BASE_ROOT = REPO_ROOT.parent / "tank" / "artifacts"
ARTIFACT_RUNS_ROOT = ARTIFACT_BASE_ROOT / "runs"


def canonical_run_dir(run_id: str) -> Path:
    return ARTIFACT_RUNS_ROOT / run_id


def legacy_run_dir(run_id: str) -> Path:
    return ARTIFACT_BASE_ROOT / run_id


def resolve_run_dir(run_id: str) -> Path:
    canonical = canonical_run_dir(run_id)
    if canonical.exists():
        return canonical
    legacy = legacy_run_dir(run_id)
    if legacy.exists():
        return legacy
    return canonical


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_runner_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        commit = out.strip()
        if commit:
            return commit
    except Exception:
        pass
    return os.environ.get("RUNNER_COMMIT", "unknown")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_spec_from_artifact(spec_path: Path) -> tuple[dict[str, Any], str, str]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("spec.json must be a JSON object")
    spec_canonical = canonical_json(spec)
    spec_sha256 = hashlib.sha256(spec_canonical.encode("utf-8")).hexdigest()
    spec_hash = spec_sha256[:8]
    return spec, spec_hash, spec_sha256


def mark_run_completed(run_id: str) -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE runs SET status = 'completed', completed_at = ? WHERE run_id = ?",
            (utc_now_iso(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def normalize_response(row: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **row,
        "item_id": row.get("item_id") or row.get("task_id"),
        "prompt": row.get("prompt", ""),
        "raw_output": row.get("raw_output") if row.get("raw_output") is not None else row.get("output", ""),
    }
    return merged


def load_responses_with_errors(file_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    responses: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as error:
                errors.append(
                    {
                        "line": idx,
                        "error": str(error),
                        "raw": line[:100],
                    }
                )
                continue
            if not isinstance(item, dict):
                errors.append(
                    {
                        "line": idx,
                        "error": "line is not a JSON object",
                        "raw": line[:100],
                    }
                )
                continue
            responses.append(normalize_response(item))
    return responses, errors


def evaluate_constraints(parsed_output: Any, constraints: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if constraints.get("json_valid") is True and parsed_output is None:
        errors.append("json_valid expected true")
        return False, errors

    required_keys = constraints.get("required_keys")
    if isinstance(required_keys, list):
        if not isinstance(parsed_output, dict):
            errors.append("required_keys expects object output")
        else:
            missing = [key for key in required_keys if key not in parsed_output]
            if missing:
                errors.append(f"missing required keys: {missing}")

    if constraints.get("extra_keys_allowed") is False and isinstance(required_keys, list):
        if isinstance(parsed_output, dict):
            extras = [key for key in parsed_output.keys() if key not in required_keys]
            if extras:
                errors.append(f"extra keys not allowed: {extras}")

    array_length = constraints.get("array_length")
    if isinstance(array_length, int):
        if not isinstance(parsed_output, list):
            errors.append("array_length expects array output")
        elif len(parsed_output) != array_length:
            errors.append(f"array length expected {array_length}, got {len(parsed_output)}")

    return len(errors) == 0, errors


def classify_output_contract(output: str | None, parsed_output: Any, parse_error: str | None, constraint_errors: list[str]) -> str:
    if parsed_output is not None:
        has_schema_error = any(
            err.startswith("required_keys") or 
            err.startswith("missing required keys") or 
            err.startswith("extra keys")
            for err in constraint_errors
        )
        if has_schema_error:
            return "json_schema_mismatch"
        else:
            return "json_valid"
    else:
        if not output:
            return "non_json_plaintext"
        output_stripped = output.strip()
        if output_stripped.startswith("{") and output_stripped.endswith("}"):
            return "json_corrupted"
        elif "{" in output_stripped and "}" in output_stripped:
            return "json_prefix_suffix"
        else:
            return "non_json_plaintext"


def check_deterministic_effective(gen_params: dict[str, Any] | None) -> bool:
    if not isinstance(gen_params, dict):
        return False
    temperature = gen_params.get("temperature")
    top_p = gen_params.get("top_p")
    top_k = gen_params.get("top_k")
    seed = gen_params.get("seed")

    if temperature != 0.0:
        return False
    if top_p is not None and top_p != 1.0:
        return False
    if top_k is not None and top_k != 0:
        return False
    if seed is None:
        return False
    return True


def score_responses(
    spec: dict[str, Any], responses: list[dict[str, Any]], parse_errors: list[dict[str, Any]]
) -> dict[str, Any]:
    task_map: dict[str, dict[str, Any]] = {}
    for task in spec.get("tasks", []):
        if isinstance(task, dict) and isinstance(task.get("task_id"), str):
            task_map[task["task_id"]] = task

    json_parse_success = 0
    output_parse_failures = 0
    constraint_pass = 0
    evaluated = 0
    response_failed = 0
    details: list[dict[str, Any]] = []
    output_parse_error_entries: list[dict[str, Any]] = []
    output_contract_summary = {
        "json_valid": 0,
        "non_json_plaintext": 0,
        "json_corrupted": 0,
        "json_prefix_suffix": 0,
        "json_schema_mismatch": 0,
    }

    for item in responses:
        item_id = item.get("item_id") or item.get("task_id")
        output = item.get("raw_output") if item.get("raw_output") is not None else item.get("output")
        parsed_output: Any = None
        parse_error = None

        if isinstance(output, str):
            try:
                parsed_output = json.loads(output)
                json_parse_success += 1
            except json.JSONDecodeError as error:
                parse_error = f"output_json_parse_error: {error}"
                output_parse_failures += 1
                output_parse_error_entries.append(
                    {
                        "item_id": item_id,
                        "error": parse_error,
                    }
                )

        task_constraints = {}
        if isinstance(item_id, str) and item_id in task_map:
            raw_constraints = task_map[item_id].get("constraints")
            if isinstance(raw_constraints, dict):
                task_constraints = raw_constraints

        row_failed = parse_error is not None
        passed = False
        errors: list[str] = []
        if task_constraints:
            evaluated += 1
            passed, errors = evaluate_constraints(parsed_output, task_constraints)
            if passed:
                constraint_pass += 1
            else:
                row_failed = True
        
        contract = classify_output_contract(
            output=output if isinstance(output, str) else None,
            parsed_output=parsed_output,
            parse_error=parse_error,
            constraint_errors=errors
        )
        if contract in output_contract_summary:
            output_contract_summary[contract] += 1
        
        details.append(
            {
                "item_id": item_id,
                "constraint_pass": passed,
                "errors": errors,
                "parse_error": parse_error,
                "output_contract": contract,
            }
        )

        if row_failed:
            response_failed += 1

    total_valid = len(responses)
    total = total_valid + len(parse_errors)
    passed = constraint_pass
    failed = response_failed + len(parse_errors)

    merged_errors = [*parse_errors]
    merged_errors.extend(output_parse_error_entries)
    for detail in details:
        detail_errors = detail.get("errors", [])
        if detail_errors:
            merged_errors.append(
                {
                    "item_id": detail.get("item_id"),
                    "error": "; ".join(str(err) for err in detail_errors),
                }
            )
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": merged_errors,
        "total_responses": total_valid,
        "json_parse_success": json_parse_success,
        "output_parse_failures": output_parse_failures,
        "json_parse_rate": (json_parse_success / total_valid) if total_valid else 0.0,
        "constraints_evaluated": evaluated,
        "constraints_passed": constraint_pass,
        "constraint_pass_rate": (constraint_pass / evaluated) if evaluated else 0.0,
        "output_contract_summary": output_contract_summary,
        "details": details,
    }


def write_run_log(run_dir: Path, run_id: str, metrics: dict[str, Any]) -> None:
    log_lines = [
        f"timestamp={utc_now_iso()}",
        f"run_id={run_id}",
        "command=runner integrate",
        f"total_responses={metrics['total_responses']}",
        f"json_parse_success={metrics['json_parse_success']}",
        f"constraints_passed={metrics['constraints_passed']}",
        f"failed={metrics['failed']}",
        f"errors={len(metrics['errors'])}",
    ]
    (run_dir / "run.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(".json.new")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def extract_spec_hash_from_run_id(run_id: str) -> str | None:
    match = re.fullmatch(r"\d{8}-\d{6}-([0-9a-fA-F]{8})", run_id)
    if not match:
        return None
    return match.group(1).lower()


def integrate(run_id: str) -> int:
    run_dir = resolve_run_dir(run_id)
    spec_path = run_dir / "spec.json"
    responses_path = run_dir / "responses.jsonl"
    env_path = run_dir / "env.json"

    required_files = [spec_path, env_path, responses_path]
    for required in required_files:
        if not required.exists():
            raise FileNotFoundError(f"Required file missing: {required}")

    spec, spec_hash, spec_sha256 = load_spec_from_artifact(spec_path)
    run_id_hash = extract_spec_hash_from_run_id(run_id)
    if run_id_hash and run_id_hash != spec_hash:
        raise ValueError(f"spec_hash mismatch: run_id has {run_id_hash} but spec.json has {spec_hash}")

    responses, parse_errors = load_responses_with_errors(responses_path)

    env = json.loads(env_path.read_text(encoding="utf-8"))
    if not isinstance(env, dict):
        raise ValueError("env.json must be a JSON object")

    schema_version = spec.get("schema_version", "1.0")
    gen_params = spec.get("gen_params")

    # gen_params の有無で判定する（schema_version が無い新形式 spec にも対応）
    gen_params_missing = not isinstance(gen_params, dict)
    if gen_params_missing:
        deterministic_intent = False
        deterministic_effective = False
    else:
        deterministic_intent = gen_params.get("deterministic_intent", False)
        deterministic_effective = check_deterministic_effective(gen_params)
        
    gpu_info = env.get("gpu", {})
    gpu_arch = (gpu_info.get("arch") if isinstance(gpu_info, dict) else env.get("gpu_arch")) or "unknown"

    for resp in responses:
        if not gen_params_missing:
            resp["gen_params"] = gen_params
        resp["deterministic_effective"] = deterministic_effective
        resp["gpu_arch"] = gpu_arch

    metrics = score_responses(spec, responses, parse_errors)
    created_at = utc_now_iso()

    result = {
        "schema_version": schema_version,
        "run_id": run_id,
        "created_at": created_at,
        "spec": spec,
        "spec_hash": spec_hash,
        "env": env,
        "gen_params_missing": gen_params_missing,
        "deterministic_intent": deterministic_intent,
        "deterministic_effective": deterministic_effective,
        "control_plane": {
            "hostname": socket.gethostname(),
            "runner_version": __version__,
            "runner_commit": get_runner_commit(),
        },
        "responses": responses,
        "metrics": metrics,
        "integrity": {
            "spec_sha256": spec_sha256,
            "responses_sha256": sha256_file(responses_path),
            "env_sha256": sha256_file(env_path),
        },
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(run_dir / "result.json", result)
    write_run_log(run_dir, run_id, metrics)
    mark_run_completed(run_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    integrate_parser = subparsers.add_parser("integrate", help="Integrate artifacts and scoring into result.json")
    integrate_parser.add_argument("run_id")

    args = parser.parse_args(argv)
    try:
        if args.command == "integrate":
            return integrate(args.run_id)
        parser.error("unknown command")
        return 2
    except Exception as error:
        print(f"runner: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())