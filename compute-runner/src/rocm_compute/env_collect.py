"""Collect compute node environment information (ROCm/GPU/OS)."""
from __future__ import annotations

import datetime as dt
import platform
import socket
import subprocess
from typing import Any


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _rocm_version() -> str:
    # Try rocminfo first, then rocm-smi
    out = _run(["rocminfo"])
    for line in out.splitlines():
        if "ROCm Version:" in line or "HSA Runtime Version:" in line:
            return line.split(":")[-1].strip()
    out = _run(["rocm-smi", "--showversion"])
    for line in out.splitlines():
        if "ROCm" in line or "rocm" in line:
            return line.strip()
    # Fall back to reading from filesystem
    try:
        import pathlib
        version_file = pathlib.Path("/opt/rocm/.info/version")
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception:
        pass
    return "unknown"


def _gpu_info() -> dict[str, str]:
    info: dict[str, str] = {"name": "unknown", "arch": "unknown", "vram": "unknown"}

    # rocm-smi --showproductname
    out = _run(["rocm-smi", "--showproductname"])
    for line in out.splitlines():
        if "Card series:" in line or "GPU" in line:
            name = line.split(":", 1)[-1].strip()
            if name and name != "unknown":
                info["name"] = name
                break

    # rocminfo for arch (gfx...)
    out = _run(["rocminfo"])
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:") and "gfx" in stripped.lower():
            info["arch"] = stripped.split(":", 1)[-1].strip()
        if "ISA Info" in stripped:
            pass  # keep scanning
        if stripped.startswith("Marketing Name:") and info["name"] == "unknown":
            info["name"] = stripped.split(":", 1)[-1].strip()

    # rocm-smi for VRAM
    out = _run(["rocm-smi", "--showmeminfo", "vram"])
    for line in out.splitlines():
        if "Total Memory" in line or "VRAM" in line:
            vram = line.split(":")[-1].strip()
            if vram:
                info["vram"] = vram
                break

    return info


def _torch_info() -> dict[str, str]:
    info: dict[str, str] = {"version": "unknown", "hip": "unknown"}
    try:
        import torch  # type: ignore[import]
        info["version"] = torch.__version__
        if hasattr(torch.version, "hip") and torch.version.hip:
            info["hip"] = torch.version.hip
    except ImportError:
        pass
    return info


def _os_info() -> dict[str, str]:
    info: dict[str, str] = {"name": "unknown", "version": "unknown"}
    try:
        uname = platform.uname()
        info["name"] = uname.system
        # Try /etc/os-release for distro details
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["name"] = line.split("=", 1)[1].strip().strip('"')
                    if line.startswith("VERSION_ID="):
                        info["version"] = line.split("=", 1)[1].strip().strip('"')
        except Exception:
            info["version"] = uname.release
    except Exception:
        pass
    return info


def collect_env() -> dict[str, Any]:
    """Collect environment info; individual failures are skipped gracefully."""
    created_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    env: dict[str, Any] = {
        "created_at": created_at,
        "hostname": socket.gethostname(),
        "os": {},
        "gpu": {},
        "driver": {},
        "backend": "rocm",
        "torch": {},
    }

    try:
        env["os"] = _os_info()
    except Exception:
        env["os"] = {"name": "unknown", "version": "unknown"}

    try:
        env["gpu"] = _gpu_info()
    except Exception:
        env["gpu"] = {"name": "unknown", "arch": "unknown", "vram": "unknown"}

    try:
        env["driver"] = {"rocm": _rocm_version()}
    except Exception:
        env["driver"] = {"rocm": "unknown"}

    try:
        env["torch"] = _torch_info()
    except Exception:
        env["torch"] = {"version": "unknown", "hip": "unknown"}

    return env
