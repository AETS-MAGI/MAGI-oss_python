#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _lib import load_tasks, run_capture


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plan",
        default="<INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json",
    )
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    tasks = load_tasks(Path(plan["tasks_file"]))
    print(f"[ok] tasks loaded: {len(tasks)} from {plan['tasks_file']}")

    model_tags = list(plan["model_tags"].values())
    failed = False
    for node in plan["nodes"]:
        host = node["ssh"]
        for tag in model_tags:
            cmd = [
                "ssh",
                host,
                "bash",
                "-lc",
                f"unset MAGI_OLLAMA_STUB; ollama show {tag} >/dev/null 2>&1",
            ]
            code, _, _ = run_capture(cmd, timeout=30)
            if code != 0:
                failed = True
                print(f"[ng] model missing on {host}: {tag}")
            else:
                print(f"[ok] model on {host}: {tag}")

    if failed:
        print("preflight failed")
        return 1
    print("preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

