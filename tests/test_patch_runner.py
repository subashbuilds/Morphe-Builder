import tempfile
import unittest
from unittest.mock import patch

from config import AppConfig, SourceConfig
from patch_runner import build_all_outputs


class PatchRunnerTests(unittest.TestCase):
    def test_each_architecture_uses_its_selected_source(self):
        app = AppConfig(
            id="demo",
            name="Demo",
            package_name="com.example.demo",
            apkmirror_org="example",
            apkmirror_app="demo",
            apkmirror_release_prefix="demo",
            patches=SourceConfig("owner/patches", r"^patches.*\.mpp$"),
            cli=SourceConfig("owner/cli", r"^cli.*\.jar$"),
            build_mode="apk",
            architectures=["arm64-v8a", "universal"],
            dpi="480",
        )
        arm_source = "/tmp/arm64-480.apk"
        universal_source = "/tmp/universal-480.apk"

        with tempfile.TemporaryDirectory() as output_dir:
            with patch("patch_runner._run_patch_command") as patch_command:
                outputs = build_all_outputs(
                    app=app,
                    cli_jar="cli.jar",
                    patches_files=["patches.mpp"],
                    downloaded_apk_paths={
                        "arm64-v8a": arm_source,
                        "universal": universal_source,
                    },
                    version="1.2.3",
                    output_dir=output_dir,
                )

            self.assertEqual([output.architecture for output in outputs], ["arm64-v8a", "universal"])
            self.assertTrue(outputs[0].path.endswith("demo-v1.2.3-arm64-v8a-480dpi.apk"))
            self.assertTrue(outputs[1].path.endswith("demo-v1.2.3-universal-480dpi.apk"))
            self.assertEqual(patch_command.call_args_list[0].kwargs["apk_path"], arm_source)
            self.assertEqual(patch_command.call_args_list[1].kwargs["apk_path"], universal_source)


    def test_multiple_patch_sources_are_all_passed_to_one_run(self):
        app = AppConfig(
            id="demo",
            name="Demo",
            package_name="com.example.demo",
            apkmirror_org="example",
            apkmirror_app="demo",
            apkmirror_release_prefix="demo",
            patches=SourceConfig("owner/patches", r"^patches.*\.mpp$"),
            cli=SourceConfig("owner/cli", r"^cli.*\.jar$"),
            build_mode="apk",
            architectures=["arm64-v8a"],
            extra_patches=[SourceConfig("other/second", r"^patches.*\.rvp$")],
            include_patches=[["HushGram patch"], ["Extra patch", "HushGram patch"]],
            exclude_patches=["Ads"],
        )

        with tempfile.TemporaryDirectory() as output_dir:
            with patch("patch_runner._run_patch_command") as patch_command:
                build_all_outputs(
                    app=app,
                    cli_jar="cli.jar",
                    patches_files=["primary.mpp", "secondary.rvp"],
                    downloaded_apk_paths={"arm64-v8a": "/tmp/arm.apk"},
                    version="1.0.0",
                    output_dir=output_dir,
                )

            self.assertEqual(
                patch_command.call_args_list[0].kwargs["patches_files"],
                ["primary.mpp", "secondary.rvp"],
            )
            # Per-source include lists are unioned; flat excludes apply to both.
            self.assertEqual(
                patch_command.call_args_list[0].kwargs["include_patches"],
                ["HushGram patch", "Extra patch"],
            )
            self.assertEqual(
                patch_command.call_args_list[0].kwargs["exclude_patches"], ["Ads"]
            )


if __name__ == "__main__":
    unittest.main()
