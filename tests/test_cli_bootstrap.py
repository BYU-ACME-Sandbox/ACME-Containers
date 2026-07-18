from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]


class BootstrapCliTests(unittest.TestCase):
    def test_fake_resolution_bootstraps_and_then_bumps_one_course(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(SOURCE_ROOT, root)
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            uv = fake_bin / "uv"
            uv.write_text(
                textwrap.dedent(
                    r'''#!/usr/bin/env python3
import re
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] != ["pip", "compile"]:
    raise SystemExit(f"Unexpected fake uv command: {args}")
output = Path(args[args.index("--output-file") + 1])
sources = []
for value in args[2:]:
    if value.startswith("--"):
        break
    sources.append(Path(value))
pattern = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?")
names = set()
for source in sources:
    for raw in source.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text("# fake architecture lock\n" + "\n".join(f"{name}==1.0.0" for name in sorted(names)) + "\n")
'''
                ),
                encoding="utf-8",
            )
            uv.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"

            # Make the test independent of the repository's current real release.
            config_path = root / "config/images.json"
            config = json.loads(config_path.read_text())
            config["release_month"] = "2026.08"
            config["publishing_enabled"] = False
            for image in config["images"].values():
                image["version"] = "2026.08.0"
            config_path.write_text(json.dumps(config, indent=2) + "\n")

            subprocess.run(
                ["./acme-containers", "release", "all"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            config = json.loads((root / "config/images.json").read_text())
            self.assertTrue(config["publishing_enabled"])
            self.assertTrue((root / "requirements/locks/core/amd64.txt").exists())
            self.assertTrue((root / "requirements/locks/core/arm64.txt").exists())
            self.assertTrue((root / "requirements/locks/courses/vol3b/amd64.txt").exists())
            self.assertTrue((root / "requirements/locks/dev/arm64.txt").exists())
            self.assertEqual(config["images"]["vol3b"]["version"], "2026.08.0")

            subprocess.run(
                ["./acme-containers", "release", "vol3b"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            config = json.loads((root / "config/images.json").read_text())
            self.assertEqual(config["images"]["vol3b"]["version"], "2026.08.1")
            self.assertEqual(config["images"]["vol3a"]["version"], "2026.08.0")


if __name__ == "__main__":
    unittest.main()
