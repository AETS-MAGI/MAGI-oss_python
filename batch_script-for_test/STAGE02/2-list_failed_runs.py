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
        print("dispatch.exec.jsonl not found")
        return 1

    rows = []
    for line in execf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "failed":
            rows.append(row)

    if not rows:
        print("no failed runs")
        return 0

    print("run_id,node,temp,epoch,replicate,exit_code")
    for r in rows:
        print(
            f"{r.get('run_id')},{r.get('node_id')},{r.get('temp_label')},"
            f"{r.get('epoch')},{r.get('replicate')},{r.get('exit_code')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

