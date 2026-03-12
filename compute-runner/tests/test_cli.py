"""Tests for compute-runner CLI subcommands."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# test_env_subcommand
# ---------------------------------------------------------------------------

def test_env_subcommand(tmp_path: Path) -> None:
    """env --out writes valid env.json with expected top-level keys."""
    mock_env = {
        "created_at": "2026-03-01T00:00:00Z",
        "hostname": "zorya",
        "os": {"name": "Ubuntu", "version": "22.04"},
        "gpu": {"name": "RX 9070 XT", "arch": "gfx1201", "vram": "16 GB"},
        "driver": {"rocm": "6.3.0"},
        "backend": "rocm",
        "torch": {"version": "2.5.0", "hip": "6.3.0"},
    }

    out_path = tmp_path / "env.json"
    with patch("rocm_compute.env_collect.collect_env", return_value=mock_env):
        from rocm_compute.cli import main
        rc = main(["env", "--out", str(out_path)])

    assert rc == 0
    assert out_path.exists(), "env.json was not created"
    data = json.loads(out_path.read_text())
    for key in ("created_at", "hostname", "os", "gpu", "driver", "backend", "torch"):
        assert key in data, f"Missing key: {key}"
    assert data["hostname"] == "zorya"
    assert data["gpu"]["arch"] == "gfx1201"


# ---------------------------------------------------------------------------
# test_run_subcommand
# ---------------------------------------------------------------------------

def test_run_subcommand(tmp_path: Path) -> None:
    """run --spec generates responses.jsonl with required fields per item."""
    # Create spec.json
    spec = {
        "model_id": "deepseek-r1-distill-qwen-7b",
        "dataset": "geo_qa",
        "quantization": "Q4_K_M",
        "gen_params": {"max_tokens": 64, "temperature": 0.0},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    # Create dataset
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    dataset_path = datasets_dir / "geo_qa.jsonl"
    items = [
        {"item_id": "JP01", "prompt": "日本の首都は？"},
        {"item_id": "JP02", "prompt": "日本の人口は？"},
    ]
    dataset_path.write_text(
        "\n".join(json.dumps(it) for it in items) + "\n", encoding="utf-8"
    )

    # Create a fake model file so resolve_model_path finds it
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    fake_model = models_dir / "deepseek-r1-distill-qwen-7b-Q4_K_M.gguf"
    fake_model.write_bytes(b"")

    outdir = tmp_path / "run_out"

    # Mock the Llama class
    mock_llama_instance = MagicMock()
    mock_llama_instance.return_value = {
        "choices": [{"text": '{"answer": "東京"}'}]
    }

    with patch("rocm_compute.inference.LlamaCppBackend.__init__", return_value=None), \
         patch.object(
             __import__("rocm_compute.inference", fromlist=["LlamaCppBackend"]).LlamaCppBackend,
             "generate",
             return_value='{"answer": "東京"}',
         ), \
         patch.object(
             __import__("rocm_compute.inference", fromlist=["LlamaCppBackend"]).LlamaCppBackend,
             "close",
             return_value=None,
         ):
        from rocm_compute.cli import main
        rc = main([
            "run",
            "--run-id", "test-001",
            "--spec", str(spec_path),
            "--outdir", str(outdir),
            "--models-dir", str(models_dir),
            "--datasets-dir", str(datasets_dir),
        ])

    assert rc == 0
    responses_path = outdir / "responses.jsonl"
    assert responses_path.exists(), "responses.jsonl was not created"

    lines = [l for l in responses_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"

    for line in lines:
        rec = json.loads(line)
        for field in ("item_id", "prompt", "raw_output", "latency_ms"):
            assert field in rec, f"Missing field '{field}' in: {rec}"
        assert rec["raw_output"] is not None


# ---------------------------------------------------------------------------
# test_run_all_fail_returns_2
# ---------------------------------------------------------------------------

def test_run_all_fail_returns_2(tmp_path: Path) -> None:
    """run returns exit code 2 when all tasks fail."""
    spec = {
        "model_id": "some-model",
        "dataset": "tiny",
        "gen_params": {},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))

    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    (datasets_dir / "tiny.jsonl").write_text(
        json.dumps({"item_id": "T1", "prompt": "test"}) + "\n"
    )

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "some-model-Q4_K_M.gguf").write_bytes(b"")

    outdir = tmp_path / "out"

    with patch("rocm_compute.inference.LlamaCppBackend.__init__", return_value=None), \
         patch.object(
             __import__("rocm_compute.inference", fromlist=["LlamaCppBackend"]).LlamaCppBackend,
             "generate",
             side_effect=RuntimeError("GPU OOM"),
         ), \
         patch.object(
             __import__("rocm_compute.inference", fromlist=["LlamaCppBackend"]).LlamaCppBackend,
             "close",
             return_value=None,
         ):
        from rocm_compute.cli import main
        rc = main([
            "run", "--run-id", "fail-001",
            "--spec", str(spec_path),
            "--outdir", str(outdir),
            "--models-dir", str(models_dir),
            "--datasets-dir", str(datasets_dir),
        ])

    assert rc == 2
    # Evidence preserved even on failure
    lines = [l for l in (outdir / "responses.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["raw_output"] is None
    assert "error" in rec
