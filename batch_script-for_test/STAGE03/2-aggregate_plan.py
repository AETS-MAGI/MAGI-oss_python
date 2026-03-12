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
    artifact_root = Path(plan["artifact_root"])
    plan_dir = artifact_root / "plans" / plan["plan_id"]
    run_map = plan_dir / "run_map.csv"
    if not run_map.exists():
        raise SystemExit(f"run_map.csv not found: {run_map}")

    lines = run_map.read_text(encoding="utf-8").splitlines()
    head = lines[0].split(",")
    idx = {k: i for i, k in enumerate(head)}
    by_key = defaultdict(lambda: {"total": 0, "ok": 0, "failed": 0, "responses": 0})

    for ln in lines[1:]:
        if not ln.strip():
            continue
        cols = ln.split(",")
        rid = cols[idx["run_id"]]
        key = (cols[idx["node_id"]], cols[idx["temp_label"]])
        by_key[key]["total"] += 1
        status = cols[idx["status"]]
        by_key[key][status] = by_key[key].get(status, 0) + 1
        resp = artifact_root / "runs" / rid / "responses.jsonl"
        if resp.exists():
            by_key[key]["responses"] += len([x for x in resp.read_text(encoding="utf-8").splitlines() if x.strip()])

    out = {
        "plan_id": plan["plan_id"],
        "summary": [
            {
                "node_id": n,
                "temp_label": t,
                **vals,
            }
            for (n, t), vals in sorted(by_key.items())
        ],
    }
    out_path = plan_dir / "aggregate.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

