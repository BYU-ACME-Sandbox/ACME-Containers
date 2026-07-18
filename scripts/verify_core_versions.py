#!/usr/bin/env python3
"""Verify that installed shared packages match the image's core constraints."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
from pathlib import Path

NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_constraints(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = NAME_RE.match(line)
        if not match:
            raise ValueError(f"Unsupported constraint line in {path}: {raw!r}")
        expected[canonical_name(match.group(1))] = match.group(2)
    if not expected:
        raise ValueError(f"No exact constraints found in {path}")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "constraints",
        nargs="?",
        default="/opt/acme/constraints/core-direct.txt",
        type=Path,
    )
    args = parser.parse_args()

    expected = read_constraints(args.constraints)
    failures: list[str] = []
    for package, expected_version in sorted(expected.items()):
        try:
            actual_version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"{package}: missing (expected {expected_version})")
            continue
        if actual_version != expected_version:
            failures.append(
                f"{package}: installed {actual_version}, expected {expected_version}"
            )

    if failures:
        raise SystemExit("Core version verification failed:\n- " + "\n- ".join(failures))

    print(f"Verified {len(expected)} shared package versions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
