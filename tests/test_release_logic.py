from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("acme_container_tool", ROOT / "scripts/acme.py")
assert SPEC and SPEC.loader
acme = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acme)


class ReleaseLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = acme.load_config()

    def test_same_month_increments_patch(self) -> None:
        self.assertEqual(acme.bump_version("2026.08.0", "2026.08"), "2026.08.1")
        self.assertEqual(acme.bump_version("2026.08.9", "2026.08"), "2026.08.10")

    def test_new_month_resets_patch(self) -> None:
        self.assertEqual(acme.bump_version("2026.08.9", "2027.01"), "2027.01.0")

    def test_core_target_expands_to_every_image(self) -> None:
        self.assertEqual(
            acme.expand_targets(self.config, ["core"]),
            acme.ALL_ORDER,
        )

    def test_single_course_remains_isolated(self) -> None:
        self.assertEqual(acme.expand_targets(self.config, ["vol3b"]), ["vol3b"])
        self.assertEqual(
            acme.expand_targets(self.config, ["vol1a,vol4b"]),
            ["vol1a", "vol4b"],
        )

    def test_bootstrap_does_not_increment_initial_versions(self) -> None:
        config = copy.deepcopy(self.config)
        original = {target: image["version"] for target, image in config["images"].items()}
        acme.bump_targets(config, ["vol3b"], month="2026.08", bootstrap=True)
        self.assertEqual(
            {target: image["version"] for target, image in config["images"].items()},
            original,
        )

    def test_nonbootstrap_bumps_only_selected_course(self) -> None:
        config = copy.deepcopy(self.config)
        original = {
            target: image["version"]
            for target, image in config["images"].items()
        }
        month = config["release_month"]

        acme.bump_targets(
            config,
            ["vol3b"],
            month=month,
            bootstrap=False,
        )

        self.assertEqual(
            config["images"]["vol3b"]["version"],
            acme.bump_version(original["vol3b"], month),
        )

        for target in acme.ALL_ORDER:
            if target != "vol3b":
                self.assertEqual(
                    config["images"][target]["version"],
                    original[target],
                )

    def test_generated_devcontainers_use_versioned_images_and_shared_venv(
        self,
    ) -> None:
        for target in acme.COURSE_ORDER:
            payload = acme.student_devcontainer(self.config, target)

            self.assertEqual(
                payload["image"],
                acme.image_reference(self.config, target),
            )
            self.assertNotIn("containerName", payload)
            self.assertEqual(
                payload["customizations"]["vscode"]["settings"][
                    "python.defaultInterpreterPath"
                ],
                "/opt/acme-venv/bin/python",
            )
            self.assertEqual(
                payload["remoteEnv"]["VIRTUAL_ENV"],
                "/opt/acme-venv",
            )

    def test_release_manifest_contains_all_images(self) -> None:
        manifest = acme.release_manifest(self.config)

        self.assertEqual(
            list(manifest["images"]),
            acme.ALL_ORDER,
        )
        self.assertEqual(
            manifest["images"]["dev"]["base_core"],
            acme.image_reference(self.config, "core"),
        )

        json.dumps(manifest)

    def test_metadata_selects_correct_dockerfile(self) -> None:
        self.assertEqual(acme.metadata(self.config, "core")["dockerfile"], "docker/core.Dockerfile")
        self.assertEqual(acme.metadata(self.config, "vol2b")["dockerfile"], "docker/course.Dockerfile")
        self.assertEqual(acme.metadata(self.config, "dev")["dockerfile"], "docker/dev.Dockerfile")


if __name__ == "__main__":
    unittest.main()
