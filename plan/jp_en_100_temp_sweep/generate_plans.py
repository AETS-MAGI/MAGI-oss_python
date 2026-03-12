#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPS = [("t0p0", 0.0), ("t0p1", 0.1), ("t0p2", 0.2), ("t0p7", 0.7)]
NODES = ["zorya", "eve"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tasks",
        default="<RUST_REPO_DIR>/tasks/generated/jp_en_100_tasks.json",
    )
    ap.add_argument(
        "--out-dir",
        default="<RUST_REPO_DIR>/plans/jp_en_100_temp_sweep",
    )
    ap.add_argument(
        "--dataset-label",
        default="pairs_ja_en_100.json",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=42,
    )
    args = ap.parse_args()

    tasks_path = Path(args.tasks)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit(f"invalid tasks list: {tasks_path}")

    for node in NODES:
        for tag, temp in TEMPS:
            filename = f"jp_en100_{node}_{tag}_e30.json"
            plan_id = f"jp_en100_{node}_{tag}_e30_20260306"
            model_id = f"deepseek-r1-distill-qwen-7b-q4_k_m-{tag}:latest"
            plan = {
                "schema_version": "batch_plan_v1",
                "plan_id": plan_id,
                "model_id": model_id,
                "dataset": args.dataset_label,
                "epochs": 30,
                "replicates": 1,
                "node_pool": [node],
                "node_schedule": {"strategy": "round_robin"},
                "gen_params_mode": "single",
                "gen_params_single": {
                    "temperature": temp,
                    "top_p": 0.95,
                    "top_k": 40,
                    "seed": args.seed,
                    "max_new_tokens": 256,
                    "deterministic_intent": False,
                },
                "backend_request": {"engine": "ollama", "accel": "rocm"},
                "tasks": tasks,
            }
            (out_dir / filename).write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"wrote: {out_dir / filename} plan_id={plan_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
