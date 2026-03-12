"""compute-runner CLI: env and run subcommands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _default_models_dir() -> Path:
    candidates = [
        Path.home() / "ROCm-project" / "models_local",   # ROCm local storage
        Path.home() / "ROCm-project" / "tank" / "models", # shared mount
        Path.home() / "compute-work" / "models",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


def _default_datasets_dir() -> Path:
    candidates = [
        Path.home() / "ROCm-project" / "datasets",        # ROCm local storage
        Path.home() / "ROCm-project" / "tank" / "datasets", # shared mount
        Path.home() / "compute-work" / "datasets",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------

def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.new")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record to a .jsonl file (逐次append)."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# env subcommand
# ---------------------------------------------------------------------------

def cmd_env(args: argparse.Namespace) -> int:
    from rocm_compute.env_collect import collect_env

    out_path = Path(args.out)
    env = collect_env()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(out_path, env)
    print(f"env written: {out_path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------

def _load_dataset(datasets_dir: Path, dataset: str) -> list[dict[str, Any]]:
    path = datasets_dir / f"{dataset}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                items.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {lineno} of {path}: {exc}") from exc
    return items


def cmd_run(args: argparse.Namespace) -> int:
    from rocm_compute.inference import load_backend, resolve_model_path, run_task

    spec_path = Path(args.spec)
    outdir = Path(args.outdir)
    models_dir = Path(args.models_dir) if args.models_dir else _default_models_dir()
    datasets_dir = Path(args.datasets_dir) if args.datasets_dir else _default_datasets_dir()

    spec_raw = json.loads(spec_path.read_text(encoding="utf-8"))
    spec: dict[str, Any] = spec_raw.get("spec", spec_raw)

    model_id: str = spec["model_id"]
    dataset_name: str = spec["dataset"]
    gen_params: dict[str, Any] = spec.get("gen_params") or {}
    quantization: str | None = spec.get("quantization") or gen_params.get("quantization")

    try:
        model_path = resolve_model_path(models_dir, model_id, quantization)
    except FileNotFoundError as exc:
        print(f"compute-runner: error: {exc}", file=sys.stderr)
        return 1

    tasks = _load_dataset(datasets_dir, dataset_name)
    if not tasks:
        print(f"compute-runner: error: dataset '{dataset_name}' is empty", file=sys.stderr)
        return 1

    outdir.mkdir(parents=True, exist_ok=True)
    responses_path = outdir / "responses.jsonl"
    # Truncate/create fresh for this run
    responses_path.write_text("", encoding="utf-8")

    backend = load_backend(model_path, gen_params)
    failed = 0
    total = len(tasks)

    try:
        for task in tasks:
            item_id = task.get("item_id") or task.get("task_id", "")
            prompt = task.get("prompt", "")
            result = run_task(
                backend=backend,
                item_id=item_id,
                prompt=prompt,
                model_id=model_id,
                gen_params=gen_params,
            )
            _append_jsonl(responses_path, result)
            if result.get("raw_output") is None:
                failed += 1
                print(
                    f"compute-runner: warn: item_id={item_id} failed: {result.get('error', 'unknown')}",
                    file=sys.stderr,
                )
    finally:
        backend.close()

    print(
        f"compute-runner: run complete: {total - failed}/{total} succeeded -> {responses_path}",
        file=sys.stderr,
    )

    if failed == total:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compute-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # env subcommand
    env_parser = subparsers.add_parser("env", help="Collect and write environment info to JSON")
    env_parser.add_argument("--out", required=True, metavar="PATH", help="Output path for env.json")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run inference tasks from a spec")
    run_parser.add_argument("--run-id", required=True, metavar="ID")
    run_parser.add_argument("--spec", required=True, metavar="PATH", help="Path to spec.json")
    run_parser.add_argument("--outdir", required=True, metavar="PATH", help="Output directory")
    run_parser.add_argument("--models-dir", default=None, metavar="DIR",
                            help="Models directory (default: auto-detect tank or ~/compute-work/models)")
    run_parser.add_argument("--datasets-dir", default=None, metavar="DIR",
                            help="Datasets directory (default: auto-detect tank or ~/compute-work/datasets)")

    args = parser.parse_args(argv)
    try:
        if args.command == "env":
            return cmd_env(args)
        if args.command == "run":
            return cmd_run(args)
        parser.error("unknown command")
        return 2
    except Exception as exc:
        print(f"compute-runner: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
