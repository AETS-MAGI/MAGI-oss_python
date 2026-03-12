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
    ap.add_argument(
        "--run-plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/3-run_plan.py",
    )
    args = ap.parse_args()

    # Current stage runner is idempotent per unit log record style.
    # For now, rerun strategy is "run plan again"; completed runs remain recorded and new attempts are appended.
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    print("rerun strategy: invoke STAGE01/3-run_plan.py with same plan")
    print(f"plan_id={plan['plan_id']}")
    print(f"command: python3 {args.run_plan} --plan {args.plan}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

