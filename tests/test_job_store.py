import json
import tempfile
import threading
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


if __name__ == "__main__":
    unittest.main()
