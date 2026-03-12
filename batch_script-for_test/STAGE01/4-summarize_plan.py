#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from _lib import atomic_write_text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan_dir = Path(plan["artifact_root"]) / "plans" / plan["plan_id"]
    run_map = plan_dir / "run_map.csv"
    if not run_map.exists():
        raise SystemExit(f"run_map.csv not found: {run_map}")

    lines = run_map.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    idx = {k: i for i, k in enumerate(header)}

    agg = defaultdict(lambda: {"total": 0, "ok": 0, "failed": 0})
    for row in lines[1:]:
        cols = row.split(",")
        key = (cols[idx["node_id"]], cols[idx["temp_label"]], cols[idx["model_tag"]])
        agg[key]["total"] += 1
        if cols[idx["status"]] == "ok":
            agg[key]["ok"] += 1
        else:
            agg[key]["failed"] += 1

    md = ["# STAGE01 Summary", ""]
    md.append("| node | temp | model | total | ok | failed |")
    md.append("|---|---|---|---:|---:|---:|")
    for (node, temp, model), v in sorted(agg.items()):
        md.append(f"| {node} | {temp} | {model} | {v['total']} | {v['ok']} | {v['failed']} |")
    md.append("")
    md.append(f"- plan_id: `{plan['plan_id']}`")
    md.append(f"- plan_dir: `{plan_dir}`")
    out = plan_dir / "SUMMARY.md"
    atomic_write_text(out, "\n".join(md) + "\n")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

