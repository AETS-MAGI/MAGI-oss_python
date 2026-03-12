#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out-dir",
        default="<TANK_DIR>/artifacts_py/analysis_out",
    )
    ap.add_argument(
        "--tank-root",
        default="<TANK_DIR>",
    )
    ap.add_argument(
        "--gold",
        default="<INSTALL_DIR>_rust/analysis/gold_answers.json",
    )
    args = ap.parse_args()

    cmd = [
        "python3",
        "<INSTALL_DIR>_rust/analysis/analyze_pairs.py",
        "--tank-root",
        args.tank_root,
        "--artifact-root",
        "artifacts_py",
        "--gold",
        args.gold,
        "--out-dir",
        args.out_dir,
    ]
    print(" ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())

