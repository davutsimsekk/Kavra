import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models import Slide
from app.pipeline import save_script
import studio_web.api as api_module
from studio_web.api import _project_dir, _resolve_vision_api_key, app


@contextmanager
def _temp_project(slides: list[Slide], sections: list[dict] | None = None):
    """Create a throwaway project dir under a patched PROJECTS_DIR."""
    with tempfile.TemporaryDirectory() as tmp:
        projects_dir = Path(tmp)
        pdir = projects_dir / "test-proje"
        (pdir / "assets").mkdir(parents=True)
        (pdir / "raw_sections.json").write_text(
            json.dumps(sections or [], ensure_ascii=False), encoding="utf-8"
        )
        save_script(pdir, slides)
        with patch.object(api_module, "PROJECTS_DIR", projects_dir):
            yield pdir


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    def test_bootstrap_exposes_ui_catalogs_without_secret_values(self):
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(len(payload["themes"]), 7)
        self.assertIn("agent_command", payload["settings"])
        self.assertEqual(set(payload["keysConfigured"]), {"gemini", "openai", "elevenlabs"})
        self.assertNotIn("apiKey", response.text)

    def test_project_path_traversal_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            _project_dir("../outside")
        self.assertEqual(raised.exception.status_code, 400)

    def test_external_browser_origin_cannot_mutate_local_api(self):
        response = self.client.post(
            "/api/source/path",
            headers={"Origin": "https://example.invalid"},
            json={"path": "does-not-matter.pdf"},
        )
        self.assertEqual(response.status_code, 403)

    def test_pronunciation_get_reflects_built_in_and_override_entries(self):
        with patch("studio_web.api.load_overrides", return_value={"kernel": "körnıl"}):
            response = self.client.get("/api/pronunciation")
            self.assertEqual(response.status_code, 200)
            entries = {e["term"]: e for e in response.json()["entries"]}
            self.assertIn("switch", entries)  # yerleşik varsayılan
            self.assertFalse(entries["switch"]["isOverride"])
            self.assertEqual(entries["kernel"]["phonetic"], "körnıl")
            self.assertTrue(entries["kernel"]["isOverride"])

    def test_pronunciation_put_rejects_non_dict_payload(self):
        response = self.client.put(
            "/api/pronunciation", headers={"Origin": "http://127.0.0.1:5173"}, json={"overrides": "nope"}
        )
        self.assertEqual(response.status_code, 400)

    def test_pronunciation_put_saves_and_get_reflects_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            with patch("app.tts.pronunciation.OVERRIDES_PATH", path):
                put_response = self.client.put(
                    "/api/pronunciation", headers={"Origin": "http://127.0.0.1:5173"},
                    json={"overrides": {"kernel": "körnıl"}},
                )
                self.assertEqual(put_response.status_code, 200)
                entries = {e["term"]: e for e in put_response.json()["entries"]}
                self.assertEqual(entries["kernel"]["phonetic"], "körnıl")

    def test_pronunciation_preview_rejects_empty_text(self):
        response = self.client.post(
            "/api/pronunciation/preview", headers={"Origin": "http://127.0.0.1:5173"}, json={"text": "  "}
        )
        self.assertEqual(response.status_code, 400)

    def test_snapshot_restore_round_trip_via_api(self):
        with _temp_project([Slide(title="orijinal")]) as pdir:
            from app.pipeline import snapshot_script
            snapshot_script(pdir, reason="before-regenerate")
            save_script(pdir, [Slide(title="değişti")])

            snapshots = self.client.get(f"/api/projects/{pdir.name}/snapshots").json()["snapshots"]
            self.assertEqual(len(snapshots), 1)

            restore = self.client.post(
                f"/api/projects/{pdir.name}/snapshots/{snapshots[0]['filename']}/restore"
            )
            self.assertEqual(restore.status_code, 200)
            self.assertEqual(restore.json()["slides"][0]["title"], "orijinal")

    def test_restore_rejects_unknown_snapshot_filename(self):
        with _temp_project([Slide(title="a")]) as pdir:
            response = self.client.post(f"/api/projects/{pdir.name}/snapshots/does-not-exist.json/restore")
            self.assertEqual(response.status_code, 404)

    def test_chapters_endpoint_reports_missing_renders_before_export(self):
        slides = [
            Slide(title="Bölüm 1", level="chapter", narration=""),
            Slide(title="1.1", narration="x"),
        ]
        with _temp_project(slides) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/chapters")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(len(body["chapters"]), 1)
            self.assertFalse(body["allRendered"])
            self.assertEqual(body["missingCount"], 2)

    def test_chapters_export_job_fails_clearly_when_nothing_is_rendered(self):
        slides = [Slide(title="Bölüm 1", level="chapter", narration="x")]
        with _temp_project(slides) as pdir:
            response = self.client.post(f"/api/projects/{pdir.name}/chapters/export")
            self.assertEqual(response.status_code, 200)
            job_id = response.json()["jobId"]
            for _ in range(50):
                job = self.client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in {"complete", "failed"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "failed")
            self.assertIn("render edilmemiş", job["error"])

    def test_render_estimate_reflects_chosen_provider(self):
        slide = Slide(title="if / else", narration="kısa anlatım metni burada")
        with _temp_project([slide]) as pdir:
            local = self.client.get(f"/api/projects/{pdir.name}/render-estimate?provider=edge")
            cloud = self.client.get(f"/api/projects/{pdir.name}/render-estimate?provider=elevenlabs")
            self.assertEqual(local.status_code, 200)
            self.assertTrue(local.json()["providerIsLocal"])
            self.assertFalse(cloud.json()["providerIsLocal"])

    def test_export_returns_downloadable_file_for_each_known_kind(self):
        slide = Slide(title="if / else", narration="Kısa anlatım metni burada duruyor.")
        with _temp_project([slide]) as pdir:
            for kind in ("notes", "transcript", "anki", "quiz"):
                response = self.client.get(f"/api/projects/{pdir.name}/export/{kind}")
                self.assertEqual(response.status_code, 200, kind)
                self.assertGreater(len(response.content), 0, kind)

    def test_export_rejects_unknown_kind(self):
        with _temp_project([Slide(title="x", narration="y")]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/export/pdf")
            self.assertEqual(response.status_code, 404)

    def test_regenerate_rejects_manually_added_slide_without_source(self):
        with _temp_project([Slide(title="Elle eklendi")]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/slides/0/regenerate",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("kaynağı yok", response.json()["detail"])

    def test_regenerate_rejects_out_of_range_index(self):
        with _temp_project([Slide(title="Tek slayt", source_section_ids=["fp"])]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/slides/5/regenerate",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={},
            )
            self.assertEqual(response.status_code, 404)

    def test_regenerate_queues_a_job_and_replaces_the_slide(self):
        from app.generation_checkpoint import source_fingerprint
        from app.models import RawSection

        section = RawSection(breadcrumb="", title="1.1 Giriş", text="içerik")
        fp = source_fingerprint(section)
        slide = Slide(title="Eski slayt", source_section_ids=[fp], source_titles=["1.1 Giriş"])
        sections = [{"breadcrumb": "", "title": "1.1 Giriş", "text": "içerik", "code_blocks": [], "level": 3}]

        with _temp_project([slide], sections=sections) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value.generate.return_value = [Slide(title="Yeni slayt")]
                fake_cls.return_value.total_cost_usd = 0.0

                response = self.client.post(
                    f"/api/projects/{pdir.name}/slides/0/regenerate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"provider": "agent"},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["jobId"]

                for _ in range(50):
                    job = self.client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertEqual(job["result"]["slides"][0]["title"], "Yeni slayt")
                self.assertEqual(job["result"]["slides"][0]["sourceTitles"], ["1.1 Giriş"])
                self.assertTrue((pdir / "snapshots").exists())


class GenerateEndpointDurationLimitTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    @staticmethod
    def _sections():
        return [{"breadcrumb": "", "title": "1. Giriş", "text": "içerik", "code_blocks": [], "level": 3}]

    def _wait_for_job(self, job_id: str) -> dict:
        job = {}
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"complete", "failed"}:
                break
            time.sleep(0.05)
        return job

    def test_duration_limit_enabled_requires_numeric_minutes(self):
        with _temp_project([], sections=self._sections()) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/generate",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={"sectionIndexes": [0], "durationLimitEnabled": True, "targetDurationMinutes": "abc"},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("dakika", response.json()["detail"])

    def test_duration_limit_enabled_rejects_out_of_range_minutes(self):
        with _temp_project([], sections=self._sections()) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/generate",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={"sectionIndexes": [0], "durationLimitEnabled": True, "targetDurationMinutes": 0},
            )
            self.assertEqual(response.status_code, 400)

    def test_duration_limit_is_passed_through_to_generate_chunked(self):
        with _temp_project([], sections=self._sections()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value.generate_chunked.return_value = []
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0], "durationLimitEnabled": True, "targetDurationMinutes": 45},
                )
                self.assertEqual(response.status_code, 200)
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "complete", job.get("error"))

                _, kwargs = fake_cls.return_value.generate_chunked.call_args
                self.assertEqual(kwargs["target_duration_minutes"], 45.0)

    def test_duration_limit_defaults_to_none_when_disabled(self):
        with _temp_project([], sections=self._sections()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value.generate_chunked.return_value = []
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0]},
                )
                self.assertEqual(response.status_code, 200)
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "complete", job.get("error"))

                _, kwargs = fake_cls.return_value.generate_chunked.call_args
                self.assertIsNone(kwargs["target_duration_minutes"])


class VisionApiKeyResolutionTests(unittest.TestCase):
    def test_disabled_returns_none_without_touching_saved_keys(self):
        with patch.object(api_module, "get_api_key") as get_key, \
             patch.object(api_module, "save_api_key") as save_key:
            result = _resolve_vision_api_key(False, "")
            self.assertIsNone(result)
            get_key.assert_not_called()
            save_key.assert_not_called()

    def test_provided_key_is_saved_and_returned(self):
        with patch.object(api_module, "save_api_key") as save_key:
            result = _resolve_vision_api_key(True, "  new-key  ")
            self.assertEqual(result, "new-key")
            save_key.assert_called_once_with("GEMINI_API_KEY", "new-key")

    def test_falls_back_to_saved_key_when_none_provided(self):
        with patch.object(api_module, "get_api_key", return_value="saved-key") as get_key:
            result = _resolve_vision_api_key(True, "")
            self.assertEqual(result, "saved-key")
            get_key.assert_called_once_with("GEMINI_API_KEY")

    def test_missing_key_entirely_raises_400(self):
        with patch.object(api_module, "get_api_key", return_value=""):
            with self.assertRaises(HTTPException) as raised:
                _resolve_vision_api_key(True, "")
            self.assertEqual(raised.exception.status_code, 400)


class CostSummaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    def test_project_payload_reflects_recorded_agent_cost_after_generate(self):
        sections = [{"breadcrumb": "", "title": "1. Giriş", "text": "içerik", "code_blocks": [], "level": 3}]
        with _temp_project([], sections=sections) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value.generate_chunked.return_value = [Slide(title="X", narration="bir iki üç")]
                fake_cls.return_value.total_cost_usd = 0.0456
                fake_cls.return_value.session_id = "sess"

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0]},
                )
                job_id = response.json()["jobId"]
                for _ in range(50):
                    job = self.client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(job["status"], "complete", job.get("error"))

            payload = self.client.get(f"/api/projects/{pdir.name}").json()
            self.assertAlmostEqual(payload["costSummary"]["totalUsd"], 0.0456)
            self.assertFalse(payload["costSummary"]["hasUnknownCostProvider"])

    def test_global_cost_summary_endpoint_aggregates_projects(self):
        sections = [{"breadcrumb": "", "title": "1", "text": "x", "code_blocks": [], "level": 3}]
        with _temp_project([], sections=sections) as pdir:
            from app.cost_ledger import record
            record(pdir, provider="agent", kind="generate", usd=0.01, words=10)
            response = self.client.get("/api/cost-summary")
            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(response.json()["totalUsd"], 0.01)


class QueueEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    def test_add_rejects_missing_source_file(self):
        response = self.client.post(
            "/api/queue",
            headers={"Origin": "http://127.0.0.1:5173"},
            json={"sourcePath": "C:/does/not/exist.pdf"},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_rejects_page_mode_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık", encoding="utf-8")
            response = self.client.post(
                "/api/queue",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={"sourcePath": str(md_path), "pageMode": True},
            )
            self.assertEqual(response.status_code, 400)

    def test_add_list_and_remove_round_trip(self):
        from studio_web.batch_queue import BatchQueueStore

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as queue_dir:
            pdf_path = Path(tmp) / "kaynak.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            # Gerçek, paylaşılan _cache/batch_queue dizinini kirletmemek için bu
            # testin süresince izole bir kuyruk deposu kullan.
            isolated_queue = BatchQueueStore(Path(queue_dir))
            with patch.object(api_module, "queue", isolated_queue), \
                 patch.object(isolated_queue, "_ensure_worker"):
                add_response = self.client.post(
                    "/api/queue",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={
                        "sourcePath": str(pdf_path), "llmSettings": {"provider": "agent"},
                        "videoSettings": {"ttsProvider": "edge"},
                    },
                )
                self.assertEqual(add_response.status_code, 200)
                item_id = add_response.json()["id"]

                list_response = self.client.get("/api/queue")
                items = list_response.json()["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], item_id)
                self.assertEqual(items[0]["status"], "queued")

                delete_response = self.client.delete(
                    f"/api/queue/{item_id}", headers={"Origin": "http://127.0.0.1:5173"},
                )
                self.assertEqual(delete_response.status_code, 200)
                self.assertEqual(self.client.get("/api/queue").json()["items"], [])

    def test_remove_unknown_item_is_404(self):
        response = self.client.delete(
            "/api/queue/does-not-exist", headers={"Origin": "http://127.0.0.1:5173"},
        )
        self.assertEqual(response.status_code, 404)


class GenerateEndpointPageModeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    @staticmethod
    def _page_sections():
        return [
            {"breadcrumb": "", "title": "1. Sayfa", "text": "birinci sayfa içeriği",
             "code_blocks": [], "level": 3, "page_image": "/tmp/page_001.png"},
            {"breadcrumb": "", "title": "2. Sayfa", "text": "ikinci sayfa içeriği",
             "code_blocks": [], "level": 3, "page_image": "/tmp/page_002.png"},
        ]

    def test_project_payload_exposes_page_mode_flag(self):
        with _temp_project([], sections=self._page_sections()) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}")
            self.assertTrue(response.json()["pageMode"])
        with _temp_project([], sections=[{"breadcrumb": "", "title": "1", "text": "x", "code_blocks": [], "level": 3}]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}")
            self.assertFalse(response.json()["pageMode"])

    def test_page_mode_forces_one_section_per_call_and_disables_single_request(self):
        with _temp_project([], sections=self._page_sections()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value.generate_chunked.return_value = []
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0, 1], "singleRequest": True, "maxSections": 10},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["jobId"]
                for _ in range(50):
                    job = self.client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(job["status"], "complete", job.get("error"))

                _, kwargs = fake_cls.return_value.generate_chunked.call_args
                self.assertEqual(kwargs["max_sections_per_chunk"], 1)
                self.assertFalse(kwargs["single_request"])
                self.assertTrue(kwargs["single_slide_per_section"])

    def test_page_mode_assigns_background_image_from_source_page(self):
        with _temp_project([], sections=self._page_sections()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                def fake_generate_chunked(sections, style_note="", chunk_completed_cb=None, **kwargs):
                    slide = Slide(title="Sayfa slaytı", narration="anlatım")
                    if chunk_completed_cb:
                        chunk_completed_cb(1, 1, [sections[0]], [slide])
                    return [slide]

                fake_cls.return_value.generate_chunked.side_effect = fake_generate_chunked
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0]},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["jobId"]
                for _ in range(50):
                    job = self.client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"complete", "failed"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertEqual(job["result"]["slides"][0]["backgroundImage"], "/tmp/page_001.png")


class GenerateEndpointDiagramExtractionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    @staticmethod
    def _sections_with_diagrams():
        return [
            {"breadcrumb": "", "title": "1. Sayfa", "text": "birinci sayfa içeriği",
             "code_blocks": [], "level": 3, "embedded_image": "/tmp/diagram_001.png"},
            {"breadcrumb": "", "title": "2. Sayfa", "text": "ikinci sayfa içeriği",
             "code_blocks": [], "level": 3, "embedded_image": "/tmp/diagram_002.png"},
        ]

    def _wait_for_job(self, job_id):
        job = {}
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"complete", "failed"}:
                break
            time.sleep(0.05)
        return job

    def test_project_payload_reports_diagrams_extracted_count(self):
        with _temp_project([], sections=self._sections_with_diagrams()) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}")
            self.assertEqual(response.json()["diagramsExtracted"], 2)

    def test_single_section_chunk_gets_its_embedded_image_attached(self):
        with _temp_project([], sections=self._sections_with_diagrams()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                def fake_generate_chunked(sections, style_note="", chunk_completed_cb=None, **kwargs):
                    slide = Slide(title="Slayt", narration="anlatım")
                    if chunk_completed_cb:
                        chunk_completed_cb(1, 1, [sections[0]], [slide])
                    return [slide]

                fake_cls.return_value.generate_chunked.side_effect = fake_generate_chunked
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0], "maxSections": 1},
                )
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertEqual(job["result"]["slides"][0]["embeddedImage"], "/tmp/diagram_001.png")

    def test_multi_section_chunk_does_not_guess_which_slide_gets_the_image(self):
        with _temp_project([], sections=self._sections_with_diagrams()) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                def fake_generate_chunked(sections, style_note="", chunk_completed_cb=None, **kwargs):
                    slides = [Slide(title="Slayt 1", narration="a"), Slide(title="Slayt 2", narration="b")]
                    if chunk_completed_cb:
                        chunk_completed_cb(1, 1, sections, slides)
                    return slides

                fake_cls.return_value.generate_chunked.side_effect = fake_generate_chunked
                fake_cls.return_value.total_cost_usd = 0.0
                fake_cls.return_value.session_id = None

                response = self.client.post(
                    f"/api/projects/{pdir.name}/generate",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"sectionIndexes": [0, 1], "maxSections": 2},
                )
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertIsNone(job["result"]["slides"][0]["embeddedImage"])
                self.assertIsNone(job["result"]["slides"][1]["embeddedImage"])

    def test_source_upload_rejects_extract_diagrams_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık", encoding="utf-8")
            with md_path.open("rb") as fh:
                response = self.client.post(
                    "/api/source/upload",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    files={"file": ("kaynak.md", fh, "text/markdown")},
                    data={"extract_diagrams": "true"},
                )
            self.assertEqual(response.status_code, 400)

    def test_source_path_rejects_extract_diagrams_for_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "kaynak.md"
            md_path.write_text("# Başlık", encoding="utf-8")
            response = self.client.post(
                "/api/source/path",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={"path": str(md_path), "extractDiagrams": True},
            )
            self.assertEqual(response.status_code, 400)


class FlashcardDeckEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")

    @staticmethod
    def _slide():
        return Slide(title="Kesmeler", narration="Kesmeler hakkında anlatım.", bullets=["İşlemci kesme isteği alır"])

    def _create_deck(self, project_id, name="Deste A", kind="static"):
        return self.client.post(
            f"/api/projects/{project_id}/flashcards/decks",
            headers={"Origin": "http://127.0.0.1:5173"}, json={"name": name, "kind": kind},
        )

    def _wait_for_job(self, job_id: str) -> dict:
        job = {}
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"complete", "failed"}:
                break
            time.sleep(0.05)
        return job

    def test_list_decks_is_empty_until_one_is_created(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["decks"], [])

    def test_create_deck_requires_slides(self):
        with _temp_project([]) as pdir:
            response = self._create_deck(pdir.name)
            self.assertEqual(response.status_code, 400)

    def test_create_deck_generates_cards_and_is_listed(self):
        with _temp_project([self._slide()]) as pdir:
            response = self._create_deck(pdir.name, name="Sınav Öncesi")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["name"], "Sınav Öncesi")
            self.assertEqual(data["kind"], "static")
            self.assertGreater(len(data["cards"]), 0)
            self.assertEqual(data["summary"]["totalCards"], len(data["cards"]))

            listed = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks").json()["decks"]
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["id"], data["id"])

    def test_multiple_decks_can_coexist(self):
        with _temp_project([self._slide()]) as pdir:
            self._create_deck(pdir.name, name="Deste A")
            self._create_deck(pdir.name, name="Deste B")
            listed = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks").json()["decks"]
            self.assertEqual({d["name"] for d in listed}, {"Deste A", "Deste B"})

    def test_llm_kind_queues_a_job_and_creates_the_deck(self):
        with _temp_project([self._slide()]) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value._call.return_value = (
                    '[{"kind": "basic", "front": "Kesme nedir?", "back": "Bir donanım sinyalidir."}]'
                )
                fake_cls.return_value.total_cost_usd = 0.0

                response = self.client.post(
                    f"/api/projects/{pdir.name}/flashcards/decks",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"name": "YZ Destesi", "kind": "llm", "provider": "agent",
                          "count": 5, "focusPrompt": "kesmelere odaklan"},
                )
                self.assertEqual(response.status_code, 200)
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "complete", job.get("error"))
                deck = job["result"]["deck"]
                self.assertEqual(deck["kind"], "llm")
                self.assertEqual(deck["focusPrompt"], "kesmelere odaklan")
                self.assertEqual(deck["cards"][0]["front"], "Kesme nedir?")

                sent_prompt = fake_cls.return_value._call.call_args[0][0]
                self.assertIn("5", sent_prompt)
                self.assertIn("kesmelere odaklan", sent_prompt)

            listed = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks").json()["decks"]
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["kind"], "llm")

    def test_llm_kind_job_fails_clearly_when_model_returns_no_cards(self):
        with _temp_project([self._slide()]) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value._call.return_value = "[]"
                fake_cls.return_value.total_cost_usd = 0.0

                response = self.client.post(
                    f"/api/projects/{pdir.name}/flashcards/decks",
                    headers={"Origin": "http://127.0.0.1:5173"},
                    json={"name": "YZ Destesi", "kind": "llm", "provider": "agent"},
                )
                job = self._wait_for_job(response.json()["jobId"])
                self.assertEqual(job["status"], "failed")

    def test_unknown_deck_kind_is_rejected(self):
        with _temp_project([self._slide()]) as pdir:
            response = self._create_deck(pdir.name, kind="sihirli")
            self.assertEqual(response.status_code, 400)

    def test_llm_count_out_of_range_is_rejected(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={"name": "YZ Destesi", "kind": "llm", "count": 999},
            )
            self.assertEqual(response.status_code, 400)

    def test_regenerate_is_rejected_for_llm_decks(self):
        with _temp_project([self._slide()]) as pdir:
            with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as fake_cls:
                fake_cls.return_value._call.return_value = '[{"kind": "basic", "front": "a", "back": "b"}]'
                fake_cls.return_value.total_cost_usd = 0.0

                response = self.client.post(
                    f"/api/projects/{pdir.name}/flashcards/decks",
                    headers={"Origin": "http://127.0.0.1:5173"}, json={"name": "YZ Destesi", "kind": "llm"},
                )
                deck = self._wait_for_job(response.json()["jobId"])["result"]["deck"]

            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/generate",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 400)

    def test_get_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/yok")
            self.assertEqual(response.status_code, 404)

    def test_regenerate_preserves_progress_for_unchanged_cards(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "good"},
            )

            regenerated = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/generate",
                headers={"Origin": "http://127.0.0.1:5173"},
            ).json()

            card = next(c for c in regenerated["cards"] if c["id"] == card_id)
            self.assertEqual(card["repetitions"], 1)

    def test_regenerate_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/yok/generate",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 404)

    def test_review_applies_sm2_scheduling_and_persists(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]

            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "good"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["card"]["repetitions"], 1)

            reloaded = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}").json()
            reloaded_card = next(c for c in reloaded["cards"] if c["id"] == card_id)
            self.assertEqual(reloaded_card["repetitions"], 1)

    def test_review_rejects_unknown_rating(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "excellent"},
            )
            self.assertEqual(response.status_code, 400)

    def test_review_unknown_card_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/does-not-exist/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "good"},
            )
            self.assertEqual(response.status_code, 404)

    def test_review_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/yok/cards/whatever/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "good"},
            )
            self.assertEqual(response.status_code, 404)

    def test_suspend_toggles_and_excludes_from_due_count(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            due_before = deck["summary"]["dueCount"]

            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/suspend",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"suspended": True},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["summary"]["dueCount"], due_before - 1)
            self.assertTrue(response.json()["card"]["suspended"])

    def test_delete_deck_removes_it_from_the_list(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.delete(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get(f"/api/projects/{pdir.name}/flashcards/decks").json()["decks"], [])

    def test_delete_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.delete(
                f"/api/projects/{pdir.name}/flashcards/decks/yok",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 404)

    def test_export_returns_anki_compatible_tsv(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name, name="Sınav Öncesi").json()
            response = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/export")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertIn(".txt", response.headers["content-disposition"])
            body = response.text
            self.assertTrue(body.startswith("Front\tBack"))
            self.assertEqual(len(body.strip("\n").split("\n")) - 1, len(deck["cards"]))

    def test_export_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/yok/export")
            self.assertEqual(response.status_code, 404)

    def test_add_card_appends_a_manual_card(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            before = len(deck["cards"])

            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "Elle soru", "back": "Elle cevap"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["card"]["manual"])

            reloaded = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}").json()
            self.assertEqual(len(reloaded["cards"]), before + 1)

    def test_add_card_rejects_empty_front(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "", "back": "cevap"},
            )
            self.assertEqual(response.status_code, 400)

    def test_add_card_unknown_deck_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/yok/cards",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "a", "back": "b"},
            )
            self.assertEqual(response.status_code, 404)

    def test_edit_card_updates_front_and_marks_manual(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]

            response = self.client.patch(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "Düzeltilmiş"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["card"]["front"], "Düzeltilmiş")
            self.assertTrue(response.json()["card"]["manual"])

    def test_edit_card_rejects_empty_front(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            response = self.client.patch(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "  "},
            )
            self.assertEqual(response.status_code, 400)

    def test_edit_card_rejects_no_changes(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            response = self.client.patch(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}",
                headers={"Origin": "http://127.0.0.1:5173"}, json={},
            )
            self.assertEqual(response.status_code, 400)

    def test_edit_unknown_card_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.patch(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/yok",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"front": "x"},
            )
            self.assertEqual(response.status_code, 404)

    def test_delete_card_removes_it(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]

            response = self.client.delete(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 200)

            reloaded = self.client.get(f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}").json()
            self.assertNotIn(card_id, [c["id"] for c in reloaded["cards"]])

    def test_delete_unknown_card_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.delete(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/yok",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertEqual(response.status_code, 404)

    def test_restore_reverts_scheduling_fields_after_a_review(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            card_id = deck["cards"][0]["id"]
            original = deck["cards"][0]

            self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/review",
                headers={"Origin": "http://127.0.0.1:5173"}, json={"rating": "good"},
            )

            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/{card_id}/restore",
                headers={"Origin": "http://127.0.0.1:5173"},
                json={
                    "easeFactor": original["easeFactor"], "intervalDays": original["intervalDays"],
                    "repetitions": original["repetitions"], "dueAt": original["dueAt"],
                    "lastReviewedAt": original["lastReviewedAt"], "suspended": original["suspended"],
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["card"]["repetitions"], 0)

    def test_restore_unknown_card_is_404(self):
        with _temp_project([self._slide()]) as pdir:
            deck = self._create_deck(pdir.name).json()
            response = self.client.post(
                f"/api/projects/{pdir.name}/flashcards/decks/{deck['id']}/cards/yok/restore",
                headers={"Origin": "http://127.0.0.1:5173"}, json={},
            )
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
