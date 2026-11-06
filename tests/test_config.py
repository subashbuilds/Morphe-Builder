import tempfile
import unittest
from pathlib import Path

from config import ConfigError, load_config


BASE_CONFIG = """
defaults:
  architectures: ["arm64-v8a", "universal"]
  build_mode: "apk"
  cli:
    repo: "owner/cli"
    asset_regex: '^cli.*\\.jar$'
  patches:
    repo: "owner/patches"
    asset_regex: '^patches.*\\.mpp$'
apps:
  - id: test_app
    name: "Test App"
    package_name: "com.example.test"
    apkmirror:
      org: "example"
      app: "test"
    patches:
      repo: "owner/test-patches"
"""


class ConfigTests(unittest.TestCase):
    def load(self, yaml_text):
        temp = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        try:
            temp.write(yaml_text)
            temp.close()
            return load_config(Path(temp.name))
        finally:
            Path(temp.name).unlink(missing_ok=True)

    def test_numeric_dpi_is_normalized(self):
        app = self.load(BASE_CONFIG + "    dpi: 480dpi\n")[0]
        self.assertEqual(app.dpi, "480")

    def test_dpi_can_be_inherited_from_defaults(self):
        config = BASE_CONFIG.replace(
            '  build_mode: "apk"', '  build_mode: "apk"\n  dpi: "nodpi"'
        )
        self.assertEqual(self.load(config)[0].dpi, "nodpi")

    def test_omitted_dpi_prefers_neutral_automatically(self):
        self.assertIsNone(self.load(BASE_CONFIG)[0].dpi)

    def test_invalid_dpi_is_rejected(self):
        with self.assertRaises(ConfigError):
            self.load(BASE_CONFIG + "    dpi: huge\n")

    def test_duplicate_architectures_are_removed(self):
        config = BASE_CONFIG + '    architectures: ["arm64-v8a", "arm64-v8a"]\n'
        self.assertEqual(self.load(config)[0].architectures, ["arm64-v8a"])

    def test_patches_can_list_multiple_sources(self):
        extra = """    patches:
      - repo: "owner/test-patches"
        asset_regex: '^patches.*\\.mpp$'
      - repo: "other/second-patches"
        asset_regex: '^patches.*\\.rvp$'
"""
        config = BASE_CONFIG.rsplit("    patches:", 1)[0] + extra
        app = self.load(config)[0]
        self.assertEqual(app.patches.repo, "owner/test-patches")
        self.assertEqual([s.repo for s in app.extra_patches], ["other/second-patches"])
        self.assertEqual(
            [s.repo for s in app.patch_sources],
            ["owner/test-patches", "other/second-patches"],
        )

    def test_single_patches_mapping_has_no_extra_sources(self):
        self.assertEqual(self.load(BASE_CONFIG)[0].extra_patches, [])


if __name__ == "__main__":
    unittest.main()
