import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from studio_web.batch_queue import BatchQueueStore


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class BatchQueueAddRemoveTests(unittest.TestCase):
    def test_add_persists_a_queued_item_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                item_id = store.add(
                    source_path="C:/does/not/matter.pdf", project_name="proje",
                    page_mode=False, vision_enrich=False, vision_api_key="",
                    llm_settings={}, video_settings={},
                )
            items = store.list()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], item_id)
            self.assertEqual(items[0]["status"], "queued")
            self.assertTrue((Path(tmp) / f"{item_id}.json").exists())

    def test_cannot_remove_an_actively_processing_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                item_id = store.add(
                    source_path="x.pdf", project_name="p", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )
            store._update(item_id, status="rendering")
            with self.assertRaises(ValueError):
                store.remove(item_id)

    def test_removing_unknown_item_raises_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
            with self.assertRaises(KeyError):
                store.remove("does-not-exist")

    def test_items_mid_processing_are_reset_to_queued_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                item_id = store.add(
                    source_path="x.pdf", project_name="p", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )
                store._update(item_id, status="generating", stage="Anlatı üretiliyor")

            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                reloaded = BatchQueueStore(Path(tmp))
            item = reloaded.list()[0]
            self.assertEqual(item["status"], "queued")
            self.assertIn("kesildi", item["stage"])


class BatchQueueProcessingTests(unittest.TestCase):
    def _fake_jobs(self, sequences: dict):
        """sequences: {job_id: [job_dict, job_dict, ...]} — her .get() çağrısında sıradakini döndürür."""
        calls = {job_id: iter(seq) for job_id, seq in sequences.items()}

        class FakeJobs:
            def get(self, job_id):
                try:
                    return next(calls[job_id])
                except StopIteration:
                    return sequences[job_id][-1]

        return FakeJobs()

    def test_full_happy_path_calls_parse_generate_render_in_order_and_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                item_id = store.add(
                    source_path="ders.pdf", project_name="ders", page_mode=False,
                    vision_enrich=False, vision_api_key="",
                    llm_settings={"provider": "agent"}, video_settings={"ttsProvider": "edge"},
                )

            fake_jobs = self._fake_jobs({
                "gen-job": [{"status": "running", "progress": 50, "message": "yarı yolda"},
                            {"status": "complete", "result": {}}],
                "render-job": [{"status": "complete", "result": {}}],
            })

            with patch("studio_web.api.parse_source_path") as fake_parse, \
                 patch("studio_web.api.generate_script") as fake_generate, \
                 patch("studio_web.api.start_render") as fake_render, \
                 patch("studio_web.api.jobs", fake_jobs), \
                 patch("studio_web.batch_queue._JOB_POLL_INTERVAL_SEC", 0.01):
                fake_parse.return_value = {"id": "ders", "sections": [{"title": "1"}, {"title": "2"}]}
                fake_generate.return_value = {"jobId": "gen-job"}
                fake_render.return_value = {"jobId": "render-job"}

                store._process(dict(store.list()[0]))

            fake_parse.assert_called_once_with({
                "path": "ders.pdf", "pageMode": False, "visionEnrich": False, "visionApiKey": "",
            })
            fake_generate.assert_called_once()
            gen_call_args = fake_generate.call_args[0]
            self.assertEqual(gen_call_args[0], "ders")
            self.assertEqual(gen_call_args[1]["sectionIndexes"], [0, 1])
            self.assertEqual(gen_call_args[1]["provider"], "agent")
            fake_render.assert_called_once_with("ders", {"ttsProvider": "edge"})

            final = store.list()[0]
            self.assertEqual(final["status"], "complete")
            self.assertEqual(final["projectId"], "ders")
            self.assertIsNone(final["error"])

    def test_generate_stage_failure_marks_item_failed_without_calling_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                store.add(
                    source_path="ders.pdf", project_name="ders", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )

            fake_jobs = self._fake_jobs({
                "gen-job": [{"status": "failed", "error": "LLM patladı"}],
            })

            with patch("studio_web.api.parse_source_path") as fake_parse, \
                 patch("studio_web.api.generate_script") as fake_generate, \
                 patch("studio_web.api.start_render") as fake_render, \
                 patch("studio_web.api.jobs", fake_jobs), \
                 patch("studio_web.batch_queue._JOB_POLL_INTERVAL_SEC", 0.01):
                fake_parse.return_value = {"id": "ders", "sections": [{"title": "1"}]}
                fake_generate.return_value = {"jobId": "gen-job"}

                store._process(dict(store.list()[0]))

            fake_render.assert_not_called()
            final = store.list()[0]
            self.assertEqual(final["status"], "failed")
            self.assertIn("LLM patladı", final["error"])

    def test_parse_stage_http_exception_is_recorded_as_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                store.add(
                    source_path="bozuk.pdf", project_name="p", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )

            with patch("studio_web.api.parse_source_path") as fake_parse:
                fake_parse.side_effect = HTTPException(400, "Kaynak dosya bozuk.")
                store._process(dict(store.list()[0]))

            final = store.list()[0]
            self.assertEqual(final["status"], "failed")
            self.assertEqual(final["error"], "Kaynak dosya bozuk.")

    def test_one_item_failing_does_not_stop_the_next_item_from_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("studio_web.batch_queue.BatchQueueStore._ensure_worker"):
                store = BatchQueueStore(Path(tmp))
                store.add(
                    source_path="bozuk.pdf", project_name="bozuk", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )
                store.add(
                    source_path="iyi.pdf", project_name="iyi", page_mode=False,
                    vision_enrich=False, vision_api_key="", llm_settings={}, video_settings={},
                )

            fake_jobs = self._fake_jobs({
                "gen-job-2": [{"status": "complete", "result": {}}],
                "render-job-2": [{"status": "complete", "result": {}}],
            })

            def fake_parse(payload):
                if "bozuk" in payload["path"]:
                    raise HTTPException(400, "Bozuk dosya.")
                return {"id": "iyi", "sections": [{"title": "1"}]}

            with patch("studio_web.api.parse_source_path", side_effect=fake_parse), \
                 patch("studio_web.api.generate_script", return_value={"jobId": "gen-job-2"}), \
                 patch("studio_web.api.start_render", return_value={"jobId": "render-job-2"}), \
                 patch("studio_web.api.jobs", fake_jobs), \
                 patch("studio_web.batch_queue._JOB_POLL_INTERVAL_SEC", 0.01):
                store._ensure_worker()
                self.assertTrue(_wait_until(
                    lambda: all(i["status"] in {"complete", "failed"} for i in store.list())
                ))

            by_project = {i["projectName"]: i for i in store.list()}
            self.assertEqual(by_project["bozuk"]["status"], "failed")
            self.assertEqual(by_project["iyi"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
