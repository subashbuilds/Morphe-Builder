import os
import unittest
from unittest.mock import patch

from github import Asset, GithubRelease
from utils import publish_release


class ReleasePublishingTests(unittest.TestCase):
    def test_existing_release_is_updated_in_place_and_stale_assets_removed(self):
        release = GithubRelease(
            tag_name="gboard-1.2.3",
            html_url="https://github.test/release",
            body="old",
            prerelease=False,
            assets=[
                Asset(1, "https://github.test/old.apk", "gboard-old.apk", 140_000_000),
                Asset(2, "https://github.test/same.apk", "gboard-new.apk", 50_000_000),
            ],
        )

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}),
            patch("utils.get_repo", return_value="owner/repo"),
            patch("utils.get_release_by_tag", return_value=release),
            patch("utils.delete_release_asset") as delete_asset,
            patch("utils.subprocess.run") as run,
        ):
            publish_release(
                tag="gboard-1.2.3",
                files=["/tmp/gboard-new.apk"],
                message="new notes",
                title="Gboard v1.2.3",
            )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:3], ["gh", "release", "edit"])
        self.assertEqual(commands[1], ["gh", "release", "upload", "gboard-1.2.3", "/tmp/gboard-new.apk", "--clobber"])
        self.assertFalse(any("delete" in command for command in commands))
        delete_asset.assert_called_once_with("owner/repo", 1)

    def test_new_release_uses_create(self):
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}),
            patch("utils.get_repo", return_value="owner/repo"),
            patch("utils.get_release_by_tag", return_value=None),
            patch("utils.subprocess.run") as run,
        ):
            publish_release(
                tag="gboard-1.2.3",
                files=["/tmp/gboard-new.apk"],
                message="notes",
                title="Gboard v1.2.3",
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "release", "create"])
        self.assertEqual(command[-1], "/tmp/gboard-new.apk")


if __name__ == "__main__":
    unittest.main()
