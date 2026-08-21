#!/usr/bin/env python3
"""Generate and verify platform-specific manual ACME Python locks.

This tooling is intentionally separate from the Docker release system. Linux
manual locks are exact copies of the published Docker locks. Apple Silicon
macOS locks are resolved from the same requirement inputs while pinning all
direct packages to the versions in the matching Linux Docker lock. Intel macOS
is intentionally unsupported.
"""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# scripts/acme.py lives beside this file, so importing it works both from the
# repository root and when this file is executed directly.
from acme import (  # type: ignore
    ALL_ORDER,
    CORE_INPUT,
    ROOT,
    AcmeError,
    canonical_name,
    direct_requirement_names,
    expand_targets,
    image_lock_path,
    load_config,
    one_locked_version,
    parse_lock_versions,
)

MANUAL_ROOT = ROOT / "generated" / "manual-environments"

PLATFORMS = {
    "linux-amd64": {
        "python_platform": "x86_64-unknown-linux-gnu",
        "reference_arch": "amd64",
        "system": "Linux",
        "machine": {"x86_64", "amd64"},
        "copy_docker_lock": True,
    },
    "linux-arm64": {
        "python_platform": "aarch64-unknown-linux-gnu",
        "reference_arch": "arm64",
        "system": "Linux",
        "machine": {"aarch64", "arm64"},
        "copy_docker_lock": True,
    },
    "macos-arm64": {
        "python_platform": "aarch64-apple-darwin",
        "reference_arch": "arm64",
        "system": "Darwin",
        "machine": {"arm64", "aarch64"},
        "copy_docker_lock": False,
    },
}



def manual_lock_path(target: str, platform_name: str) -> Path:
    return MANUAL_ROOT / target / "locks" / f"{platform_name}.txt"


def target_sources(config: dict, target: str) -> list[Path]:
    if target == "core":
        return [CORE_INPUT]
    if target == "dev":
        return [
            CORE_INPUT,
            ROOT / "requirements/dev/labs.in",
            ROOT / "requirements/dev/sphinx.in",
            ROOT / "requirements/dev/tools.in",
        ]
    return [CORE_INPUT, ROOT / config["images"][target]["requirements"]]


def direct_names_for_target(config: dict, target: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for source in target_sources(config, target):
        for name in direct_requirement_names(source):
            canonical = canonical_name(name)
            if canonical not in seen:
                names.append(canonical)
                seen.add(canonical)
    return names


def docker_reference_lock(target: str, platform_name: str) -> Path:
    arch = str(PLATFORMS[platform_name]["reference_arch"])
    return image_lock_path(target, arch)


def ensure_docker_reference(target: str, platform_name: str) -> Path:
    reference = docker_reference_lock(target, platform_name)
    if not reference.exists():
        raise AcmeError(
            f"Missing Docker reference lock {reference.relative_to(ROOT)}. "
            "Prepare the Docker locks first."
        )
    return reference


def write_direct_constraints(config: dict, target: str, platform_name: str, path: Path) -> None:
    """Pin every direct package to the matching Linux Docker-lock version."""
    reference = ensure_docker_reference(target, platform_name)
    lines = [
        f"# Direct package versions from {reference.relative_to(ROOT)}.",
        "# Used only while resolving the corresponding Apple Silicon macOS lock.",
        "",
    ]
    for name in direct_names_for_target(config, target):
        lines.append(f"{name}=={one_locked_version(reference, name)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise AcmeError(f"Command failed with exit code {completed.returncode}")


def compile_macos_lock(config: dict, target: str, platform_name: str, *, upgrade: bool) -> None:
    if shutil.which("uv") is None:
        raise AcmeError("uv is required to compile manual lock files.")

    output = manual_lock_path(target, platform_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    reference = ensure_docker_reference(target, platform_name)

    # Compile into a temporary output and replace the committed lock only after
    # uv succeeds. This prevents a failed resolution from leaving a Linux seed
    # or a partially written macOS lock behind.
    with tempfile.TemporaryDirectory(prefix="acme-manual-lock-") as temp_dir:
        temp_root = Path(temp_dir)
        constraint = temp_root / "direct-constraints.txt"
        temp_output = temp_root / f"{target}-{platform_name}.txt"
        write_direct_constraints(config, target, platform_name, constraint)

        # uv prefers versions already present in the output file when --upgrade
        # is absent. Seed a new macOS resolution from the matching Linux Docker
        # lock; on later runs, seed from the existing macOS lock instead.
        if not upgrade:
            seed = output if output.exists() else reference
            shutil.copyfile(seed, temp_output)

        command = [
            "uv",
            "pip",
            "compile",
            *[str(path.relative_to(ROOT)) for path in target_sources(config, target)],
            "--python-version",
            config["python_version"],
            "--python-platform",
            str(PLATFORMS[platform_name]["python_platform"]),
            "--constraint",
            str(constraint),
            "--output-file",
            str(temp_output),
        ]
        if upgrade:
            command.append("--upgrade")

        env = os.environ.copy()
        env["UV_CUSTOM_COMPILE_COMMAND"] = (
            f"./manual-locks generate {target} --platform {platform_name}"
            + (" --upgrade" if upgrade else "")
        )
        run(command, env=env)
        shutil.copyfile(temp_output, output)


def copy_linux_lock(target: str, platform_name: str) -> None:
    reference = ensure_docker_reference(target, platform_name)
    output = manual_lock_path(target, platform_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(reference, output)
    print(f"Copied {reference.relative_to(ROOT)} -> {output.relative_to(ROOT)}")


def generate_locks(targets: Iterable[str], platform_name: str, *, upgrade: bool) -> None:
    config = load_config()
    for target in targets:
        if bool(PLATFORMS[platform_name]["copy_docker_lock"]):
            copy_linux_lock(target, platform_name)
        else:
            compile_macos_lock(config, target, platform_name, upgrade=upgrade)


def assert_direct_parity(config: dict, target: str, platform_name: str) -> None:
    manual = manual_lock_path(target, platform_name)
    reference = ensure_docker_reference(target, platform_name)
    for name in direct_names_for_target(config, target):
        expected = one_locked_version(reference, name)
        actual = one_locked_version(manual, name)
        if actual != expected:
            raise AcmeError(
                f"{target}/{platform_name}/{name}: manual lock has {actual}; "
                f"Docker reference has {expected}."
            )


def validate_locks(targets: Iterable[str], platform_name: str) -> None:
    config = load_config()
    for target in targets:
        path = manual_lock_path(target, platform_name)
        if not path.exists():
            raise AcmeError(f"Missing manual lock {path.relative_to(ROOT)}")
        if not parse_lock_versions(path):
            raise AcmeError(f"Manual lock contains no pinned packages: {path.relative_to(ROOT)}")
        assert_direct_parity(config, target, platform_name)

        if bool(PLATFORMS[platform_name]["copy_docker_lock"]):
            reference = ensure_docker_reference(target, platform_name)
            if path.read_bytes() != reference.read_bytes():
                raise AcmeError(
                    f"{path.relative_to(ROOT)} must exactly match "
                    f"{reference.relative_to(ROOT)}"
                )
    print(f"Manual lock validation passed for {platform_name}.")


def host_matches(platform_name: str) -> bool:
    expected = PLATFORMS[platform_name]
    system = host_platform.system()
    machine = host_platform.machine().lower()
    return system == expected["system"] and machine in expected["machine"]


def verify_native(targets: Iterable[str], platform_name: str) -> None:
    if not host_matches(platform_name):
        raise AcmeError(
            f"Native verification for {platform_name} must run on its matching host. "
            f"Current host is {host_platform.system()} {host_platform.machine()}."
        )
    if shutil.which("uv") is None:
        raise AcmeError("uv is required to verify manual lock files.")

    config = load_config()
    validate_locks(targets, platform_name)

    with tempfile.TemporaryDirectory(prefix=f"acme-{platform_name}-") as temp_dir:
        venv = Path(temp_dir) / ".venv"
        run(["uv", "python", "install", config["python_version"]])
        run(["uv", "venv", "--python", config["python_version"], str(venv)])

        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        for target in targets:
            lock = manual_lock_path(target, platform_name)
            print(f"\n==> Verifying {target} from {lock.relative_to(ROOT)}")
            run(["uv", "pip", "sync", "--python", str(python), str(lock)])
            run(["uv", "pip", "check", "--python", str(python)])

            requirement_sources = [
                str(path.relative_to(ROOT)) for path in target_sources(config, target)
            ]
            run(
                [
                    str(python),
                    str(ROOT / "scripts/smoke_requirements.py"),
                    *requirement_sources,
                ]
            )

    print(f"Native installation verification passed for {platform_name}.")


def write_manifest(targets: Iterable[str]) -> None:
    """Write small metadata files beside generated locks for traceability."""
    config = load_config()
    for target in targets:
        target_dir = MANUAL_ROOT / target
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "target": target,
            "display_name": config["images"][target]["display_name"],
            "python_version": config["python_version"],
            "uv_version": config["uv_version"],
            "docker_image_version": config["images"][target]["version"],
            "platforms": list(PLATFORMS),
        }
        (target_dir / "metadata.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def resolve_targets(tokens: Iterable[str]) -> list[str]:
    # Manual locks do not use Docker's "core expands to all downstream" behavior.
    # "all" still means all configured targets, while "core" means core only.
    config = load_config()
    return expand_targets(config, tokens, core_expands=False)


def cmd_generate(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.targets)
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]
    for platform_name in platforms:
        generate_locks(targets, platform_name, upgrade=args.upgrade)
        validate_locks(targets, platform_name)
    write_manifest(targets)


def cmd_validate(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.targets)
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]
    for platform_name in platforms:
        validate_locks(targets, platform_name)


def cmd_verify(args: argparse.Namespace) -> None:
    targets = resolve_targets(args.targets)
    if args.platform == "all":
        raise AcmeError("Native verification requires one explicit --platform value.")
    verify_native(targets, args.platform)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="Generate manual locks for one or more targets.")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--platform", choices=[*PLATFORMS, "all"], default="all")
    p.add_argument("--upgrade", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("validate", help="Validate generated locks against Docker direct pins.")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--platform", choices=[*PLATFORMS, "all"], default="all")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("verify", help="Install and smoke-test locks on the native platform.")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--platform", choices=[*PLATFORMS, "all"], required=True)
    p.set_defaults(func=cmd_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except AcmeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
