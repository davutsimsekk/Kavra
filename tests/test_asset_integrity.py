import tempfile
import unittest
from pathlib import Path

from app.asset_integrity import audit_assets, quarantine_orphan_assets, write_asset_manifest


class AssetIntegrityTests(unittest.TestCase):
    def test_stale_numbered_assets_are_quarantined_without_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            (assets / "slide_ single1.mp3").write_bytes(b"ignored")
            (assets / "slide_001.mp3").write_bytes(b"active")
            (assets / "slide_003.mp3").write_bytes(b"stale")
            (assets / "segment_003.mp4").write_bytes(b"stale")

            moved = quarantine_orphan_assets(pdir, 1)

            self.assertEqual(moved, ["segment_003.mp4", "slide_003.mp3"])
            self.assertTrue((assets / "slide_001.mp3").exists())
            self.assertFalse((assets / "slide_003.mp3").exists())
            self.assertEqual(len(list((assets / "_orphaned").rglob("slide_003.mp3"))), 1)

    def test_manifest_reports_only_canonical_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            (assets / "slide_001.mp3").write_bytes(b"one")
            (assets / "slide_002.mp3").write_bytes(b"two")

            manifest = write_asset_manifest(pdir, 2)
            audit = audit_assets(pdir, 2)

            self.assertEqual(manifest["activeAudio"], ["slide_001.mp3", "slide_002.mp3"])
            self.assertEqual(audit["canonicalAudioCount"], 2)
            self.assertEqual(audit["orphanCount"], 0)

    def test_audit_reports_ready_and_missing_segments_separately_from_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            assets = pdir / "assets"
            assets.mkdir()
            # 3 slaytın hepsinde ses hazır ama sadece ilk ikisinde video segmenti var
            # (ör. render 3. slaytta kesildi) — sağlık kartı bu ikisini ayrı göstermeli.
            (assets / "slide_001.mp3").write_bytes(b"one")
            (assets / "slide_002.mp3").write_bytes(b"two")
            (assets / "slide_003.mp3").write_bytes(b"three")
            (assets / "segment_001.mp4").write_bytes(b"one")
            (assets / "segment_002.mp4").write_bytes(b"two")

            audit = audit_assets(pdir, 3)

            self.assertEqual(audit["canonicalAudioCount"], 3)
            self.assertEqual(audit["missingAudioCount"], 0)
            self.assertEqual(audit["canonicalSegmentCount"], 2)
            self.assertEqual(audit["missingSegmentCount"], 1)


if __name__ == "__main__":
    unittest.main()
