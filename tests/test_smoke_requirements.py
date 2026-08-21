from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_requirements", ROOT / "scripts/smoke_requirements.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SmokeRequirementTests(unittest.TestCase):
    def test_requirement_parser_handles_extras_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.in"
            path.write_text(
                "# comment\n"
                "gymnasium[classic-control]~=0.29.1\n"
                "scikit-learn>=1.3,<2  # inline comment\n",
                encoding="utf-8",
            )
            self.assertEqual(
                smoke.direct_distribution_names([path]),
                ["gymnasium", "scikit-learn"],
            )

    def test_all_current_requirement_inputs_parse(self) -> None:
        paths = [ROOT / "requirements/core.in"]
        paths.extend(sorted((ROOT / "requirements/courses").glob("*.in")))
        paths.extend(sorted((ROOT / "requirements/dev").glob("*.in")))
        names = smoke.direct_distribution_names(paths)
        self.assertIn("numpy", names)
        self.assertIn("cartopy", names)
        self.assertIn("sphinxcontrib-bibtex", names)

    @mock.patch.object(smoke.importlib.metadata, "version", return_value="1.0")
    @mock.patch.object(
        smoke.importlib.metadata,
        "packages_distributions",
        return_value={"sklearn": ["scikit-learn"]},
    )
    def test_distribution_metadata_maps_nonmatching_import_name(
        self, _packages: mock.Mock, _version: mock.Mock
    ) -> None:
        self.assertEqual(
            smoke.import_modules_for_distribution("scikit-learn"),
            ("sklearn",),
        )

    @mock.patch.object(smoke.importlib.metadata, "version", return_value="0.25.0")
    def test_cartopy_uses_stronger_override(self, _version: mock.Mock) -> None:
        self.assertEqual(
            smoke.import_modules_for_distribution("cartopy"),
            ("cartopy.crs",),
        )

    @mock.patch.object(smoke.importlib.metadata, "version", return_value="1.1.1")
    def test_jupyter_metapackage_is_deliberately_skipped(self, _version: mock.Mock) -> None:
        self.assertEqual(smoke.import_modules_for_distribution("jupyter"), ())


if __name__ == "__main__":
    unittest.main()
