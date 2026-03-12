#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "template",
        nargs="?",
        default=None,
        help="Optional template plan JSON path (e.g. examples/jp_en_sweep_plan.json)",
    )
    ap.add_argument(
        "--out",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    ap.add_argument(
        "--tasks-file",
        default="<INSTALL_DIR>_rust/tasks/pairs_ja_en_100.json",
    )
    ap.add_argument("--plan-id", default="py_stage01_eve_zorya_temp_sweep_20260306")
    args = ap.parse_args()

    if args.template:
        template_path = Path(args.template)
        plan = json.loads(template_path.read_text(encoding="utf-8"))
    else:
        plan = {
            "schema_version": "py-batch-plan-v1",
            "plan_id": args.plan_id,
            "tasks_file": args.tasks_file,
            "nodes": [
                {"node_id": "zorya", "ssh": "YOUR_USER@YOUR_HOST_ZORYA"},
                {"node_id": "eve", "ssh": "YOUR_USER@YOUR_HOST_EVE"},
            ],
            "epochs": 30,
            "replicates": 1,
            "model_tags": {
                "t0p0": "deepseek-r1-distill-qwen-7b-q4_k_m-t0p0:latest",
                "t0p1": "deepseek-r1-distill-qwen-7b-q4_k_m-t0p1:latest",
                "t0p2": "deepseek-r1-distill-qwen-7b-q4_k_m-t0p2:latest",
                "t0p7": "deepseek-r1-distill-qwen-7b-q4_k_m-t0p7:latest",
            },
            "temps": [
                {"label": "t0p0", "temperature": 0.0},
                {"label": "t0p1", "temperature": 0.1},
                {"label": "t0p2", "temperature": 0.2},
                {"label": "t0p7", "temperature": 0.7},
            ],
            "gen_params": {
                "top_k": 0,
                "top_p": 1,
                "max_new_tokens": 512,
                "seed": 42,
            },
            "artifact_root": "<TANK_DIR>/artifacts_py",
            "timeout_sec_per_task": 120,
            "parallel_tasks_per_node": 1,
        }

    # explicit overrides
    plan["plan_id"] = args.plan_id or plan.get("plan_id")
    plan["tasks_file"] = args.tasks_file or plan.get("tasks_file")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
