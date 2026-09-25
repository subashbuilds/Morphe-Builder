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


if __name__ == "__main__":
    unittest.main()
