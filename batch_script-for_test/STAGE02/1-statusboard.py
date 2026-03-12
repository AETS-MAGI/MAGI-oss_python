#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan_id = plan["plan_id"]
    plan_dir = Path(plan["artifact_root"]) / "plans" / plan_id
    table = plan_dir / "dispatch.table.jsonl"
    execf = plan_dir / "dispatch.exec.jsonl"
    total = 0
    by_key = defaultdict(lambda: {"total": 0, "started": 0, "ok": 0, "failed": 0})

    if table.exists():
        for line in table.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["node_id"], row["temp_label"])
            by_key[key]["total"] += 1
            total += 1

    if execf.exists():
        for line in execf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["node_id"], row["temp_label"])
            status = row.get("status")
            if status == "started":
                by_key[key]["started"] += 1
            elif status == "ok":
                by_key[key]["ok"] += 1
            elif status == "failed":
                by_key[key]["failed"] += 1

    print(f"plan_id={plan_id}")
    print(f"plan_dir={plan_dir}")
    print(f"total_units={total}")
    print("")
    print("node,temp,total,started,ok,failed,pending")
    for key in sorted(by_key):
        v = by_key[key]
        pending = v["total"] - v["ok"] - v["failed"]
        print(f"{key[0]},{key[1]},{v['total']},{v['started']},{v['ok']},{v['failed']},{pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

