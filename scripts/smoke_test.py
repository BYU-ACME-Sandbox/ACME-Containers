#!/usr/bin/env python3
"""Fast runtime checks for a built ACME image."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_requirements import smoke_requirement_files

INFO_PATH = Path("/opt/acme/image-info.json")
CONFIG_PATH = Path("/opt/acme/config/images.json")
REQUIREMENTS_ROOT = Path("/opt/acme/requirements")


def run(*command: str) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def expected_machine(lock_arch: str) -> set[str]:
    return {
        "amd64": {"x86_64", "amd64"},
        "arm64": {"aarch64", "arm64"},
    }[lock_arch]


def requirement_sources(target: str) -> list[Path]:
    sources = [REQUIREMENTS_ROOT / "core.in"]
    if target == "dev":
        sources.extend(
            [
                REQUIREMENTS_ROOT / "dev/labs.in",
                REQUIREMENTS_ROOT / "dev/sphinx.in",
                REQUIREMENTS_ROOT / "dev/tools.in",
            ]
        )
    elif target != "core":
        sources.append(REQUIREMENTS_ROOT / "course.in")
    return sources


def core_checks() -> None:
    import cvxopt
    import matplotlib
    import numpy as np
    import pandas as pd
    import scipy.linalg

    matrix = np.array([[3.0, 1.0], [1.0, 2.0]])
    rhs = np.array([9.0, 8.0])
    solution = scipy.linalg.solve(matrix, rhs)
    if not np.allclose(solution, [2.0, 3.0]):
        raise RuntimeError(f"Unexpected linear solve result: {solution}")

    frame = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    if int(frame.sum().sum()) != 10:
        raise RuntimeError("Unexpected pandas result")

    cvx_matrix = cvxopt.matrix([1.0, 2.0, 3.0])
    if cvx_matrix.size != (3, 1):
        raise RuntimeError("Unexpected CVXOPT matrix shape")

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "smoke.png"
        plt.figure()
        plt.plot([0, 1], [0, 1])
        plt.savefig(output)
        plt.close("all")
        if output.stat().st_size == 0:
            raise RuntimeError("Matplotlib produced an empty image")

    run(sys.executable, "/opt/acme/scripts/verify_core_versions.py")
    run(sys.executable, "-m", "jupyter", "--version")
    run(sys.executable, "-m", "flake8", "--version")
    run(sys.executable, "-m", "nbqa", "--version")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", help="Expected image target")
    args = parser.parse_args()

    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    target = args.target or info["target"]
    if target != info["target"]:
        raise RuntimeError(f"Image says {info['target']}, test requested {target}")

    expected_python = config["python_version"]
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise RuntimeError(f"Python {actual_python}; expected {expected_python}")

    machine = platform.machine().lower()
    lock_arch = info["lock_arch"]
    if machine not in expected_machine(lock_arch):
        raise RuntimeError(f"Machine {machine!r} does not match lock architecture {lock_arch}")

    if Path(sys.executable).resolve() != Path("/opt/acme-venv/bin/python").resolve():
        raise RuntimeError(f"Unexpected interpreter: {sys.executable}")
    if os.environ.get("VIRTUAL_ENV") != "/opt/acme-venv":
        raise RuntimeError("VIRTUAL_ENV is not /opt/acme-venv")

    core_checks()
    smoke_requirement_files(requirement_sources(target))

    if target == "dev":
        for command in ("latexmk", "node", "svgo"):
            run(command, "--version")

    print(
        f"Smoke test passed: target={target}, python={actual_python}, "
        f"machine={machine}, lock={lock_arch}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
