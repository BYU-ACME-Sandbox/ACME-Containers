#!/usr/bin/env python3
"""ACME container dependency, release, and generated-file tooling."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "images.json"
CORE_INPUT = ROOT / "requirements" / "core.in"
LOCK_ROOT = ROOT / "requirements" / "locks"
GENERATED_ROOT = ROOT / "generated"
COURSE_ORDER = ["vol1a", "vol1b", "vol2a", "vol2b", "vol3a", "vol3b", "vol4a", "vol4b"]
ALL_ORDER = ["core", *COURSE_ORDER, "dev"]
ARCHES = {
    "amd64": "x86_64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
}
VERSION_RE = re.compile(r"^(?P<month>\d{4}\.\d{2})\.(?P<patch>\d+)$")
REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?")
LOCK_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)(?:\s*;.*)?(?:\s*\\)?$")


class AcmeError(RuntimeError):
    pass


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcmeError(f"Could not read {CONFIG_PATH}: {exc}") from exc


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def image_targets(config: dict[str, Any], *, include_core: bool = True) -> list[str]:
    targets = [t for t in ALL_ORDER if t in config["images"]]
    return targets if include_core else [t for t in targets if t != "core"]


def parse_target_tokens(tokens: Iterable[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        for part in token.split(","):
            value = part.strip().lower()
            if value:
                result.append(value)
    return result


def expand_targets(config: dict[str, Any], tokens: Iterable[str], *, core_expands: bool = True) -> list[str]:
    requested = parse_target_tokens(tokens)
    if not requested or "all" in requested:
        return image_targets(config)
    unknown = sorted(set(requested) - set(config["images"]))
    if unknown:
        raise AcmeError(f"Unknown target(s): {', '.join(unknown)}")
    expanded = set(requested)
    if core_expands and "core" in expanded:
        expanded.update(image_targets(config))
    return [t for t in ALL_ORDER if t in expanded]


def core_lock_path(arch: str) -> Path:
    return LOCK_ROOT / "core" / f"{arch}.txt"


def core_direct_path(arch: str) -> Path:
    return LOCK_ROOT / "core" / f"direct-{arch}.txt"


def image_lock_path(target: str, arch: str) -> Path:
    if target == "core":
        return core_lock_path(arch)
    if target == "dev":
        return LOCK_ROOT / "dev" / f"{arch}.txt"
    return LOCK_ROOT / "courses" / target / f"{arch}.txt"


def direct_requirement_specs(path: Path) -> dict[str, str]:
    specs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://")):
            continue
        match = REQ_NAME_RE.match(line)
        if not match:
            raise AcmeError(f"Could not parse requirement in {path}: {raw!r}")
        name = canonical_name(match.group(1))
        normalized = re.sub(r"\s+", "", line).lower()
        if name in specs and specs[name] != normalized:
            raise AcmeError(f"Conflicting direct requirements for {name} in {path}")
        specs[name] = normalized
    return specs


def direct_requirement_names(path: Path) -> list[str]:
    return list(direct_requirement_specs(path))


def parse_lock_versions(path: Path) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    if not path.exists():
        return versions
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "--")):
            continue
        match = LOCK_RE.match(line)
        if not match:
            continue
        versions.setdefault(canonical_name(match.group(1)), set()).add(match.group(2))
    return versions


def one_locked_version(path: Path, package: str) -> str:
    values = parse_lock_versions(path).get(canonical_name(package), set())
    if len(values) != 1:
        found = ", ".join(sorted(values)) if values else "none"
        raise AcmeError(f"Expected one locked version of {package} in {path}; found {found}.")
    return next(iter(values))


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise AcmeError(f"Command failed with exit code {completed.returncode}")


def uv_compile(sources: list[Path], output: Path, arch: str, *, constraint: Path | None, upgrade: bool) -> None:
    config = load_config()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "uv", "pip", "compile",
        *[str(p.relative_to(ROOT)) for p in sources],
        "--python-version", config["python_version"],
        "--python-platform", ARCHES[arch],
        "--output-file", str(output.relative_to(ROOT)),
    ]
    if constraint:
        command += ["--constraint", str(constraint.relative_to(ROOT))]
    if upgrade:
        command.append("--upgrade")
    env = os.environ.copy()
    env["UV_CUSTOM_COMPILE_COMMAND"] = f"./acme-containers locks --arch {arch}" + (" --upgrade" if upgrade else "")
    run(command, env=env)


def write_core_direct_constraints(arch: str) -> None:
    lines = [
        "# Generated from requirements/core.in and the complete core lock.",
        "# These exact direct versions must match in every image.",
        "",
    ]
    for name in direct_requirement_names(CORE_INPUT):
        lines.append(f"{name}=={one_locked_version(core_lock_path(arch), name)}")
    core_direct_path(arch).write_text("\n".join(lines) + "\n", encoding="utf-8")


def compile_locks(targets: list[str], arch: str, *, upgrade: bool) -> None:
    config = load_config()
    if shutil.which("uv") is None:
        raise AcmeError("uv is required to compile lock files.")
    need_core = (
        "core" in targets
        or not core_lock_path(arch).exists()
        or not core_direct_path(arch).exists()
    )
    if need_core:
        uv_compile([CORE_INPUT], core_lock_path(arch), arch, constraint=None, upgrade=upgrade)
        write_core_direct_constraints(arch)

    constraint = core_direct_path(arch)
    for target in targets:
        if target == "core":
            continue
        if target == "dev":
            sources = [
                CORE_INPUT,
                ROOT / "requirements/dev/labs.in",
                ROOT / "requirements/dev/sphinx.in",
                ROOT / "requirements/dev/tools.in",
            ]
        else:
            sources = [CORE_INPUT, ROOT / config["images"][target]["requirements"]]
        uv_compile(sources, image_lock_path(target, arch), arch, constraint=constraint, upgrade=upgrade)


def bump_version(current: str, month: str) -> str:
    match = VERSION_RE.fullmatch(current)
    if not match:
        raise AcmeError(f"Invalid image version {current!r}; expected YYYY.MM.PATCH")
    if match.group("month") == month:
        return f"{month}.{int(match.group('patch')) + 1}"
    return f"{month}.0"


def bump_targets(config: dict[str, Any], targets: list[str], *, month: str, bootstrap: bool) -> None:
    if not re.fullmatch(r"\d{4}\.\d{2}", month):
        raise AcmeError("Release month must have the form YYYY.MM")
    config["release_month"] = month
    if not bootstrap:
        for target in targets:
            config["images"][target]["version"] = bump_version(config["images"][target]["version"], month)


def image_reference(config: dict[str, Any], target: str) -> str:
    image = config["images"][target]
    return f"{config['registry']}/{image['repository']}:{image['version']}"


def student_devcontainer(config: dict[str, Any], target: str) -> dict[str, Any]:
    image = config["images"][target]
    return {
        "name": f"{image['display_name']} (Python 3.13)",
        "image": image_reference(config, target),
        "workspaceFolder": "/workspaces/${localWorkspaceFolderBasename}",
        "containerUser": "vscode",
        "postCreateCommand": (
            'python -m ipykernel install --user --name acme --display-name "ACME Python" '
            "&& (test -f .utils/install_completions.sh && "
            "bash .utils/install_completions.sh </dev/null || true)"
        ),
        "postStartCommand": (
            "git config --global --add safe.directory '*' || true; "
            "(test -f .utils/install_completions.sh && "
            "bash .utils/install_completions.sh </dev/null || true)"
        ),
        "customizations": {"vscode": {
            "settings": {
                "terminal.integrated.defaultProfile.linux": "bash",
                "terminal.integrated.profiles.linux": {"bash": {"path": "/bin/bash"}},
                "files.autoSave": "afterDelay",
                "files.autoSaveDelay": 1000,
                "files.eol": "\n",
                "files.exclude": {
                    "**/.*": True, "**/.devcontainer": True, "**/.vscode": True,
                    "**/.git": True, "**/.github": True, "**/.gitignore": True,
                    "**/requirements.txt": True, "**/*.md": True, "**/*.tex": True,
                    "**/content": True, "**/__pycache__": True, "**/*.pyc": True,
                    "**/validate_driver.txt": True, "**/.utils": True,
                },
                "github.copilot.enable": False,
                "github.copilot-chat.enable": False,
                "extensions.ignoreRecommendations": True,
                "extensions.unwantedRecommendations": ["GitHub.copilot", "GitHub.copilot-chat"],
                "extensions.autoUpdate": False,
                "extensions.autoCheckUpdates": False,
                "update.mode": "none",
                "python.defaultInterpreterPath": "/opt/acme-venv/bin/python",
                "python.terminal.activateEnvironment": False,
                "jupyter.jupyterServerType": "local",
                "jupyter.defaultKernel": "ACME Python",
            },
            "extensions": [
                "ms-python.python", "ms-toolsai.jupyter",
                "ms-toolsai.jupyter-keymap", "ms-toolsai.jupyter-renderers",
                "ms-azuretools.vscode-docker",
            ],
        }},
    }


def dev_devcontainer(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "ACME Lab Development (Python 3.13)",
        "image": image_reference(config, "dev"),
        "workspaceFolder": "/workspaces/${localWorkspaceFolderBasename}",
        "containerUser": "vscode",
        "postCreateCommand": (
            "echo 'Container created. Configure GitHub credentials, then run: "
            "./.setup_tools (or ./.setup_tools --czar)'"
        ),
        "postStartCommand": "git config --global --add safe.directory '*' || true",
        "customizations": {"vscode": {
            "settings": {
                "terminal.integrated.defaultProfile.linux": "bash",
                "terminal.integrated.profiles.linux": {"bash": {"path": "/bin/bash"}},
                "files.autoSave": "afterDelay",
                "files.autoSaveDelay": 1000,
                "files.eol": "\n",
                "python.defaultInterpreterPath": "/opt/acme-venv/bin/python",
                "python.terminal.activateEnvironment": False,
                "jupyter.jupyterServerType": "local",
                "jupyter.defaultKernel": "ACME Dev Python",
                "extensions.autoUpdate": False,
                "extensions.autoCheckUpdates": False,
                "update.mode": "none",
            },
            "extensions": [
                "ms-python.python", "ms-toolsai.jupyter",
                "ms-toolsai.jupyter-keymap", "ms-toolsai.jupyter-renderers",
                "ms-azuretools.vscode-docker",
            ],
        }},
    }


def release_manifest(config: dict[str, Any]) -> dict[str, Any]:
    core = config["images"]["core"]
    images: dict[str, Any] = {}
    for target in image_targets(config):
        image = config["images"][target]
        entry = {
            "kind": image["kind"],
            "repository": f"{config['registry']}/{image['repository']}",
            "version": image["version"],
            "reference": image_reference(config, target),
            "display_name": image["display_name"],
        }
        if target != "core":
            entry["base_core"] = f"{config['registry']}/{core['repository']}:{core['version']}"
        images[target] = entry
    return {
        "schema_version": 1,
        "source_repository": config["source_repository"],
        "python_version": config["python_version"],
        "python_image_variant": config["python_image_variant"],
        "uv_version": config["uv_version"],
        "release_month": config["release_month"],
        "images": images,
    }


def expected_generated(config: dict[str, Any]) -> dict[Path, str]:
    values = {
        GENERATED_ROOT / "release-manifest.json": json.dumps(release_manifest(config), indent=2) + "\n",
        GENERATED_ROOT / "devcontainers/dev/devcontainer.json": json.dumps(dev_devcontainer(config), indent=2) + "\n",
    }
    for target in COURSE_ORDER:
        values[GENERATED_ROOT / "devcontainers" / target / "devcontainer.json"] = (
            json.dumps(student_devcontainer(config, target), indent=2) + "\n"
        )
    return values


def render_generated(config: dict[str, Any]) -> None:
    for path, text in expected_generated(config).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def validate(*, allow_missing_locks: bool = False) -> None:
    config = load_config()
    errors: list[str] = []
    if config.get("registry") != "ghcr.io/byu-acme-sandbox":
        errors.append("Registry should be ghcr.io/byu-acme-sandbox")
    if not re.fullmatch(r"3\.13\.\d+", config.get("python_version", "")):
        errors.append("python_version must be an exact Python 3.13 patch")
    if config.get("python_image_variant") not in {"slim-bookworm", "slim-trixie"}:
        errors.append("python_image_variant must be an approved explicit Debian slim variant")
    if set(config["images"]) != set(ALL_ORDER):
        errors.append("Configuration must define core, eight courses, and dev")

    for target, image in config["images"].items():
        if not VERSION_RE.fullmatch(image.get("version", "")):
            errors.append(f"{target}: invalid version")
        if target in COURSE_ORDER and not (ROOT / image["requirements"]).exists():
            errors.append(f"{target}: missing requirements input")

    try:
        core_specs = direct_requirement_specs(CORE_INPUT)
        course_union: dict[str, str] = {}
        owners: dict[str, list[str]] = {}
        for target in COURSE_ORDER:
            specs = direct_requirement_specs(ROOT / config["images"][target]["requirements"])
            overlap = sorted(set(specs) & set(core_specs))
            if overlap:
                errors.append(f"{target}: duplicates core packages: {', '.join(overlap)}")
            for name, spec in specs.items():
                owners.setdefault(name, []).append(target)
                if name in course_union and course_union[name] != spec:
                    errors.append(f"{name}: inconsistent course bounds across {', '.join(owners[name])}")
                else:
                    course_union[name] = spec
        dev_specs = direct_requirement_specs(ROOT / "requirements/dev/labs.in")
        missing = sorted(set(course_union) - set(dev_specs))
        if missing:
            errors.append("Development labs input missing: " + ", ".join(missing))
        for name, spec in course_union.items():
            if name in dev_specs and dev_specs[name] != spec:
                errors.append(f"Development bound for {name} differs from course bound")
        overlap = sorted(set(dev_specs) & set(core_specs))
        if overlap:
            errors.append("Development labs duplicates core packages: " + ", ".join(overlap))
    except AcmeError as exc:
        errors.append(str(exc))

    require_locks = config.get("publishing_enabled", False) and not allow_missing_locks
    locks_exist = all(core_lock_path(a).exists() for a in ARCHES)
    if require_locks and not locks_exist:
        errors.append("Publishing enabled but core locks missing")

    if locks_exist:
        core_names = direct_requirement_names(CORE_INPUT)
        arch_direct: dict[str, dict[str, str]] = {}
        for arch in ARCHES:
            if not core_direct_path(arch).exists():
                errors.append(f"Missing {core_direct_path(arch)}")
                continue
            arch_direct[arch] = {}
            for name in core_names:
                try:
                    locked = one_locked_version(core_lock_path(arch), name)
                    constrained = one_locked_version(core_direct_path(arch), name)
                    arch_direct[arch][name] = locked
                    if locked != constrained:
                        errors.append(f"{arch}/{name}: core lock and constraint differ")
                except AcmeError as exc:
                    errors.append(str(exc))
        if set(arch_direct) == set(ARCHES):
            for name in core_names:
                values = {arch_direct[a].get(name) for a in ARCHES}
                if len(values) != 1:
                    errors.append(f"Core direct package {name} differs across architectures")
        for target in image_targets(config, include_core=False):
            if target == "dev":
                input_paths = [
                    ROOT / "requirements/dev/labs.in",
                    ROOT / "requirements/dev/sphinx.in",
                    ROOT / "requirements/dev/tools.in",
                ]
            else:
                input_paths = [ROOT / config["images"][target]["requirements"]]
            expected_direct = {
                name
                for input_path in input_paths
                for name in direct_requirement_names(input_path)
            }

            for arch in ARCHES:
                path = image_lock_path(target, arch)
                if not path.exists():
                    if require_locks:
                        errors.append(f"Missing {path}")
                    continue
                locked_names = set(parse_lock_versions(path))
                missing_direct = sorted(expected_direct - locked_names)
                if missing_direct:
                    errors.append(
                        f"{target}/{arch}: lock missing direct packages: "
                        + ", ".join(missing_direct)
                    )
                for name in core_names:
                    try:
                        expected = one_locked_version(core_direct_path(arch), name)
                        actual = one_locked_version(path, name)
                        if actual != expected:
                            errors.append(f"{target}/{arch}/{name}: {actual} != {expected}")
                    except AcmeError as exc:
                        errors.append(str(exc))

    for path, expected in expected_generated(config).items():
        if not path.exists():
            errors.append(f"Missing generated file {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            errors.append(f"Stale generated file {path.relative_to(ROOT)}")

    if errors:
        raise AcmeError("Validation failed:\n- " + "\n- ".join(errors))
    print("Validation passed.")


def finalize_release(tokens: Iterable[str], *, month: str | None = None) -> list[str]:
    config = load_config()
    targets = expand_targets(config, tokens)
    bootstrap = not config.get("publishing_enabled", False)
    bump_targets(config, targets, month=month or config["release_month"], bootstrap=bootstrap)
    config["publishing_enabled"] = True
    save_config(config)
    render_generated(config)
    validate()
    return targets


def git_show_json(revision: str, path: str) -> dict[str, Any] | None:
    if not revision:
        return None
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def plan_targets(before: str, after: str, requested: str) -> list[str]:
    config = load_config()
    if requested and requested != "auto":
        return expand_targets(config, [requested])
    if not config.get("publishing_enabled", False):
        return []
    old = git_show_json(before, "config/images.json")
    if old is None or not old.get("publishing_enabled", False):
        return image_targets(config)

    selected: set[str] = set()
    for target in image_targets(config):
        old_image = old.get("images", {}).get(target, {})
        new_image = config["images"][target]
        if old_image.get("version") != new_image["version"]:
            selected.add(target)
        for key in ("repository", "kind", "requirements", "smoke_imports"):
            if old_image.get(key) != new_image.get(key):
                selected.add(target)

    # Automatic publication is version-driven. Build-affecting source or lock
    # changes without a version bump intentionally produce no image publication,
    # preventing a stable release tag from being overwritten accidentally.
    # A manual workflow_dispatch target can still rebuild an existing version.
    if "core" in selected:
        selected.update(image_targets(config))
    return [t for t in ALL_ORDER if t in selected]


def write_github_output(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    with open(output, "a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def metadata(config: dict[str, Any], target: str) -> dict[str, str]:
    if target not in config["images"]:
        raise AcmeError(f"Unknown target {target}")
    image = config["images"][target]
    dockerfile = {"core": "docker/core.Dockerfile", "course": "docker/course.Dockerfile", "dev": "docker/dev.Dockerfile"}[image["kind"]]
    core = config["images"]["core"]
    return {
        "target": target,
        "kind": image["kind"],
        "dockerfile": dockerfile,
        "image": f"{config['registry']}/{image['repository']}",
        "version": image["version"],
        "python_version": config["python_version"],
        "python_image_variant": config["python_image_variant"],
        "uv_version": config["uv_version"],
        "svgo_version": config["svgo_version"],
        "core_image": f"{config['registry']}/{core['repository']}",
        "core_version": core["version"],
        "source_url": f"https://github.com/{config['source_repository']}",
    }


def cmd_status(_: argparse.Namespace) -> None:
    c = load_config()
    print(f"Registry:   {c['registry']}")
    print(f"Python:     {c['python_version']} ({c['python_image_variant']})")
    print(f"uv:         {c['uv_version']}")
    print(f"Month:      {c['release_month']}")
    print(f"Publishing: {'enabled' if c['publishing_enabled'] else 'disabled (bootstrap mode)'}\n")
    for target in image_targets(c):
        print(f"{target:6} {image_reference(c, target)}")


def cmd_render(_: argparse.Namespace) -> None:
    render_generated(load_config())
    print("Generated devcontainers and release manifest.")


def cmd_locks(args: argparse.Namespace) -> None:
    c = load_config()
    targets = expand_targets(c, args.targets)
    for arch in ARCHES if args.arch == "all" else [args.arch]:
        compile_locks(targets, arch, upgrade=args.upgrade)
    validate(allow_missing_locks=True)


def cmd_release(args: argparse.Namespace) -> None:
    c = load_config()
    targets = expand_targets(c, args.targets)
    for arch in ARCHES if args.arch == "all" else [args.arch]:
        compile_locks(targets, arch, upgrade=args.upgrade)
    bootstrap = not c.get("publishing_enabled", False)
    finalized = finalize_release(args.targets, month=args.month)
    print("Bootstrap release prepared." if bootstrap else "Bumped: " + ", ".join(finalized))


def cmd_finalize(args: argparse.Namespace) -> None:
    c = load_config()
    bootstrap = not c.get("publishing_enabled", False)
    targets = finalize_release(args.targets, month=args.month)
    print("Bootstrap release finalized." if bootstrap else "Bumped: " + ", ".join(targets))


def cmd_bump(args: argparse.Namespace) -> None:
    c = load_config()
    targets = expand_targets(c, args.targets)
    bump_targets(c, targets, month=args.month or c["release_month"], bootstrap=False)
    save_config(c)
    render_generated(c)
    print("Bumped: " + ", ".join(targets))


def cmd_set_month(args: argparse.Namespace) -> None:
    c = load_config()
    if not re.fullmatch(r"\d{4}\.\d{2}", args.month):
        raise AcmeError("Release month must be YYYY.MM")
    c["release_month"] = args.month
    save_config(c)
    render_generated(c)


def cmd_validate(args: argparse.Namespace) -> None:
    validate(allow_missing_locks=args.allow_missing_locks)


def cmd_metadata(args: argparse.Namespace) -> None:
    values = metadata(load_config(), args.target)
    write_github_output(values) if args.github_output else print(json.dumps(values, indent=2))


def cmd_ci_plan(args: argparse.Namespace) -> None:
    targets = plan_targets(args.before, args.after, args.requested)
    downstream = [t for t in targets if t != "core"]
    values = {
        "targets": json.dumps(targets, separators=(",", ":")),
        "build_core": "true" if "core" in targets else "false",
        "downstream": json.dumps(downstream or ["none"], separators=(",", ":")),
        "has_downstream": "true" if downstream else "false",
    }
    write_github_output(values)
    print("Planned targets:", ", ".join(targets) if targets else "none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("render"); p.set_defaults(func=cmd_render)
    p = sub.add_parser("locks")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--arch", choices=[*ARCHES, "all"], default="all")
    p.add_argument("--upgrade", action="store_true")
    p.set_defaults(func=cmd_locks)
    p = sub.add_parser("release")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--arch", choices=[*ARCHES, "all"], default="all")
    p.add_argument("--month")
    p.add_argument("--upgrade", action="store_true")
    p.set_defaults(func=cmd_release)
    p = sub.add_parser("finalize-release")
    p.add_argument("targets", nargs="*", default=["all"])
    p.add_argument("--month")
    p.set_defaults(func=cmd_finalize)
    p = sub.add_parser("bump")
    p.add_argument("targets", nargs="+")
    p.add_argument("--month")
    p.set_defaults(func=cmd_bump)
    p = sub.add_parser("set-month"); p.add_argument("month"); p.set_defaults(func=cmd_set_month)
    p = sub.add_parser("validate"); p.add_argument("--allow-missing-locks", action="store_true"); p.set_defaults(func=cmd_validate)
    p = sub.add_parser("metadata"); p.add_argument("target"); p.add_argument("--github-output", action="store_true"); p.set_defaults(func=cmd_metadata)
    p = sub.add_parser("ci-plan")
    p.add_argument("--before", default="")
    p.add_argument("--after", default="HEAD")
    p.add_argument("--requested", default="auto")
    p.set_defaults(func=cmd_ci_plan)
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
