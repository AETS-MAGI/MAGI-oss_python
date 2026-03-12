#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan_dir = Path(plan["artifact_root"]) / "plans" / plan["plan_id"]
    execf = plan_dir / "dispatch.exec.jsonl"
    if not execf.exists():
        raise SystemExit(f"dispatch.exec.jsonl not found: {execf}")

    last_by_run = {}
    for line in execf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rid = row.get("run_id")
        if isinstance(rid, str):
            last_by_run[rid] = row

    for rid, row in sorted(last_by_run.items()):
        run_dir = Path(plan["artifact_root"]) / "runs" / rid
        responses = run_dir / "responses.jsonl"
        exitj = run_dir / "compute.exit.json"
        if exitj.exists():
            status = "failed_with_evidence"
        elif responses.exists():
            status = "ok"
        else:
            status = "incomplete"
        count = 0
        if responses.exists():
            count = len([ln for ln in responses.read_text(encoding="utf-8").splitlines() if ln.strip()])
        result = {
            "schema_version": "magi-result-v0",
            "run_id": rid,
            "created_at": row.get("ended_at") or row.get("started_at"),
            "status": status,
            "summary": {
                "node_id": row.get("node_id"),
                "temperature": row.get("temperature"),
                "temp_label": row.get("temp_label"),
                "model_tag": row.get("model_tag"),
                "responses_count": count,
                "has_compute_exit_json": exitj.exists(),
            },
        }
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"integrated runs: {len(last_by_run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

