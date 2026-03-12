#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_hash8(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:8]


def make_run_id(hash8: str) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + hash8


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def run_capture(cmd: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, p.stdout, p.stderr


def run_ssh(host: str, script: str) -> tuple[int, str, str]:
    # Support multi-word ssh strings like "root@host -p 13771 -i /path/to/key"
    parts = shlex.split(host)
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no"] + parts + [f"bash -lc {shlex.quote(script)}"]
    return run_capture(cmd)


def load_tasks(tasks_file: Path) -> list[dict[str, Any]]:
    raw = json.loads(tasks_file.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
        raw = raw["tasks"]
    if not isinstance(raw, list):
        raise ValueError(f"tasks file must be a list or object with tasks[]: {tasks_file}")
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        pair_id = row.get("pair_id")
        lang = row.get("lang")
        prompt = row.get("prompt")
        if isinstance(pair_id, str) and isinstance(lang, str) and isinstance(prompt, str):
            item_id = row.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                item_id = f"{pair_id}:{lang}"
            out.append(
                {
                    "item_id": item_id,
                    "prompt": prompt,
                    "meta": {
                        "pair_id": pair_id,
                        "lang": lang,
                        "gold_canonical_answer": row.get("gold_canonical_answer"),
                        "domain": row.get("domain"),
                        "subtype": row.get("subtype"),
                    },
                }
            )
            continue
        # already in runner-friendly form
        if isinstance(row.get("item_id"), str) and isinstance(row.get("prompt"), str):
            out.append(row)
    if not out:
        raise ValueError(f"no valid tasks parsed from {tasks_file}")
    return out
