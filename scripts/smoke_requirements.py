#!/usr/bin/env python3
"""Import every direct Python requirement declared in one or more .in files.

The requirement files remain the source of truth.  Import names are discovered
from installed distribution metadata, with a very small override/skip table for
packages whose distribution name does not map cleanly to the most useful import.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import re
import sys
from pathlib import Path
from typing import Iterable

REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?")

# Only genuine distribution/import exceptions belong here.  Course membership
# is intentionally NOT encoded here; it is derived from the .in files.
IMPORT_OVERRIDES: dict[str, tuple[str, ...]] = {
    # Importing cartopy.crs exercises Cartopy's projection machinery rather
    # than merely importing its package root.
    "cartopy": ("cartopy.crs",),
    # Namespace packages can otherwise resolve only to ``sphinxcontrib``.
    "sphinxcontrib-bibtex": ("sphinxcontrib.bibtex",),
}

# ``jupyter`` is a metapackage.  The container smoke test verifies the Jupyter
# CLI separately, while installation itself is checked by ``uv pip check``.
SKIP_IMPORTS: dict[str, str] = {
    "jupyter": "metapackage; Jupyter CLI is tested separately",
}


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_distribution_names(paths: Iterable[Path]) -> list[str]:
    """Return canonical direct distribution names from requirement inputs."""
    names: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            raise RuntimeError(f"Requirement source does not exist: {path}")
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "http://", "https://")):
                continue
            match = REQ_NAME_RE.match(line)
            if not match:
                raise RuntimeError(f"Could not parse requirement in {path}: {raw!r}")
            name = canonical_name(match.group(1))
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _public_top_level_modules(distribution: str) -> list[str]:
    """Find public top-level modules belonging to an installed distribution."""
    wanted = canonical_name(distribution)
    modules: set[str] = set()
    for module, distributions in importlib.metadata.packages_distributions().items():
        if not module or module.startswith("_") or not module.isidentifier():
            continue
        if any(canonical_name(item) == wanted for item in distributions):
            modules.add(module)
    return sorted(modules, key=str.casefold)


def import_modules_for_distribution(distribution: str) -> tuple[str, ...]:
    """Resolve the most representative import module for a distribution."""
    name = canonical_name(distribution)

    # Ensure the distribution itself is installed even for skipped metapackages.
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"Direct requirement {name!r} is not installed") from exc

    if name in SKIP_IMPORTS:
        return ()
    if name in IMPORT_OVERRIDES:
        return IMPORT_OVERRIDES[name]

    candidates = _public_top_level_modules(name)
    preferred = name.replace("-", "_")

    # Prefer the ordinary package-name transformation when it exists.  This
    # selects matplotlib over mpl_toolkits/pylab, pytest over py, etc.
    for candidate in candidates:
        if candidate.casefold() == preferred.casefold():
            return (candidate,)

    if len(candidates) == 1:
        return (candidates[0],)

    # Some wheels omit top_level metadata.  Try the standard hyphen->underscore
    # import as a final deterministic fallback before requiring an override.
    if importlib.util.find_spec(preferred) is not None:
        return (preferred,)

    if not candidates:
        raise RuntimeError(
            f"Could not determine an import for distribution {name!r}. "
            "Add a deliberate IMPORT_OVERRIDES or SKIP_IMPORTS entry."
        )
    raise RuntimeError(
        f"Ambiguous imports for distribution {name!r}: {', '.join(candidates)}. "
        "Add a deliberate IMPORT_OVERRIDES entry."
    )


def jax_functional_check() -> None:
    """Exercise JAX compilation when JAX is a direct requirement."""
    import jax
    import jax.numpy as jnp

    result = jax.jit(lambda x: x @ x)(jnp.eye(3))
    if result.shape != (3, 3):
        raise RuntimeError("Unexpected JAX result")
    print(f"JAX backend: {jax.default_backend()}")


def smoke_requirement_files(paths: Iterable[Path]) -> list[str]:
    """Import every direct distribution declared by the supplied .in files."""
    paths = list(paths)
    distributions = direct_distribution_names(paths)
    imported: list[str] = []

    for distribution in distributions:
        if distribution in SKIP_IMPORTS:
            # version() inside import_modules_for_distribution still verifies
            # that the metapackage itself is installed.
            import_modules_for_distribution(distribution)
            print(f"Skipping import for {distribution}: {SKIP_IMPORTS[distribution]}")
            continue

        modules = import_modules_for_distribution(distribution)
        for module in modules:
            print(f"Importing {distribution} -> {module}...", flush=True)
            importlib.import_module(module)
            imported.append(module)

    if "jax" in distributions:
        jax_functional_check()

    print(
        f"Direct requirement smoke test passed: "
        f"{len(distributions)} distributions, {len(imported)} imports."
    )
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", nargs="+", type=Path, help="Requirement .in files")
    args = parser.parse_args()
    smoke_requirement_files(args.requirements)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
