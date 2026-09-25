import unittest
from unittest.mock import Mock, patch

import apkmirror


class VariantSelectionTests(unittest.TestCase):
    def variant(self, arch, dpi="nodpi", *, bundle=False, name=""):
        return apkmirror.Variant(
            is_bundle=bundle,
            link=f"https://example.test/{name or arch}-{dpi}",
            architecture=arch,
            dpi=dpi,
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


if __name__ == "__main__":
    unittest.main()
