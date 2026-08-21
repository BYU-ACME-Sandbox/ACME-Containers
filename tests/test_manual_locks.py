import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("manual_locks", ROOT / "scripts/manual_locks.py")
manual_locks = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(manual_locks)


class ManualLockTests(unittest.TestCase):
    def test_supported_platforms(self):
        self.assertEqual(
            set(manual_locks.PLATFORMS),
            {"linux-amd64", "linux-arm64", "macos-arm64"},
        )

    def test_linux_maps_to_existing_docker_arches(self):
        self.assertEqual(manual_locks.PLATFORMS["linux-amd64"]["reference_arch"], "amd64")
        self.assertEqual(manual_locks.PLATFORMS["linux-arm64"]["reference_arch"], "arm64")
        self.assertTrue(manual_locks.PLATFORMS["linux-amd64"]["copy_docker_lock"])
        self.assertTrue(manual_locks.PLATFORMS["linux-arm64"]["copy_docker_lock"])

    def test_macos_target_triple(self):
        self.assertEqual(
            manual_locks.PLATFORMS["macos-arm64"]["python_platform"],
            "aarch64-apple-darwin",
        )

    def test_course_direct_names_include_core_and_course(self):
        config = manual_locks.load_config()
        names = set(manual_locks.direct_names_for_target(config, "vol2b"))
        self.assertIn("numpy", names)
        self.assertIn("cvxopt", names)
        self.assertIn("cvxpy", names)
        self.assertIn("gymnasium", names)

    def test_core_does_not_expand_to_every_target(self):
        self.assertEqual(manual_locks.resolve_targets(["core"]), ["core"])

    def test_all_target_order_matches_repository(self):
        self.assertEqual(manual_locks.resolve_targets(["all"]), manual_locks.ALL_ORDER)


if __name__ == "__main__":
    unittest.main()
