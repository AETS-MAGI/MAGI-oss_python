"""Inference backend abstraction for compute-runner."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class InferenceBackend(Protocol):
    """Minimal interface all inference backends must implement."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Run inference and return the raw output string."""
        ...

    def close(self) -> None:
        """Release model resources."""
        ...


class LlamaCppBackend:
    """llama-cpp-python backed inference."""

    BACKEND_ID = "llama_cpp"

    def __init__(self, model_path: Path, n_ctx: int = 2048, n_gpu_layers: int = -1) -> None:
        try:
            from llama_cpp import Llama  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python is required for inference. "
                "Install with: pip install 'rocm-compute-runner[inference]'"
            ) from exc

        self._llm = Llama(
            model_path=str(model_path),
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

    def generate(self, prompt: str, **kwargs: Any) -> str:
        # Accept both max_tokens (llama-cpp native) and max_new_tokens (HF convention)
        max_tokens: int = kwargs.get("max_tokens") or kwargs.get("max_new_tokens") or 512
        temperature: float = kwargs.get("temperature", 0.0)
        top_p: float = kwargs.get("top_p", 1.0)
        top_k: int = kwargs.get("top_k", 40)
        seed: int | None = kwargs.get("seed")
        output = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
            echo=False,
        )
        return output["choices"][0]["text"]  # type: ignore[index]

    def close(self) -> None:
        # llama-cpp-python cleans up via __del__; explicit close is a no-op
        pass


def resolve_model_path(models_dir: Path, model_id: str, quantization: str | None = None) -> Path:
    """Find the GGUF file for a given model_id, optionally filtering by quantization tag.

    Search order (all searches are case-insensitive via lower() comparison):
      1. Absolute path: if model_id is an absolute path and file exists, use it directly
      2. Exact file: {models_dir}/{model_id}
      3. Quant filter (recursive): **/*{model_id}*{quantization}*.gguf
      4. Any GGUF (recursive):     **/*{model_id}*.gguf

    Supports both flat layout ({models_dir}/{file}.gguf) and
    directory-per-model layout ({models_dir}/{ModelDir}/{file}.gguf).
    """
    candidate = Path(model_id)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    base = models_dir / model_id
    if base.exists() and base.is_file():
        return base

    model_id_lower = model_id.lower()
    quant_lower = quantization.lower() if quantization else None

    all_ggufs = sorted(models_dir.rglob("*.gguf"))

    if quant_lower:
        filtered = [
            p for p in all_ggufs
            if model_id_lower in p.name.lower() and quant_lower in p.name.lower()
        ]
        if filtered:
            return filtered[0]

    filtered = [p for p in all_ggufs if model_id_lower in p.name.lower()]
    if filtered:
        return filtered[0]

    # Also try matching against parent directory name
    if quant_lower:
        filtered = [
            p for p in all_ggufs
            if model_id_lower in p.parent.name.lower() and quant_lower in p.parent.name.lower()
        ]
        if filtered:
            return filtered[0]

    filtered = [p for p in all_ggufs if model_id_lower in p.parent.name.lower()]
    if filtered:
        return filtered[0]

    raise FileNotFoundError(
        f"No GGUF model found for '{model_id}'"
        + (f" (quant={quantization})" if quantization else "")
        + f" in {models_dir}"
    )


def load_backend(model_path: Path, gen_params: dict[str, Any] | None = None) -> InferenceBackend:
    """Instantiate and return the appropriate backend for the given model file."""
    params = gen_params or {}
    return LlamaCppBackend(
        model_path=model_path,
        n_ctx=params.get("n_ctx", 2048),
        n_gpu_layers=params.get("n_gpu_layers", -1),
    )


def run_task(
    backend: InferenceBackend,
    item_id: str,
    prompt: str,
    model_id: str,
    gen_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single task through the backend and return a result record."""
    params = gen_params or {}
    t0 = time.monotonic()
    try:
        raw_output = backend.generate(prompt, **params)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "item_id": item_id,
            "prompt": prompt,
            "raw_output": raw_output,
            "latency_ms": latency_ms,
            "model_id": model_id,
            "backend": getattr(backend, "BACKEND_ID", "unknown"),
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "item_id": item_id,
            "prompt": prompt,
            "raw_output": None,
            "latency_ms": latency_ms,
            "model_id": model_id,
            "backend": getattr(backend, "BACKEND_ID", "unknown"),
            "error": str(exc),
        }
