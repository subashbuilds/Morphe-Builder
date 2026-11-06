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


if __name__ == "__main__":
    unittest.main()
