import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_web.job_store import PersistentJobStore


class PersistentJobStoreTests(unittest.TestCase):
    def test_completed_job_survives_store_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            completed = threading.Event()

            def work(_job_id):
                completed.set()
                return {"ok": True}

            first = PersistentJobStore(storage)
            job_id = first.create("script", work)
            self.assertTrue(completed.wait(2))
            for _ in range(100):
                if first.get(job_id)["status"] == "complete":
                    break
                threading.Event().wait(0.01)

            second = PersistentJobStore(storage)
            self.assertEqual(second.get(job_id)["status"], "complete")
            self.assertEqual(second.get(job_id)["result"], {"ok": True})

    def test_running_job_is_marked_interrupted_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            job_id = "interrupted-job"
            (storage / f"{job_id}.json").write_text(json.dumps({
                "id": job_id,
                "kind": "script",
                "status": "running",
                "progress": 42,
            }), encoding="utf-8")

            store = PersistentJobStore(storage)
            job = store.get(job_id)
            self.assertEqual(job["status"], "failed")
            self.assertIn("Kaldığı yerden", job["error"])

    def test_active_jobs_keep_workspace_context_for_refresh_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentJobStore(Path(tmp))
            release = threading.Event()

            def work(_job_id):
                release.wait(2)
                return {"ok": True}

            job_id = store.create(
                "video",
                work,
                context={"projectId": "course-1", "videoId": "video-1"},
            )
            for _ in range(100):
                if store.get(job_id)["status"] == "running":
                    break
                threading.Event().wait(0.01)

            active = store.list_active("video")
            self.assertEqual([job["id"] for job in active], [job_id])
            self.assertEqual(active[0]["context"], {"projectId": "course-1", "videoId": "video-1"})
            self.assertEqual(store.list_active("script"), [])
            release.set()
            for _ in range(100):
                if store.get(job_id)["status"] == "complete":
                    break
                threading.Event().wait(0.01)
            self.assertEqual(store.get(job_id)["status"], "complete")

    def test_cancel_invokes_callback_and_finishes_as_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentJobStore(Path(tmp))
            callback_called = threading.Event()
            release_work = threading.Event()

            def work(job_id):
                store.set_cancel_callback(job_id, lambda: (callback_called.set(), release_work.set()))
                release_work.wait(2)
                raise RuntimeError("render process stopped")

            job_id = store.create("video", work)
            for _ in range(100):
                if store.get(job_id)["status"] == "running":
                    break
                threading.Event().wait(0.01)

            cancelling = store.cancel(job_id)
            self.assertIn(cancelling["status"], {"cancelling", "cancelled"})
            self.assertTrue(callback_called.wait(1))
            for _ in range(100):
                if store.get(job_id)["status"] == "cancelled":
                    break
                threading.Event().wait(0.01)
            self.assertEqual(store.get(job_id)["status"], "cancelled")
            self.assertIsNone(store.get(job_id)["error"])

    def test_old_jobs_are_pruned_beyond_retention_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            store = PersistentJobStore(storage)

            with patch("studio_web.job_store.JOB_RETENTION", 3):
                job_ids = []
                for i in range(5):
                    job_id = store.create("script", lambda _job_id, i=i: {"n": i})
                    job_ids.append(job_id)
                    for _ in range(100):
                        if store.get(job_id)["status"] in {"complete", "failed"}:
                            break
                        threading.Event().wait(0.01)

                self.assertEqual(len(list(storage.glob("*.json"))), 3)
                # en yeni 3 iş hâlâ erişilebilir olmalı, en eski 2'si silinmiş olmalı
                for job_id in job_ids[-3:]:
                    store.get(job_id)
                for job_id in job_ids[:2]:
                    with self.assertRaises(KeyError):
                        store.get(job_id)


class OrphanedWorkerCleanupTests(unittest.TestCase):
    """API çökünce (Ctrl+C, taskkill, çöküş) canlı bir render worker sürecinin de kapatılması —
    bkz. studio_web.job_store._kill_orphaned_worker. Windows'ta gerçek bir process ile sınandı
    (Docker/POSIX doğrulaması ayrıca app/tts/remote.py testlerinde ve manuel işlemde yapıldı)."""

    @staticmethod
    def _spawn(marker: str) -> subprocess.Popen:
        # Trailing argümanlar, gerçek bir process'in komut satırında görünür; render_worker'ı
        # gerçekten çalıştırmadan "marker içeren canlı bir süreç" simüle etmenin en basit yolu budur.
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", marker],
            start_new_session=(os.name != "nt"),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def _wait_exit(self, proc: subprocess.Popen, timeout: float = 10) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.1)
        return False

    def test_stale_job_kills_the_matching_orphaned_process(self):
        proc = self._spawn("kavra-test-marker-xyz")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                storage = Path(tmp)
                (storage / "orphan-job.json").write_text(json.dumps({
                    "id": "orphan-job", "kind": "video", "status": "running", "progress": 40,
                    "workerPid": proc.pid, "workerMarker": "kavra-test-marker-xyz",
                }), encoding="utf-8")
                store = PersistentJobStore(storage)
                self.assertEqual(store.get("orphan-job")["status"], "failed")
            self.assertTrue(self._wait_exit(proc), "yetim süreç PersistentJobStore yeniden açılışında kapatılmadı")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)

    def test_mismatched_marker_never_touches_the_live_process(self):
        """PID yeniden başlatma sonrası başka bir sürece ait olabilir; marker eşleşmezse dokunulmaz."""
        proc = self._spawn("kavra-test-marker-gercek")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                storage = Path(tmp)
                (storage / "orphan-job.json").write_text(json.dumps({
                    "id": "orphan-job", "kind": "video", "status": "running",
                    "workerPid": proc.pid, "workerMarker": "baska-bir-marker-eslesmiyor",
                }), encoding="utf-8")
                PersistentJobStore(storage)
            time.sleep(1)
            self.assertIsNone(proc.poll(), "eşleşmeyen marker'a rağmen süreç kapatıldı")
        finally:
            proc.kill()
            proc.wait(5)

    def test_missing_worker_fields_are_ignored_without_error(self):
        """Eski (workerPid/workerMarker'sız) job kayıtları hâlâ sorunsuz yüklenmeli."""
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            (storage / "old-job.json").write_text(json.dumps({
                "id": "old-job", "kind": "script", "status": "running",
            }), encoding="utf-8")
            store = PersistentJobStore(storage)
            self.assertEqual(store.get("old-job")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
