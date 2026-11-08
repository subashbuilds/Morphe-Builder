import unittest
from unittest.mock import Mock, patch

import apkmirror


class VariantSelectionTests(unittest.TestCase):
    def variant(self, arch, dpi="nodpi", *, bundle=False, name="", splits=0, size=0):
        return apkmirror.Variant(
            is_bundle=bundle,
            link=f"https://example.test/{name or arch}-{dpi}",
            architecture=arch,
            dpi=dpi,
            split_count=splits,
            file_size=size,
        )

    def test_exact_numeric_dpi_is_preferred(self):
        variants = [
            self.variant("arm64-v8a", "320dpi"),
            self.variant("arm64-v8a", "480dpi"),
            self.variant("arm64-v8a", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "480dpi")

    def test_density_range_containing_requested_dpi_matches(self):
        variants = [
            self.variant("arm64-v8a", "320dpi"),
            self.variant("arm64-v8a", "120-480dpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "120-480dpi")

    def test_smallest_file_size_wins_over_exact_dpi_and_more_splits(self):
        # The reported case: a 278 MB exact "480dpi, 10 S" bundle must lose
        # to a 140 MB "320-480dpi, 1 S" bundle for dpi: 480.
        variants = [
            self.variant("arm64-v8a", "480dpi", splits=10, size=278 * 1024**2, name="big"),
            self.variant("arm64-v8a", "320-480dpi", splits=1, size=140 * 1024**2, name="small"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "320-480dpi")
        self.assertEqual(selected.file_size, 140 * 1024**2)
        self.assertEqual(selected.split_count, 1)

    def test_smallest_file_size_wins_between_equal_arch_and_dpi(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", size=278 * 1024**2, name="big"),
            self.variant("arm64-v8a", "480dpi", size=140 * 1024**2, name="small"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.link, "https://example.test/small-480dpi")

    def test_stated_size_beats_unstated_size(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", size=0, name="unknown"),
            self.variant("arm64-v8a", "480dpi", size=140 * 1024**2, name="known"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.link, "https://example.test/known-480dpi")

    def test_fewer_splits_wins_between_equal_arch_and_dpi(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", splits=10, name="big"),
            self.variant("arm64-v8a", "480dpi", splits=2, name="small"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.link, "https://example.test/small-480dpi")

    def test_fewer_splits_wins_over_exact_dpi(self):
        # The reported bug: the 10-split exact 480dpi upload was chosen even
        # though a 2-split range upload (480-640dpi / 360-480dpi) is smaller.
        variants = [
            self.variant("arm64-v8a", "480dpi", splits=10, name="exact"),
            self.variant("arm64-v8a", "480-640dpi", splits=2, name="range"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "480-640dpi")
        self.assertEqual(selected.split_count, 2)

    def test_smallest_split_variant_wins_within_a_density_range(self):
        variants = [
            self.variant("arm64-v8a", "480-640dpi", splits=10, name="ten"),
            self.variant("arm64-v8a", "480-640dpi", splits=2, name="two"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.split_count, 2)

    def test_equal_splits_prefer_exact_dpi_over_range(self):
        variants = [
            self.variant("arm64-v8a", "480-640dpi", splits=2, name="range"),
            self.variant("arm64-v8a", "480dpi", splits=2, name="exact"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "480dpi")

    def test_density_matching_rows_beat_neutral_dpi_even_with_more_splits(self):
        variants = [
            self.variant("arm64-v8a", "nodpi", splits=1, name="neutral"),
            self.variant("arm64-v8a", "360-480dpi", splits=6, name="density"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "360-480dpi")

    def test_unknown_split_count_still_beats_many_splits(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", splits=10, name="many"),
            self.variant("arm64-v8a", "480dpi", splits=0, name="unknown"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.link, "https://example.test/unknown-480dpi")

    def test_unavailable_dpi_falls_back_to_neutral_dpi(self):
        variants = [
            self.variant("arm64-v8a", "640dpi"),
            self.variant("arm64-v8a", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "nodpi")

    def test_missing_dpi_prefers_anydpi_or_nodpi(self):
        variants = [
            self.variant("arm64-v8a", "640dpi"),
            self.variant("arm64-v8a", "anydpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a")
        self.assertEqual(selected.dpi, "anydpi")

    def test_unavailable_exact_arch_dpi_prefers_universal_neutral_dpi(self):
        variants = [
            self.variant("arm64-v8a", "320dpi"),
            self.variant("universal", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.architecture, "universal")
        self.assertEqual(selected.dpi, "nodpi")

    def test_missing_architecture_falls_back_to_universal(self):
        variants = [
            self.variant("x86_64", "nodpi"),
            self.variant("universal", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "arm64-v8a")
        self.assertEqual(selected.architecture, "universal")

    def test_unavailable_architecture_and_universal_raise(self):
        variants = [self.variant("x86", "nodpi"), self.variant("x86_64", "nodpi")]
        with self.assertRaises(apkmirror.FailedToFindElement):
            apkmirror.select_variant(variants, "arm64-v8a")

    def test_apk_build_prefers_plain_exact_apk(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", bundle=True),
            self.variant("arm64-v8a", "480dpi", bundle=False),
        ]
        selected = apkmirror.select_variant(
            variants, "arm64-v8a", "480", prefer_bundle=False
        )
        self.assertFalse(selected.is_bundle)

    def test_module_build_prefers_bundle(self):
        variants = [
            self.variant("arm64-v8a", "480dpi", bundle=False),
            self.variant("arm64-v8a", "480dpi", bundle=True),
        ]
        selected = apkmirror.select_variant(
            variants, "arm64-v8a", "480", prefer_bundle=True
        )
        self.assertTrue(selected.is_bundle)

    def test_module_build_rejects_plain_apk_only_release(self):
        variants = [self.variant("arm64-v8a", "480dpi", bundle=False)]
        with self.assertRaises(apkmirror.FailedToFindElement):
            apkmirror.select_variant(
                variants, "arm64-v8a", "480", prefer_bundle=True, bundle_only=True
            )

    def test_x86_64_architecture_is_normalized_without_collapsing_to_x86(self):
        variants = [
            self.variant("x86_64", "nodpi"),
            self.variant("x86", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "x86_64")
        self.assertEqual(selected.architecture, "x86_64")

    def test_x86_does_not_match_x86_64(self):
        variants = [
            self.variant("x86_64", "nodpi"),
            self.variant("x86", "nodpi"),
        ]
        selected = apkmirror.select_variant(variants, "x86")
        self.assertEqual(selected.architecture, "x86")


class VariantParsingTests(unittest.TestCase):
    def test_release_table_architecture_and_dpi_are_parsed(self):
        html = """
        <div class="table">
          <div class="table-row"><div class="table-cell">Variant</div></div>
          <div class="table-row">
            <div class="table-cell"><span class="apkm-badge">BUNDLE</span> APKM</div>
            <div class="table-cell">arm64-v8a</div>
            <div class="table-cell">Android 12L+</div>
            <div class="table-cell">120-480 dpi</div>
            <a class="accent_color" href="/download.apkm">Download</a>
          </div>
        </div>
        """
        response = Mock(status_code=200, content=html.encode())
        with patch("apkmirror.flaresolverr_request", return_value=response):
            variants = apkmirror.get_variants("https://example.test/release", session=None)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].architecture, "arm64-v8a")
        self.assertEqual(variants[0].dpi, "120-480dpi")
        self.assertTrue(variants[0].is_bundle)
        self.assertEqual(variants[0].split_count, 0)
        self.assertEqual(variants[0].file_size, 0)

    def test_file_size_is_parsed_when_present(self):
        html = """
        <div class="table">
          <div class="table-row"><div class="table-cell">Variant</div></div>
          <div class="table-row">
            <div class="table-cell"><span class="apkm-badge">BUNDLE</span> APKM</div>
            <div class="table-cell">arm64-v8a</div>
            <div class="table-cell">Android 9.0+</div>
            <div class="table-cell">320-480 dpi</div>
            <div class="table-cell">140 MB</div>
            <div class="table-cell">1 split</div>
            <a class="accent_color" href="/download.apkm">Download</a>
          </div>
        </div>
        """
        response = Mock(status_code=200, content=html.encode())
        with patch("apkmirror.flaresolverr_request", return_value=response):
            variants = apkmirror.get_variants("https://example.test/release", session=None)

        self.assertEqual(variants[0].file_size, 140 * 1024**2)
        self.assertEqual(variants[0].split_count, 1)

    def test_file_size_is_read_from_the_download_link_text(self):
        # Regression: APKMirror puts the size in the download link text
        # ("Download (278 MB)"), not in a table cell. Reading only the
        # cells left every row at size 0, so the 278 MB exact-480dpi row
        # kept winning over the 140 MB 320-480dpi one.
        html = """
        <div class="table">
          <div class="table-row"><div class="table-cell">Variant</div></div>
          <div class="table-row">
            <div class="table-cell"><span class="apkm-badge">BUNDLE</span> APKM</div>
            <div class="table-cell">arm64-v8a</div>
            <div class="table-cell">Android 9.0+</div>
            <div class="table-cell">480 dpi</div>
            <a class="accent_color" href="/big.apkm">Download (278 MB)</a>
          </div>
          <div class="table-row">
            <div class="table-cell"><span class="apkm-badge">BUNDLE</span> APKM</div>
            <div class="table-cell">arm64-v8a</div>
            <div class="table-cell">Android 9.0+</div>
            <div class="table-cell">320-480 dpi</div>
            <a class="accent_color" href="/small.apkm">Download (140 MB)</a>
          </div>
        </div>
        """
        response = Mock(status_code=200, content=html.encode())
        with patch("apkmirror.flaresolverr_request", return_value=response):
            variants = apkmirror.get_variants("https://example.test/release", session=None)

        self.assertEqual(variants[0].file_size, 278 * 1024**2)
        self.assertEqual(variants[1].file_size, 140 * 1024**2)
        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertEqual(selected.dpi, "320-480dpi")
        self.assertEqual(selected.file_size, 140 * 1024**2)

    def test_split_count_is_parsed_when_present(self):
        html = """
        <div class="table">
          <div class="table-row"><div class="table-cell">Variant</div></div>
          <div class="table-row">
            <div class="table-cell">APK</div>
            <div class="table-cell">arm64-v8a</div>
            <div class="table-cell">Android 12L+</div>
            <div class="table-cell">480 dpi</div>
            <div class="table-cell">2 splits</div>
            <a class="accent_color" href="/download.apk">Download</a>
          </div>
        </div>
        """
        response = Mock(status_code=200, content=html.encode())
        with patch("apkmirror.flaresolverr_request", return_value=response):
            variants = apkmirror.get_variants("https://example.test/release", session=None)

        self.assertEqual(variants[0].split_count, 2)


class VariantSizeProbeTests(unittest.TestCase):
    def variant(self, dpi, name, *, bundle=True, arch="arm64-v8a"):
        return apkmirror.Variant(
            is_bundle=bundle,
            link=f"https://example.test/{name}",
            architecture=arch,
            dpi=dpi,
        )

    @staticmethod
    def page(body):
        return f"<html><body>{body}</body></html>".encode()

    def fake_requests(self, pages):
        def fake(url, session=None, **kwargs):
            return Mock(status_code=200, content=pages[url])

        return fake

    def test_size_is_read_from_exact_byte_count(self):
        soup = apkmirror.BeautifulSoup(
            self.page('<div>File size: 278.2 MB (291,737,613 bytes)</div>'),
            "html.parser",
        )
        self.assertEqual(apkmirror.parse_variant_page_size(soup), 291737613)

    def test_size_is_read_from_file_size_row_then_button(self):
        row = apkmirror.BeautifulSoup(
            self.page("<div>File size: 140 MB</div>"), "html.parser"
        )
        self.assertEqual(apkmirror.parse_variant_page_size(row), 140 * 1024**2)
        button = apkmirror.BeautifulSoup(
            self.page('<a class="downloadButton">Download APK Bundle (66.8 MB)</a>'),
            "html.parser",
        )
        self.assertEqual(
            apkmirror.parse_variant_page_size(button), int(66.8 * 1024**2)
        )

    def test_missing_size_is_zero(self):
        soup = apkmirror.BeautifulSoup(self.page("<p>nothing</p>"), "html.parser")
        self.assertEqual(apkmirror.parse_variant_page_size(soup), 0)

    def test_probed_sizes_make_the_smaller_in_range_variant_win(self):
        # Regression: the release page states no sizes, so selection fell
        # back to "exact DPI wins" and always downloaded the 278 MB row.
        exact = self.variant("480dpi", "exact")
        small = self.variant("320-480dpi", "small")
        wide = self.variant("480-640dpi", "wide")
        neutral = self.variant("nodpi", "neutral")
        pages = {
            exact.link: self.page("File size: 278.2 MB (291,737,613 bytes)"),
            small.link: self.page("File size: 140 MB (146,800,640 bytes)"),
            wide.link: self.page("File size: 200 MB (209,715,200 bytes)"),
            neutral.link: self.page("File size: 10 MB (10,485,760 bytes)"),
        }
        variants = [exact, small, wide, neutral]
        fake = self.fake_requests(pages)
        with patch("apkmirror.flaresolverr_request", side_effect=fake) as request:
            apkmirror.populate_variant_sizes(
                variants, "arm64-v8a", "480", session=None
            )
            # The neutral row can never beat a density match: not fetched.
            fetched = [c.args[0] for c in request.call_args_list]
            self.assertNotIn(neutral.link, fetched)

        selected = apkmirror.select_variant(variants, "arm64-v8a", "480")
        self.assertIs(selected, small)

    def test_known_sizes_are_not_fetched_again(self):
        known = self.variant("480dpi", "known")
        known.file_size = 100
        with patch("apkmirror.flaresolverr_request") as request:
            apkmirror.populate_variant_sizes(
                [known], "arm64-v8a", "480", session=None
            )
        request.assert_not_called()

    def test_failed_probe_keeps_exact_dpi_fallback(self):
        exact = self.variant("480dpi", "exact")
        wide = self.variant("480-640dpi", "wide")
        with patch("apkmirror.flaresolverr_request", side_effect=RuntimeError("boom")):
            apkmirror.populate_variant_sizes(
                [exact, wide], "arm64-v8a", "480", session=None
            )
        selected = apkmirror.select_variant([exact, wide], "arm64-v8a", "480")
        self.assertIs(selected, exact)


class PrereleaseVariantTests(unittest.TestCase):
    ROW = """
      <div class="table-row">
        <div class="table-cell"><a class="accent_color" href="{href}">{label}</a></div>
        <div class="table-cell">arm64-v8a</div>
        <div class="table-cell">Android 9.0+</div>
        <div class="table-cell">{dpi}</div>
      </div>"""

    def parse(self, *rows):
        html = (
            '<div class="table"><div class="table-row"><div class="table-cell">'
            "Variant</div></div>" + "".join(rows) + "</div>"
        )
        response = Mock(status_code=200, content=html.encode())
        with patch("apkmirror.flaresolverr_request", return_value=response):
            return apkmirror.get_variants("https://example.test/release", session=None)

    def test_beta_row_is_flagged_and_release_row_is_not(self):
        variants = self.parse(
            self.ROW.format(
                href="/gboard-18-5-4-993608717-release-arm64-v8a-android-apk-download/",
                label="18.5.4.993608717-release-arm64-v8a",
                dpi="nodpi",
            ),
            self.ROW.format(
                href="/gboard-18-5-4-993608717-beta-arm64-v8a-android-apk-download/",
                label="18.5.4.993608717-beta-arm64-v8a",
                dpi="nodpi",
            ),
        )
        self.assertEqual([v.prerelease for v in variants], [False, True])
        self.assertEqual(variants[0].name, "18.5.4.993608717-release-arm64-v8a")

    def test_beta_flag_is_found_in_url_slug_when_label_is_plain(self):
        variants = self.parse(
            self.ROW.format(
                href="/gboard-18-5-4-beta-arm64-v8a-android-apk-download/",
                label="18.5.4.993608717",
                dpi="nodpi",
            )
        )
        self.assertTrue(variants[0].prerelease)

    def test_select_never_picks_a_smaller_beta_over_the_release(self):
        # Regression: Gboard lists release and beta on the same page; the
        # beta was the smaller/equal row and got downloaded.
        release = apkmirror.Variant(
            is_bundle=False, link="https://example.test/release",
            architecture="arm64-v8a", dpi="nodpi", file_size=120 * 1024**2,
            name="18.5.4.993608717-release-arm64-v8a",
        )
        beta = apkmirror.Variant(
            is_bundle=False, link="https://example.test/beta",
            architecture="arm64-v8a", dpi="nodpi", file_size=100 * 1024**2,
            name="18.5.4.993608717-beta-arm64-v8a", prerelease=True,
        )
        for order in ([release, beta], [beta, release]):
            selected = apkmirror.select_variant(order, "arm64-v8a", "480")
            self.assertIs(selected, release)

    def test_beta_is_used_only_when_nothing_else_exists(self):
        beta = apkmirror.Variant(
            is_bundle=False, link="https://example.test/beta",
            architecture="arm64-v8a", dpi="nodpi", prerelease=True,
        )
        self.assertIs(apkmirror.select_variant([beta], "arm64-v8a", "480"), beta)

    def test_beta_pages_are_not_probed_for_size(self):
        release = apkmirror.Variant(
            is_bundle=False, link="https://example.test/release",
            architecture="arm64-v8a", dpi="nodpi",
        )
        beta = apkmirror.Variant(
            is_bundle=False, link="https://example.test/beta",
            architecture="arm64-v8a", dpi="nodpi", prerelease=True,
        )
        page = Mock(status_code=200, content=b"File size: 5 MB (5,242,880 bytes)")
        with patch("apkmirror.flaresolverr_request", return_value=page) as request:
            apkmirror.populate_variant_sizes(
                [release, beta], "arm64-v8a", "480", session=None
            )
        self.assertEqual([c.args[0] for c in request.call_args_list], [release.link])


class ChannelSelectionTests(unittest.TestCase):
    def row(self, channel, size, arch="arm64-v8a"):
        return apkmirror.Variant(
            is_bundle=False,
            # Every row lives under the release page's own "...-release/"
            # path, so "release" is in the URL of beta rows too.
            link=(
                "https://example.test/apk/google-inc/gboard/"
                "gboard-the-google-keyboard-18-5-4-993608717-release/"
                f"gboard-the-google-keyboard-18-5-4-993608717-{channel}-{arch}"
                "-android-apk-download/"
            ),
            architecture=arch,
            dpi="nodpi",
            file_size=size,
            name=f"18.5.4.993608717-{channel}-{arch}",
            prerelease=channel != "release",
        )

    def test_version_channel_is_read_from_the_full_version_name(self):
        self.assertEqual(
            apkmirror.version_channel("18.5.4.993608717-release-arm64-v8a"), "release"
        )
        self.assertEqual(
            apkmirror.version_channel("18.5.4.993608717-beta-arm64-v8a"), "beta"
        )
        self.assertEqual(apkmirror.version_channel("18.5.4-release"), "release")
        self.assertIsNone(apkmirror.version_channel("21.04.223"))
        self.assertIsNone(apkmirror.version_channel("12.7.1-release.0"))

    def test_release_version_picks_release_even_when_beta_is_smaller(self):
        release = self.row("release", 120 * 1024**2)
        beta = self.row("beta", 100 * 1024**2)
        selected = apkmirror.select_variant(
            [beta, release], "arm64-v8a", "480", channel="release"
        )
        self.assertIs(selected, release)

    def test_release_page_path_does_not_make_beta_rows_look_like_release(self):
        # Regression: matching the whole URL made every row (beta included)
        # look like a "release" row, so nothing was filtered and the smaller
        # beta bundle won.
        release = self.row("release", 120 * 1024**2)
        beta = self.row("beta", 42 * 1024**2)
        self.assertTrue(apkmirror._has_channel(release, "release"))
        self.assertFalse(apkmirror._has_channel(beta, "release"))
        self.assertTrue(beta.link.count("-release/") == 1)

    def test_beta_version_picks_beta(self):
        release = self.row("release", 100 * 1024**2)
        beta = self.row("beta", 120 * 1024**2)
        selected = apkmirror.select_variant(
            [release, beta], "arm64-v8a", "480", channel="beta"
        )
        self.assertIs(selected, beta)

    def test_unlabelled_rows_fall_back_to_stable_selection(self):
        plain = apkmirror.Variant(
            is_bundle=False, link="https://example.test/plain",
            architecture="arm64-v8a", dpi="nodpi", name="18.5.4.993608717",
        )
        selected = apkmirror.select_variant(
            [plain], "arm64-v8a", "480", channel="release"
        )
        self.assertIs(selected, plain)

    def test_only_the_targeted_channel_is_probed_for_size(self):
        release = self.row("release", 0)
        beta = self.row("beta", 0)
        page = Mock(status_code=200, content=b"File size: 5 MB (5,242,880 bytes)")
        with patch("apkmirror.flaresolverr_request", return_value=page) as request:
            apkmirror.populate_variant_sizes(
                [release, beta], "arm64-v8a", "480", session=None, channel="release"
            )
        self.assertEqual([c.args[0] for c in request.call_args_list], [release.link])


if __name__ == "__main__":
    unittest.main()
