#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base-plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    ap.add_argument(
        "--task-index",
        default="<INSTALL_DIR>_rust/tasks/task_sets_index.json",
    )
    ap.add_argument(
        "--out-dir",
        default="<INSTALL_DIR>/batch_script/STAGE05/plans",
    )
    ap.add_argument("--suffix", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()

    base = json.loads(Path(args.base_plan).read_text(encoding="utf-8"))
    index = json.loads(Path(args.task_index).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    task_sets = index.get("task_sets", [])
    if not isinstance(task_sets, list) or not task_sets:
        raise SystemExit("task_sets_index has no task_sets[]")

    manifest = []
    for entry in task_sets:
        if not isinstance(entry, dict):
            continue
        set_id = entry.get("set_id")
        rel_path = entry.get("path")
        if not isinstance(set_id, str) or not isinstance(rel_path, str):
            continue
        plan = dict(base)
        plan["plan_id"] = f"py_stage05_{set_id}_{args.suffix}"
        plan["tasks_file"] = f"<INSTALL_DIR>_rust/{rel_path}"
        plan["dataset_label"] = set_id
        out_path = out_dir / f"{set_id}.plan.json"
        out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.append(
            {
                "set_id": set_id,
                "tasks_file": plan["tasks_file"],
                "plan_id": plan["plan_id"],
                "plan_path": str(out_path),
            }
        )
        print(f"wrote: {out_path} plan_id={plan['plan_id']}")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote: {out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

