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
        ]
        print(" ".join(cmd))
        p = subprocess.run(cmd, check=False)
        if p.returncode != 0:
            rc = p.returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

