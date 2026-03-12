#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--analysis-dir",
        default="<TANK_DIR>/artifacts_py/analysis_out",
    )
    ap.add_argument(
        "--note-dir",
        default="<TANK_DIR>/lab_notebook/notes/runs",
    )
    args = ap.parse_args()

    analysis = Path(args.analysis_dir)
    note_dir = Path(args.note_dir)
    note_dir.mkdir(parents=True, exist_ok=True)
    report = analysis / "report.md"
    summary = analysis / "summary.csv"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    body = [
        "# PY Batch Latest Analysis Links",
        "",
        f"- generated_at: `{stamp}`",
        f"- report: `{report}`",
        f"- summary: `{summary}`",
        "",
    ]
    (note_dir / "PY_BATCH_LATEST.md").write_text("\n".join(body), encoding="utf-8")
    print(f"wrote: {note_dir / 'PY_BATCH_LATEST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

