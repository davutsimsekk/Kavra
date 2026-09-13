import tempfile
import time
import unittest
from pathlib import Path

from app.models import Slide
from app.pipeline import list_snapshots, restore_snapshot, save_script, snapshot_script


class SnapshotLifecycleTests(unittest.TestCase):
    def test_list_is_empty_when_nothing_was_ever_snapshotted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_snapshots(Path(tmp)), [])

    def test_snapshot_then_list_reports_reason_and_slide_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            save_script(pdir, [Slide(title="a"), Slide(title="b")])
            snapshot_script(pdir, reason="before-regenerate")

            snapshots = list_snapshots(pdir)

            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["reason"], "before-regenerate")
            self.assertEqual(snapshots[0]["slideCount"], 2)

    def test_restore_brings_back_old_content_and_snapshots_the_current_state_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            save_script(pdir, [Slide(title="orijinal")])
            snapshot_script(pdir, reason="before-regenerate")
            save_script(pdir, [Slide(title="kötü sonuç")])  # regenerate bunu yazmış olsun

            restored = restore_snapshot(pdir, list_snapshots(pdir)[0]["filename"])

            self.assertEqual(restored[0].title, "orijinal")
            # geri yükleme öncesi durum da (kötü sonuç) kendiliğinden saklanmalı
            reasons = [s["reason"] for s in list_snapshots(pdir)]
            self.assertIn("before-restore", reasons)

    def test_restore_rejects_path_traversal_in_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            save_script(pdir, [Slide(title="a")])
            with self.assertRaises(ValueError):
                restore_snapshot(pdir, "../../etc/passwd")

    def test_only_the_most_recent_ten_snapshots_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            save_script(pdir, [Slide(title="a")])
            for i in range(12):
                snapshot_script(pdir, reason=f"tur-{i}")
                time.sleep(0.01)  # farklı dosya adları için zaman damgasını ilerlet
            self.assertEqual(len(list_snapshots(pdir)), 10)


if __name__ == "__main__":
    unittest.main()
