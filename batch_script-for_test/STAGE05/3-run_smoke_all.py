#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--manifest",
        default="<INSTALL_DIR>/batch_script/STAGE05/plans/manifest.json",
    )
    ap.add_argument("--only", default="t0p1")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--max-runs", type=int, default=0)
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    rc = 0
    for row in manifest:
        plan = row["plan_path"]
        cmd = [
            "python3",
            "<INSTALL_DIR>/batch_script/STAGE01/3-run_plan.py",
            "--plan",
            plan,
            "--only",
            args.only,
            "--epochs",
            str(args.epochs),
        ]
        if args.max_runs > 0:
            cmd += ["--max-runs", str(args.max_runs)]
        print(" ".join(cmd))
        p = subprocess.run(cmd, check=False)
        if p.returncode != 0:
            rc = p.returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

