"""Eşzamanlı video parçası üretimi (VideoOptions.overlap_segments) testleri.

İki şeyi birlikte güvenceye alır:
  1. Seçenek KAPALIYKEN (varsayılan) eski sıralı akış hiç değişmez: önce bütün sesler,
     sonra bütün parçalar; TTS fonksiyonlarına yeni parametre gönderilmez; ikinci
     ilerleme çubuğu bildirilmez.
  2. Seçenek AÇIKKEN her slaytın parçası sesi biter bitmez üretilir, ama ilk ses
     bitmeden (modeller yüklenmeden) hiçbir parça başlamaz; hata, iptal ve önbellek
     davranışı doğru kalır ve çıktı sıralı modla aynıdır.

Gerçek TTS/ffmpeg/PIL çalışmaz: ses dosyası, ffprobe, render_slide, build_segment ve
concat_* sahtelenir; olaylar ortak bir günlüğe zaman sırasıyla yazılır.
"""
import json
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import DEFAULT_SETTINGS, VideoOptions
from app.models import Slide, SynthResult, WordTiming
from app.pipeline import PASS3_MAX_WORKERS, project_dir_for, render_video, save_script


class EventLog:
    def __init__(self):
        self.events: list[tuple] = []
        self.lock = threading.Lock()

    def add(self, *event):
        with self.lock:
            self.events.append(event)

    def index_of(self, event) -> int:
        return self.events.index(event)

    def names(self, kind: str) -> list:
        return [e[1] for e in self.events if e[0] == kind]


class RecordingProvider:
    """Sıralı TTS sağlayıcısı: her slayt için 'tts_start'/'tts_done' olayları yazar."""

    def __init__(self, log: EventLog, delay: float = 0.0, fail_on: str | None = None, with_words: bool = False):
        self.log, self.delay, self.fail_on, self.with_words = log, delay, fail_on, with_words
        self.calls: list[str] = []

    def synthesize(self, text, voice, out_path, rate="+0%"):
        self.calls.append(text)
        self.log.add("tts_start", text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError(f"TTS çöktü: {text}")
        time.sleep(self.delay)
        Path(out_path).write_bytes(b"fake-mp3")
        self.log.add("tts_done", text)
        words = [WordTiming(text=w, start=i * 0.3, end=i * 0.3 + 0.25) for i, w in enumerate(text.split())]
        return SynthResult(duration=1.0, words=words if self.with_words else None)


class OverlapHarness(unittest.TestCase):
    """render_video'yu sahte ffmpeg/PIL ile çalıştıran ortak düzenek."""

    segment_delay = 0.0

    def setUp(self):
        self.log = EventLog()
        self.segment_fail_on: int | None = None
        self.segment_calls: list[dict] = []
        self.active_segments = 0
        self.max_active_segments = 0
        self.segment_progress: list[tuple[int, int]] = []
        self.statuses: list[str] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, kwargs in (
            ("app.pipeline.PROJECTS_DIR", {"new": Path(self.tmp.name)}),
            ("app.pipeline.ffprobe_duration", {"return_value": 1.0}),
            ("app.pipeline.render_slide", {"side_effect": self._fake_render_slide}),
            ("app.pipeline.build_segment", {"side_effect": self._fake_build_segment}),
            ("app.pipeline.concat_videos", {}),
            ("app.pipeline.concat_audio", {}),
        ):
            patcher = patch(target, **kwargs)
            setattr(self, target.rsplit(".", 1)[1], patcher.start())
            self.addCleanup(patcher.stop)

    def _fake_render_slide(self, slide, i, total, breadcrumb, img_path, **kw):
        Path(img_path).write_bytes(b"\x89PNG\r\n")

    def _fake_build_segment(self, img_path, audio_path, duration, seg_path, opts, ass_path=None, reveal_stages=None):
        index = int(Path(seg_path).stem.split("_")[1])
        with self.log.lock:
            self.active_segments += 1
            self.max_active_segments = max(self.max_active_segments, self.active_segments)
        self.log.add("seg_start", index)
        try:
            time.sleep(self.segment_delay)
            if self.segment_fail_on == index:
                self.log.add("seg_fail", index)
                raise RuntimeError(f"ffmpeg segment hatası (segment_{index:03d}.mp4)")
            self.segment_calls.append({
                "index": index, "duration": duration,
                "ass": Path(ass_path).read_text(encoding="utf-8") if ass_path else None,
            })
            Path(seg_path).write_bytes(b"fake-segment")
            self.log.add("seg_done", index)
        finally:
            with self.log.lock:
                self.active_segments -= 1

    def project(self, name: str, slides: list[Slide]) -> Path:
        pdir = project_dir_for(name)
        save_script(pdir, slides)
        return pdir

    def render(self, pdir, slides, opts, provider=None, provider_name="edge", watchdog=20.0):
        """render_video'yu ayrı iş parçacığında çalıştırır; asılı kalırsa test açıkça düşer."""
        holder = {}

        def target():
            try:
                with patch("app.pipeline.get_provider", return_value=provider):
                    holder["result"] = render_video(
                        pdir, slides, provider_name, "v", "+0%", opts,
                        status_cb=self.statuses.append,
                        segment_progress_cb=lambda d, t: self.segment_progress.append((d, t)),
                    )
            except BaseException as exc:  # testte incelenmek üzere taşınır
                holder["error"] = exc

        runner = threading.Thread(target=target, daemon=True)
        runner.start()
        runner.join(watchdog)
        self.assertFalse(runner.is_alive(), "render asılı kaldı")
        if "error" in holder:
            raise holder["error"]
        return holder["result"]


def _slides(n: int) -> list[Slide]:
    return [Slide(title=f"Slayt {i}", narration=f"anlatım {i} metni") for i in range(1, n + 1)]


class DefaultSequentialModeIsUnchangedTests(OverlapHarness):
    def test_option_is_off_by_default_everywhere(self):
        self.assertFalse(VideoOptions().overlap_segments)
        self.assertIs(DEFAULT_SETTINGS["overlap_segments"], False)

    def test_all_audio_is_finished_before_any_segment_starts(self):
        slides = _slides(5)
        pdir = self.project("sirali", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook"), RecordingProvider(self.log, delay=0.01))

        last_tts = max(i for i, e in enumerate(self.log.events) if e[0] == "tts_done")
        first_seg = min(i for i, e in enumerate(self.log.events) if e[0] == "seg_start")
        self.assertLess(last_tts, first_seg, "sıralı modda parça, tüm sesler bitmeden başlamamalı")
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3, 4, 5])
        self.assertEqual(self.segment_progress, [], "sıralı modda ikinci ilerleme çubuğu bildirilmemeli")

    @patch("app.pipeline.synthesize_parallel")
    def test_parallel_tts_is_called_with_its_old_signature(self, fake_parallel):
        # Eski imza: item_done_cb parametresi YOK. Kapalı modda bu çağrı kırılmamalı.
        def old_signature(items, n_workers, progress_cb=None, status_cb=None, retry_incomplete=False):
            for _text, _voice, out_path in items:
                Path(out_path).write_bytes(b"fake-mp3")
            return [SynthResult(duration=0.0, words=None) for _ in items]

        fake_parallel.side_effect = old_signature
        slides = _slides(3)
        pdir = self.project("eski-imza", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", coqui_parallel_workers=2),
                    provider_name="coqui")
        self.assertNotIn("item_done_cb", fake_parallel.call_args.kwargs)
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3])


class OverlapModeTests(OverlapHarness):
    segment_delay = 0.02

    def test_segments_start_while_later_slides_are_still_being_voiced(self):
        slides = _slides(6)
        pdir = self.project("eszamanli", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                    RecordingProvider(self.log, delay=0.05))

        first_seg = self.log.index_of(("seg_start", 1))
        last_tts_done = self.log.index_of(("tts_done", "anlatım 6 metni"))
        self.assertLess(first_seg, last_tts_done, "1. slaytın parçası son ses bitmeden başlamalı")
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3, 4, 5, 6])

    def test_no_segment_starts_before_the_first_audio_is_finished(self):
        slides = _slides(4)
        pdir = self.project("ilk-ses", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                    RecordingProvider(self.log, delay=0.03))
        first_tts_done = self.log.index_of(("tts_done", "anlatım 1 metni"))
        first_seg = min(i for i, e in enumerate(self.log.events) if e[0] == "seg_start")
        self.assertLess(first_tts_done, first_seg)

    def test_slides_with_reused_audio_wait_for_the_first_new_audio(self):
        slides = _slides(4)
        pdir = self.project("yeniden-kullanilan", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook"), RecordingProvider(self.log))

        # Tema değişti (tüm parçalar yeniden) + yalnız 3. slaytın anlatımı değişti (tek yeni ses).
        self.log.events.clear()
        changed = [*slides[:2], Slide(title="Slayt 3", narration="yeni anlatım üç"), slides[3]]
        provider = RecordingProvider(self.log, delay=0.05)
        self.render(pdir, changed, VideoOptions(theme_preset="aurora", overlap_segments=True), provider)

        self.assertEqual(provider.calls, ["yeni anlatım üç"], "yalnız değişen slayt seslendirilmeli")
        tts_done = self.log.index_of(("tts_done", "yeni anlatım üç"))
        first_seg = min(i for i, e in enumerate(self.log.events) if e[0] == "seg_start")
        self.assertLess(tts_done, first_seg, "sesi hazır slaytlar da modeller hazır olana dek beklemeli")
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3, 4])

    def test_segment_concurrency_never_exceeds_the_pass3_limit(self):
        self.segment_delay = 0.05
        slides = _slides(10)
        pdir = self.project("sinir", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                    RecordingProvider(self.log))
        self.assertLessEqual(self.max_active_segments, PASS3_MAX_WORKERS)
        self.assertEqual(sorted(self.log.names("seg_done")), list(range(1, 11)))

    def test_final_concat_order_follows_slide_index(self):
        slides = _slides(6)
        pdir = self.project("sira", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                    RecordingProvider(self.log, delay=0.01))
        segment_paths = self.concat_videos.call_args[0][0]
        self.assertEqual([Path(p).stem for p in segment_paths], [f"segment_{i:03d}" for i in range(1, 7)])

    def test_segment_progress_is_monotonic_and_complete(self):
        slides = _slides(5)
        pdir = self.project("ilerleme", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                    RecordingProvider(self.log, delay=0.01))
        self.assertEqual(self.segment_progress[0], (0, 5))
        self.assertEqual(self.segment_progress[-1], (5, 5))
        done = [d for d, _t in self.segment_progress]
        self.assertEqual(done, sorted(done))
        self.assertTrue(all(t == 5 for _d, t in self.segment_progress))

    def test_second_render_is_fully_cached(self):
        slides = _slides(3)
        pdir = self.project("onbellek", slides)
        opts = VideoOptions(theme_preset="notebook", overlap_segments=True)
        self.render(pdir, slides, opts, RecordingProvider(self.log))
        self.log.events.clear()
        provider = RecordingProvider(self.log)
        self.render(pdir, slides, opts, provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.log.names("seg_start"), [])

    @patch("app.pipeline.synthesize_parallel")
    def test_parallel_xtts_path_feeds_segments_through_item_callbacks(self, fake_parallel):
        def parallel_with_callbacks(items, n_workers, progress_cb=None, status_cb=None,
                                    retry_incomplete=False, item_done_cb=None):
            # İki worker gibi: tamamlanma sırası slayt sırasından farklı.
            for position in (1, 0, 3, 2, 4):
                time.sleep(0.03)
                Path(items[position][2]).write_bytes(b"fake-mp3")
                self.log.add("tts_done", position)
                item_done_cb(position, None)
            return [SynthResult(duration=0.0, words=None) for _ in items]

        fake_parallel.side_effect = parallel_with_callbacks
        slides = _slides(5)
        pdir = self.project("xtts-paralel", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", coqui_parallel_workers=2,
                                               overlap_segments=True), provider_name="coqui")
        self.assertIn("item_done_cb", fake_parallel.call_args.kwargs)
        self.assertLess(self.log.index_of(("seg_start", 2)), self.log.index_of(("tts_done", 4)))
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3, 4, 5])

    @patch("app.pipeline.synthesize_parallel")
    def test_tts_path_that_never_reports_items_still_gets_every_segment(self, fake_parallel):
        def silent(items, n_workers, progress_cb=None, status_cb=None, retry_incomplete=False, item_done_cb=None):
            for _text, _voice, out_path in items:
                Path(out_path).write_bytes(b"fake-mp3")
            return [SynthResult(duration=0.0, words=None) for _ in items]

        fake_parallel.side_effect = silent
        slides = _slides(4)
        pdir = self.project("bildirimsiz", slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", coqui_parallel_workers=2,
                                               overlap_segments=True), provider_name="coqui")
        self.assertEqual(sorted(self.log.names("seg_done")), [1, 2, 3, 4])


class OverlapFailureTests(OverlapHarness):
    segment_delay = 0.02

    def test_tts_failure_stops_the_render_and_keeps_finished_segments_cached(self):
        slides = _slides(5)
        pdir = self.project("tts-hata", slides)
        opts = VideoOptions(theme_preset="notebook", overlap_segments=True)
        with self.assertRaisesRegex(RuntimeError, "TTS çöktü"):
            self.render(pdir, slides, opts, RecordingProvider(self.log, delay=0.02, fail_on="anlatım 4"))

        assets = pdir / "assets"
        for i in (4, 5):
            self.assertFalse((assets / f"slide_{i:03d}.hash").exists(), f"{i}. slayt tamamlanmış sayılmamalı")
        finished = [i for i in range(1, 6) if (assets / f"slide_{i:03d}.hash").exists()]
        for i in finished:
            self.assertTrue((assets / f"segment_{i:03d}.mp4").exists())

        # Hata giderilince yalnız eksik slaytlar yeniden seslendirilir.
        self.log.events.clear()
        provider = RecordingProvider(self.log)
        self.render(pdir, slides, opts, provider)
        self.assertIn("anlatım 4 metni", provider.calls)
        self.assertNotIn("anlatım 1 metni", provider.calls)
        self.assertEqual(sorted(self.log.names("seg_done")), [i for i in range(1, 6) if i not in finished])

    def test_segment_failure_surfaces_the_real_error_without_hanging(self):
        self.segment_fail_on = 2
        slides = _slides(5)
        pdir = self.project("parca-hata", slides)
        with self.assertRaisesRegex(RuntimeError, "segment_002"):
            self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                        RecordingProvider(self.log, delay=0.03))
        self.assertFalse((pdir / "assets" / "slide_002.hash").exists())
        self.concat_videos.assert_not_called()

    def test_no_new_segment_is_started_after_a_segment_failed(self):
        self.segment_delay = 0.0
        self.segment_fail_on = 1
        slides = _slides(6)
        pdir = self.project("hata-sonrasi", slides)
        with self.assertRaises(RuntimeError):
            self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=True),
                        RecordingProvider(self.log, delay=0.05))
        failed_at = self.log.index_of(("seg_fail", 1))
        # Hatadan sonra sesi biten slaytların hiçbiri için parça başlatılmamalı.
        voiced_after = {int(e[1].split()[1]) for e in self.log.events[failed_at:] if e[0] == "tts_done"}
        self.assertTrue(voiced_after, "test kurgusu: hatadan sonra da ses üretilmeli")
        started_after = set(self.log.names("seg_start")) & voiced_after
        self.assertEqual(started_after, set())


class OverlapOutputMatchesSequentialTests(OverlapHarness):
    def _render_and_collect(self, name: str, overlap: bool, with_words: bool):
        self.segment_calls = []
        slides = _slides(4)
        pdir = self.project(name, slides)
        self.render(pdir, slides, VideoOptions(theme_preset="notebook", overlap_segments=overlap),
                    RecordingProvider(self.log, with_words=with_words))
        hashes = [(pdir / "assets" / f"slide_{i:03d}.hash").read_text() for i in range(1, 5)]
        return sorted(self.segment_calls, key=lambda c: c["index"]), hashes

    def test_segments_subtitles_and_cache_hashes_are_identical_in_both_modes(self):
        for with_words in (False, True):
            with self.subTest(with_words=with_words):
                seq_calls, seq_hashes = self._render_and_collect(f"esit-sirali-{with_words}", False, with_words)
                ovl_calls, ovl_hashes = self._render_and_collect(f"esit-eszamanli-{with_words}", True, with_words)
                self.assertEqual(seq_calls, ovl_calls)
                self.assertEqual(seq_hashes, ovl_hashes)


class TtsItemCallbackTests(unittest.TestCase):
    """TTS fonksiyonlarının bildirdiği indeksler her zaman ``items`` sırasına göre olmalı."""

    def test_coqui_sequential_branch_reports_each_item_with_its_result(self):
        from app.tts.coqui_parallel import synthesize_parallel

        class Fake:
            def synthesize(self, text, voice, out_path):
                return SynthResult(duration=1.0, words=[WordTiming(text=text, start=0, end=1)])

        seen = []
        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=Fake()):
            synthesize_parallel([("a", "v", Path("a")), ("b", "v", Path("b"))], 1,
                                item_done_cb=lambda i, r: seen.append((i, r.words[0].text)))
        self.assertEqual(seen, [(0, "a"), (1, "b")])

    def test_coqui_oom_retry_maps_indices_back_to_the_original_items(self):
        from app.tts import coqui_parallel

        calls = []

        def fake_run_workers(groups, progress_cb=None, status_cb=None, retry_incomplete=False, item_done_cb=None):
            calls.append(groups)
            outcomes = {}
            for group in groups:
                for idx, *_rest in group:
                    oom = len(calls) == 1 and idx in (1, 3)
                    outcomes[idx] = (not oom, oom, "CUDA out of memory" if oom else None)
                    if not oom:
                        item_done_cb(idx, None)
            return outcomes

        seen = []
        items = [(f"m{i}", "v", Path(f"{i}.mp3")) for i in range(5)]
        with patch.object(coqui_parallel, "_run_workers", side_effect=fake_run_workers):
            # 3 worker: yeniden deneme 2 worker'la yine paralel yolda kalır (1'e düşseydi model yüklenirdi).
            coqui_parallel.synthesize_parallel(items, 3, retry_incomplete=True,
                                               item_done_cb=lambda i, r: seen.append(i))
        self.assertEqual(len(calls), 2, "OOM olan öğeler yeniden denenmeli")
        self.assertEqual(sorted(seen), [0, 1, 2, 3, 4])
        self.assertEqual(seen[-2:], [1, 3], "yeniden denemedeki yerel indeksler asıl indekslere dönmeli")

    def test_coqui_parallel_worker_results_trigger_callbacks(self):
        from app.tts import coqui_parallel
        from tests.test_coqui_parallel import _FakeCtx

        ctx = _FakeCtx()
        self.addCleanup(ctx.stop.set)

        def worker(worker_index, items, result_queue, retry_incomplete, start_event):
            result_queue.put(("status", "loading", worker_index, None))
            result_queue.put(("status", "ready", worker_index, None))
            start_event.wait(5)
            for idx, *_rest in items:
                result_queue.put(("status", "started", worker_index, idx))
                result_queue.put(("result", idx, idx != 4, False, None if idx != 4 else "hata"))

        seen = []
        groups = coqui_parallel._distribute([(f"m{i}", "v", f"{i}.mp3") for i in range(6)], 3)
        with patch.object(coqui_parallel, "_POLL_SECONDS", 0.03):
            coqui_parallel._run_workers(groups, _ctx=ctx, _worker=worker, item_done_cb=lambda i, r: seen.append(i))
        self.assertEqual(sorted(seen), [0, 1, 2, 3, 5], "yalnız başarılı öğeler bildirilmeli")

    def test_chatterbox_retry_maps_indices_back_to_the_original_items(self):
        from app.tts import chatterbox_parallel

        calls = []

        def fake_run_workers(groups, sentence_isolation=False, retry_incomplete=False,
                             progress_cb=None, status_cb=None, item_done_cb=None):
            calls.append(groups)
            outcomes = {}
            for group in groups:
                for idx, *_rest in group:
                    bad = len(calls) == 1 and idx == 2
                    outcomes[idx] = (not bad, bad, "CUDA out of memory" if bad else None)
                    if not bad:
                        item_done_cb(idx, None)
            return outcomes

        seen = []
        items = [(f"m{i}", "v", Path(f"{i}.mp3")) for i in range(4)]
        with patch.object(chatterbox_parallel, "_run_workers", side_effect=fake_run_workers):
            chatterbox_parallel.synthesize_parallel(items, 2, item_done_cb=lambda i, r: seen.append(i))
        self.assertEqual(sorted(seen), [0, 1, 2, 3])
        self.assertEqual(seen[-1], 2)

    def test_remote_reports_each_downloaded_item_with_its_result(self):
        from app.tts.remote import synthesize_remote

        class FakeClient:
            stop = threading.Event()

            def health(self):
                return {"gpu": "T4", "engines": {"coqui": {"available": True}}}

            def synthesize(self, engine, text, voice, out_path, rate):
                return SynthResult(duration=float(text[-1]), words=None)

        seen = {}
        items = [(f"m{i}", "v", Path(f"{i}.mp3")) for i in range(1, 4)]
        results = synthesize_remote(items, "coqui", "+0%", 2, client=FakeClient(),
                                    item_done_cb=lambda i, r: seen.__setitem__(i, r))
        self.assertEqual(sorted(seen), [0, 1, 2])
        self.assertEqual([seen[i] for i in range(3)], results)


class RenderWorkerEmitTests(unittest.TestCase):
    def test_concurrent_emits_never_interleave_lines(self):
        import contextlib
        import io

        from studio_web import render_worker

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            threads = [threading.Thread(target=lambda n=n: [render_worker.emit({"type": "segments", "current": n,
                                                                              "total": 99, "pad": "x" * 500})
                                                            for _ in range(50)])
                       for n in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        lines = buffer.getvalue().splitlines()
        self.assertEqual(len(lines), 400)
        for line in lines:
            self.assertTrue(line.startswith("__DERS_JOB__"))
            json.loads(line[len("__DERS_JOB__"):])


if __name__ == "__main__":
    unittest.main()


class ApiSegmentEventTests(unittest.TestCase):
    """Render worker'ın "segments" olayı işe ikinci ilerleme alanlarını yazar; diğer olaylar
    eskisi gibi işlenir (sıralı modda bu olay hiç gelmez, alanlar da oluşmaz)."""

    @patch("studio_web.api.subprocess.Popen")
    def test_segment_events_update_the_second_progress_fields(self, popen):
        from unittest.mock import MagicMock

        from app.config import CACHE_DIR
        from studio_web import api

        lines = [
            '__DERS_JOB__{"type": "progress", "current": 1, "total": 3, "title": "Seslendiriliyor"}\n',
            '__DERS_JOB__{"type": "segments", "current": 0, "total": 3}\n',
            '__DERS_JOB__{"type": "segments", "current": 2, "total": 3}\n',
            '__DERS_JOB__{"type": "complete", "result": {"videoUrl": "v"}}\n',
        ]
        process = MagicMock()
        process.stdout = iter(lines)
        process.wait.return_value = 0
        process.poll.return_value = 0
        popen.return_value = process

        job_id = api.jobs.create("test-segments", lambda _job: {}, context={})
        time.sleep(0.2)  # işin kendi (boş) çalışması bitsin; alanları biz yazacağız
        result = api._render_in_isolated_process(
            job_id, CACHE_DIR, [Slide(title="T", narration="T")], "edge", "v", "+0%", VideoOptions(),
        )
        job = api.jobs.get(job_id)
        self.assertEqual(result, {"videoUrl": "v"})
        self.assertEqual((job["segmentsDone"], job["segmentsTotal"]), (2, 3))
        self.assertEqual(job["current"], 1)

    def test_api_parses_and_persists_the_option(self):
        from studio_web import api

        source = Path(api.__file__).read_text(encoding="utf-8")
        self.assertIn('overlap_segments=bool(payload.get("overlapSegments", False))', source)
        self.assertIn('"overlap_segments": options.overlap_segments', source)
        self.assertIn('"overlapSegments"))', source)
