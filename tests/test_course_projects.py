"""Course/source/video integration tests use no paid APIs or renderer."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app import course_projects as courses
from app.cost_ledger import record, summarize_all_projects, summarize_project
from app.flashcards import create_deck, source_batches
from app.models import RawSection, Slide
from app.pipeline import load_raw_sections, save_script
import studio_web.api as api_module


class CourseProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_root = self.root / "projects"
        self.project_root.mkdir()
        patcher = patch.object(api_module, "PROJECTS_DIR", self.project_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(api_module.app, base_url="http://localhost")
        self.course = self.client.post("/api/projects", json={"name": "Biyoloji"}).json()
        self.base = "/api/projects/" + self.course["id"]
        self.pdir = self.project_root / self.course["id"]

    def upload(self, filename="Hafta 1.md", text="# Hücre\n\nHücre canlıların yapısal birimidir."):
        response = self.client.post(self.base + "/sources/upload",
                                    files={"file": (filename, text.encode("utf-8"), "text/markdown")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["source"]

    def video(self, ids, name="Video"):
        response = self.client.post(self.base + "/videos", json={"name": name, "sourceIds": ids})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_empty_courses_are_listed_and_same_name_never_overwrites(self):
        second = self.client.post("/api/projects", json={"name": "Biyoloji"}).json()
        self.assertNotEqual(second["id"], self.course["id"])
        listing = self.client.get("/api/bootstrap").json()["projects"]
        self.assertEqual(len(listing), 2)
        self.assertEqual(listing[0]["sourceCount"], 0)
        self.assertEqual(self.client.post("/api/projects", json={"name": ""}).status_code, 400)

    def test_same_filename_imports_have_distinct_persistent_source_files(self):
        a = self.upload()
        b = self.upload(text="# Enzim\n\nEnzimler katalizördür.")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(courses.list_sources(self.pdir)), 2)
        original = self.client.get(self.base + "/sources/" + a["id"] + "/file")
        self.assertIn("Hücre", original.text)
        self.assertNotIn("Enzimler", original.text)
        self.assertTrue((self.pdir / "sources" / a["id"] / "document.md").is_file())

    def test_failed_import_does_not_publish_or_remove_other_sources(self):
        self.upload()
        with patch("app.course_projects.parse_source", side_effect=ValueError("Bozuk dosya")):
            failure = self.client.post(self.base + "/sources/upload", files={"file": ("bad.pdf", b"bad")})
        self.assertEqual(failure.status_code, 400)
        self.assertEqual(len(courses.list_sources(self.pdir)), 1)
        self.assertEqual(len(list((self.pdir / "sources").iterdir())), 1)

    def test_video_single_source_is_exact_original_and_videos_are_isolated(self):
        source = self.upload()
        a = self.video([source["id"]], "İlk anlatım")
        b = self.video([source["id"]], "İkinci anlatım")
        adir = self.pdir / "videos" / a["videoId"]
        bdir = self.pdir / "videos" / b["videoId"]
        original = load_raw_sections(self.pdir / "sources" / source["id"])
        self.assertEqual(load_raw_sections(adir), original)
        self.assertNotEqual(a["apiBase"], b["apiBase"])
        self.assertEqual(self.client.put(a["apiBase"] + "/slides",
                         json={"slides": [Slide(title="İlk anlatı", narration="Özel açıklama.").to_dict()]}).status_code, 200)
        self.assertEqual(self.client.get(b["apiBase"]).json()["slides"], [])
        self.assertEqual(self.client.get(self.base).json()["sources"][0]["sectionCount"], len(original))
        self.assertFalse((self.pdir / "script.json").exists())
        (adir / "ders.mp4").write_bytes(b"first-video")
        (bdir / "ders.mp4").write_bytes(b"second-video")
        self.assertEqual(self.client.get(a["apiBase"] + "/output/video").content, b"first-video")
        self.assertEqual(self.client.get(b["apiBase"] + "/output/video").content, b"second-video")
        self.assertEqual(self.client.post(a["apiBase"] + "/quality").status_code, 200)
        self.assertEqual(self.client.get(a["apiBase"] + "/snapshots").status_code, 200)
        self.assertIn("İlk anlatı", self.client.get(a["apiBase"] + "/export/notes").text)

    def test_selection_order_provenance_and_cross_project_access(self):
        a = self.upload("Hafta 1.md", "# Hücre\n\nHücre içeriği.")
        b = self.upload("Hafta 2.md", "# Enzim\n\nEnzim içeriği.")
        video = self.video([b["id"], a["id"]])
        self.assertEqual(video["sourceIds"], [b["id"], a["id"]])
        self.assertIn("Enzim", video["sections"][0]["title"])
        self.assertEqual(video["sourceRefs"][0]["sourceId"], b["id"])
        for ids in [[], [a["id"], a["id"]], ["../outside"], [123]]:
            self.assertEqual(self.client.post(self.base + "/videos", json={"sourceIds": ids}).status_code, 400)
        other = self.client.post("/api/projects", json={"name": "Başka ders"}).json()
        result = self.client.post("/api/projects/" + other["id"] + "/videos", json={"sourceIds": [a["id"]]})
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.client.get("/api/projects/" + other["id"] + "/videos/" + video["videoId"]).status_code, 404)
        self.assertEqual(self.client.put(self.base + "/slides", json={"slides": []}).status_code, 409)

    def test_mixed_page_modes_are_rejected_without_changing_existing_videos(self):
        a = self.upload()
        pdf = self.root / "page.pdf"
        pdf.write_bytes(b"pdf")
        with patch("app.course_projects.parse_source", return_value=[RawSection("", "Sayfa", "Metin", page_image="page.png")]):
            b = courses.import_source(self.pdir, pdf, "Sayfa.pdf", page_mode=True)
        response = self.client.post(self.base + "/videos", json={"sourceIds": [a["id"], b["id"]]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(courses.list_videos(self.pdir), [])

    def test_multisource_decks_work_without_any_narration_and_keep_source_references(self):
        a = self.upload("1.md", "# Hücre\n\nHücrelerin zarları vardır.")
        b = self.upload("2.md", "# Enzim\n\nEnzimler tepkimeleri hızlandırır.")
        ignored = self.upload("3.md", "# Alakasız\n\nSEÇİLMEYEN GİZLİ İÇERİK")
        result = self.client.post(self.base + "/flashcards/decks",
                                 json={"name": "Vize", "kind": "static", "sourceIds": [a["id"], b["id"]]})
        self.assertEqual(result.status_code, 200, result.text)
        deck = result.json()
        self.assertEqual(deck["sourceIds"], [a["id"], b["id"]])
        self.assertEqual({c["sourceId"] for c in deck["cards"]}, {a["id"], b["id"]})
        self.assertNotIn("SEÇİLMEYEN", json.dumps(deck))
        self.assertGreaterEqual(len(deck["cards"]), 2)
        self.assertFalse((self.pdir / "script.json").exists())
        regen = self.client.post(self.base + "/flashcards/decks/" + deck["id"] + "/generate")
        self.assertEqual(regen.status_code, 200, regen.text)
        self.assertEqual(regen.json()["sourceIds"], deck["sourceIds"])
        self.assertNotIn(ignored["id"], regen.json()["sourceIds"])
        fresh = self.client.get(self.base + "/flashcards/decks/" + deck["id"]).json()
        self.assertEqual(fresh["cards"][0]["sourceId"], a["id"])

    def test_source_costs_include_nested_videos_once(self):
        source = self.upload()
        video = self.video([source["id"]])
        record(self.pdir, provider="agent", kind="flashcards", usd=0.1)
        record(self.pdir / "videos" / video["videoId"], provider="agent", kind="generate", usd=0.2)
        self.assertEqual(summarize_project(self.pdir)["totalUsd"], 0.3)
        self.assertEqual(summarize_all_projects(self.project_root)["entryCount"], 2)

    def test_llm_batches_cover_all_selected_text_and_limit_total_cards(self):
        slides = [Slide(title=str(i), narration=("kaynak" + str(i) + " ") * 1800) for i in range(4)]
        batches = source_batches(slides)
        self.assertEqual([s for batch in batches for s in batch], slides)
        generator = Mock()
        generator._call.side_effect = [json.dumps([{"kind": "basic", "front": f"Soru {i}-{j}", "back": "Yanıt"} for j in range(20)]) for i in range(len(batches))]
        deck = create_deck(self.pdir, "Büyük deste", "llm", slides, generator=generator, count=8,
                           source_context={"sourceIds": ["test"], "sourceNames": ["Deneme"], "sourceRefs": []})
        self.assertEqual(generator._call.call_count, len(batches))
        self.assertLessEqual(len(deck["cards"]), 8)
        for i, call in enumerate(generator._call.call_args_list):
            self.assertIn("kaynak" + str(i), call.args[0])

    def test_pdf_single_source_preserves_page_image_and_original_text(self):
        path = self.root / "lesson.pdf"
        path.write_bytes(b"pdf")
        original = RawSection("1", "Birinci sayfa", "Orijinal metin", page_image="absolute-page.png")
        with patch("app.course_projects.parse_source", return_value=[original]):
            source = courses.import_source(self.pdir, path, "lesson.pdf", page_mode=True)
        video = self.video([source["id"]])
        self.assertEqual(load_raw_sections(self.pdir / "videos" / video["videoId"]), [original])
        self.assertTrue(video["pageMode"])


    def test_single_source_generation_uses_original_generator_options_and_isolated_context(self):
        a = self.upload("1.md", "# Hücre\n\nHücrelerin zarları vardır.")
        self.upload("2.md", "# Enzim\n\nBU KAYNAK SEÇİLMEDİ")
        video = self.video([a["id"]])
        raw = load_raw_sections(self.pdir / "sources" / a["id"])
        with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator") as cls, patch.object(api_module, "save_settings"):
            generator = cls.return_value
            generator.generate_chunked.return_value = []
            generator.total_cost_usd = 0.0
            generator.session_id = None
            response = self.client.post(video["apiBase"] + "/generate",
                json={"sectionIndexes": [0], "provider": "agent", "apiKey": "not-persisted",
                      "maxSections": 4, "durationLimitEnabled": True, "targetDurationMinutes": 45})
            self.assertEqual(response.status_code, 200, response.text)
            for _ in range(100):
                job = self.client.get("/api/jobs/" + response.json()["jobId"]).json()
                if job["status"] in {"complete", "failed"}:
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "complete", job.get("error"))
            args, kwargs = generator.generate_chunked.call_args
            self.assertEqual(args[0], raw)
            self.assertEqual(kwargs["initial_context_slides"], [])
            self.assertEqual(kwargs["max_sections_per_chunk"], 4)
            self.assertEqual(kwargs["target_duration_minutes"], 45)
            self.assertFalse(kwargs["single_slide_per_section"])
        config = courses.read_json(self.pdir / "videos" / video["videoId"] / "video.json")
        self.assertEqual(config["narrationSettings"]["targetDurationMinutes"], 45)
        self.assertNotIn("not-persisted", json.dumps(config))
        self.assertFalse((self.pdir / "generation_checkpoint.json").exists())

    def test_real_pdf_page_images_stay_inside_the_import_and_survive_new_videos(self):
        import pymupdf
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((50, 80), "Biology lesson: cells contain a membrane and genetic material.", fontsize=14)
        source = self.root / "real.pdf"
        document.save(source)
        document.close()
        meta = courses.import_source(self.pdir, source, "real.pdf", page_mode=True)
        original = load_raw_sections(self.pdir / "sources" / meta["id"])
        self.assertTrue(Path(original[0].page_image).is_file())
        a, b = self.video([meta["id"]]), self.video([meta["id"]])
        self.assertEqual(a["sections"], b["sections"])
        self.assertEqual(a["sections"][0]["page_image"], original[0].page_image)

    def test_empty_source_selection_never_schedules_paid_cards(self):
        self.upload()
        with patch.object(api_module.jobs, "create") as create:
            response = self.client.post(self.base + "/flashcards/decks",
                                        json={"kind": "llm", "sourceIds": []})
            self.assertEqual(response.status_code, 400)
            create.assert_not_called()


    def pdf(self, name="slides.pdf", pages=20, page_mode=False):
        import pymupdf
        path = self.root / name
        with pymupdf.open() as document:
            for index in range(pages):
                page = document.new_page(width=320, height=180)
                page.draw_rect(page.rect, color=None, fill=(index / pages, 0.2, 0.4))
                if index != 1:  # Keep one image-only page.
                    page.insert_text((20, 40), f"{name} Page {index + 1}: original content.")
            document.save(path)
        return courses.import_source(self.pdir, path, name, page_mode=page_mode)

    def video_mode(self, ids, mode):
        response = self.client.post(self.base + "/videos", json={
            "sourceIds": ids, "presentationMode": mode,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_job(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(1000):
            job = self.client.get("/api/jobs/" + response.json()["jobId"]).json()
            if job["status"] in {"complete", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "complete", job.get("error"))
        return job["result"]

    def test_pdf_mode_selected_after_upload_keeps_all_20_pages_and_regeneration(self):
        from app.llm.base import NarrationGenerator
        from PIL import Image, ImageChops
        from app.video.slide_renderer import render_slide

        class Generator(NarrationGenerator):
            session_id = None
            total_cost_usd = 0.0

            def generate(self, sections, style_note=""):
                # Deliberately return too many slides: the pipeline must merge.
                from app.generation_checkpoint import source_fingerprint
                return [Slide(title=section.title, narration=narration,
                              source_section_ids=[source_fingerprint(section)])
                        for section in reversed(sections)
                        for narration in ["Birinci açıklama.", "İkinci açıklama."]]

        source = self.pdf()
        sdir = self.pdir / "sources" / source["id"]
        original_bytes = (sdir / "raw_sections.json").read_bytes()
        self.assertEqual(source["sectionCount"], 20)
        self.assertFalse(any(sdir.rglob("*.png")))  # No rasterization during upload.
        video = self.video_mode([source["id"]], "pdf")
        self.assertTrue(video["pageMode"])
        self.assertEqual(len(video["sections"]), 20)
        vdir = self.pdir / "videos" / video["videoId"]
        raw = load_raw_sections(vdir)
        self.assertTrue(all(Path(s.page_image).is_file() for s in raw))
        with patch("app.llm.agent_cli_provider.AgentCliNarrationGenerator", return_value=Generator()), \
             patch.object(api_module, "save_settings"):
            result = self.wait_job(self.client.post(video["apiBase"] + "/generate", json={
                "sectionIndexes": list(range(20)), "provider": "agent", "singleRequest": False,
                "maxSections": 4,
            }))
            self.assertEqual(len(result["slides"]), 20)
            self.assertEqual([s["backgroundImage"] for s in result["slides"]], [s.page_image for s in raw])
            regenerated = self.wait_job(self.client.post(
                video["apiBase"] + "/slides/5/regenerate", json={"provider": "agent"}))
        self.assertEqual(len(regenerated["slides"]), 20)
        self.assertEqual([s["backgroundImage"] for s in regenerated["slides"]], [s.page_image for s in raw])
        self.assertIn("İkinci açıklama.", regenerated["slides"][5]["narration"])
        slide = Slide.from_dict(regenerated["slides"][5])
        image_path = vdir / "check.png"
        with Image.open(slide.background_image) as expected:
            render_slide(slide, 6, 20, "", image_path, width=expected.width, height=expected.height)
            with Image.open(image_path) as actual:
                self.assertIsNone(ImageChops.difference(expected.convert("RGB"), actual.convert("RGB")).getbbox())
        self.assertEqual((sdir / "raw_sections.json").read_bytes(), original_bytes)

    def test_same_pdf_can_create_generated_video_without_changing_page_video(self):
        source = self.pdf(pages=3, page_mode=True)
        page_video = self.video_mode([source["id"]], "pdf")
        generated = self.video_mode([source["id"]], "generated")
        self.assertFalse(generated["pageMode"])
        self.assertTrue(all(not s["page_image"] for s in generated["sections"]))
        self.assertEqual(len(generated["sections"]), 2)  # Image-only page has no source text.
        self.assertTrue(self.client.get(page_video["apiBase"]).json()["pageMode"])
        self.assertTrue(courses.read_json(self.pdir / "sources" / source["id"] / "source.json")["pageMode"])

    def test_pdf_mode_combines_pages_in_order_without_filename_collisions(self):
        first, second = self.pdf("first.pdf", 3), self.pdf("second.pdf", 2)
        video = self.video_mode([second["id"], first["id"]], "pdf")
        self.assertEqual(len(video["sections"]), 5)
        paths = [s["page_image"] for s in video["sections"]]
        self.assertEqual(len(set(paths)), 5)
        self.assertEqual([r["sourceId"] for r in video["sourceRefs"]], [second["id"]]*2 + [first["id"]]*3)
        self.assertEqual([r["pageNumber"] for r in video["sourceRefs"]], [1, 2, 1, 2, 3])

    def test_old_text_only_import_recovers_omitted_pdf_pages_without_reupload(self):
        source = self.pdf(pages=3)
        sdir = self.pdir / "sources" / source["id"]
        sections = courses.read_json(sdir / "raw_sections.json")
        sections = [s for s in sections if s["text"]]
        sections[0]["text"] += "\nPreviously paid vision description."
        courses.write_json(sdir / "raw_sections.json", sections)
        with patch("app.vision_caption.caption_page_image") as paid:
            video = self.video_mode([source["id"]], "pdf")
            paid.assert_not_called()
        self.assertEqual(len(video["sections"]), 3)
        self.assertIn("Previously paid vision", video["sections"][0]["text"])
        self.assertIsNone(video["sourceRefs"][1]["sectionIndex"])

    def test_invalid_mode_or_non_pdf_selection_creates_no_video(self):
        source = self.upload()
        for mode in ["pdf", "unknown", [], {}]:
            response = self.client.post(self.base + "/videos", json={
                "sourceIds": [source["id"]], "presentationMode": mode,
            })
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(courses.list_videos(self.pdir), [])

    def test_page_preparation_failure_removes_partial_video(self):
        source = self.pdf(pages=2)
        with patch("app.course_projects.parse_source", side_effect=ValueError("PDF okunamadı")):
            response = self.client.post(self.base + "/videos", json={
                "sourceIds": [source["id"]], "presentationMode": "pdf",
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list((self.pdir / "videos").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
