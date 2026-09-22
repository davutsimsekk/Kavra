"""app.tts.coqui_parallel testleri: dağıtım, OOM tespiti ve geri çekilme
mantığı (worker sayısını yarıya indirip yeniden deneme).

Gerçek multiprocessing/GPU/model KULLANILMAZ — synthesize_parallel'a sahte
bir _run_workers_fn enjekte edilerek yalnızca orkestrasyon mantığı (kaç
worker ile denendi, OOM'da ne oldu, OOM olmayan hatada ne oldu) test edilir.
Gerçek process açma mekanizması bu depoda kapsamlı biçimde elle doğrulandı
(RTX 4060 üzerinde gerçek render'larla) — burada tekrar test edilmiyor.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from app.tts.coqui_parallel import (
    MAX_PARALLEL_WORKERS,
    _distribute,
    _is_oom_text,
    synthesize_parallel,
)


class DistributeTests(unittest.TestCase):
    def test_round_robins_items_across_workers_with_original_index(self):
        items = [("a", "v", "a.mp3"), ("b", "v", "b.mp3"), ("c", "v", "c.mp3"), ("d", "v", "d.mp3")]
        groups = _distribute(items, 2)
        self.assertEqual(groups[0], [(0, "a", "v", "a.mp3"), (2, "c", "v", "c.mp3")])
        self.assertEqual(groups[1], [(1, "b", "v", "b.mp3"), (3, "d", "v", "d.mp3")])

    def test_empty_groups_when_more_workers_than_items(self):
        items = [("a", "v", "a.mp3")]
        groups = _distribute(items, 3)
        self.assertEqual(groups[0], [(0, "a", "v", "a.mp3")])
        self.assertEqual(groups[1], [])
        self.assertEqual(groups[2], [])


class IsOomTextTests(unittest.TestCase):
    def test_detects_common_cuda_oom_phrasings(self):
        self.assertTrue(_is_oom_text("CUDA out of memory. Tried to allocate 512.00 MiB"))
        self.assertTrue(_is_oom_text("RuntimeError: cuda oom"))
        self.assertTrue(_is_oom_text("some CUDA error occurred"))

    def test_does_not_flag_unrelated_errors(self):
        self.assertFalse(_is_oom_text("coqui-tts internal API changed"))
        self.assertFalse(_is_oom_text(""))
        self.assertFalse(_is_oom_text(None))


class _FakeProvider:
    """CoquiTTSProvider'ın gerçek yerine geçen, tek tek üretim çağrılarını
    sayan hafif bir sahte nesne."""

    def __init__(self, result="ok"):
        self.calls = []
        self._result = result

    def synthesize(self, text, voice, out_path, rate="+0%"):
        self.calls.append((text, voice, out_path))
        return self._result


class SynthesizeParallelSequentialFallbackTests(unittest.TestCase):
    def test_n_workers_1_uses_plain_sequential_synthesis(self):
        fake_provider = _FakeProvider("result")

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
            results = synthesize_parallel(items, 1)

        self.assertEqual(len(fake_provider.calls), 2)
        self.assertEqual(results, ["result", "result"])

    def test_sequential_fallback_reports_progress_per_item(self):
        fake_provider = _FakeProvider("result")
        seen = []

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3")), ("c", "v", Path("c.mp3"))]
            synthesize_parallel(items, 1, progress_cb=lambda done, total: seen.append((done, total)))

        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_sequential_fallback_reports_model_and_slide_status(self):
        fake_provider = _FakeProvider("result")
        seen = []

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
            synthesize_parallel(items, 1, status_cb=seen.append)

        self.assertEqual(seen[0], "XTTS modeli yükleniyor")
        self.assertIn("modeli hazır", seen[1])
        self.assertIn("slayt 1/2 başladı", seen[2])

    def test_single_item_uses_plain_sequential_even_if_n_workers_is_higher(self):
        fake_provider = _FakeProvider("ok")

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            results = synthesize_parallel([("a", "v", Path("a.mp3"))], 3)

        self.assertEqual(len(fake_provider.calls), 1)
        self.assertEqual(results, ["ok"])


class SynthesizeParallelOrchestrationTests(unittest.TestCase):
    def test_all_success_returns_a_synthresult_per_item_in_order(self):
        def fake_run_workers(groups, progress_cb=None):
            return {i: (True, False, None) for g in groups for i, *_ in g}

        items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3")), ("c", "v", Path("c.mp3"))]
        results = synthesize_parallel(items, 2, _run_workers_fn=fake_run_workers)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.words is None for r in results))

    def test_run_workers_receives_the_progress_callback_and_it_fires_per_item(self):
        seen = []

        def fake_run_workers(groups, progress_cb=None):
            total = sum(len(g) for g in groups)
            for done in range(1, total + 1):
                progress_cb(done, total)
            return {i: (True, False, None) for g in groups for i, *_ in g}

        items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3")), ("c", "v", Path("c.mp3"))]
        synthesize_parallel(items, 2, _run_workers_fn=fake_run_workers,
                             progress_cb=lambda done, total: seen.append((done, total)))

        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_oom_failure_retries_with_half_the_workers(self):
        # 4 öğeyle: n=3 OOM -> n=2, tekrar OOM -> n=1 dener. n<=1 doğrudan
        # (enjekte edilen sahte çalıştırıcıyı hiç çağırmadan) güvenli sıralı
        # yola düştüğü için, bu son adımda gerçek modelin yüklenmesini
        # önlemek üzere CoquiTTSProvider de ayrıca sahteleniyor.
        attempted_worker_counts = []

        def fake_run_workers(groups, progress_cb=None):
            n = len([g for g in groups if g])
            attempted_worker_counts.append(n)
            return {i: (False, True, "CUDA out of memory") for g in groups for i, *_ in g}

        fake_provider = _FakeProvider("sequential-ok")
        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3")), ("c", "v", Path("c.mp3"))]
            results = synthesize_parallel(items, 3, _run_workers_fn=fake_run_workers)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.words is None for result in results))
        self.assertEqual(attempted_worker_counts, [3, 2])

    def test_oom_failure_eventually_falls_back_to_single_sequential_worker(self):
        def always_oom(groups, progress_cb=None):
            return {i: (False, True, "CUDA out of memory") for g in groups for i, *_ in g}

        fake_provider = _FakeProvider("sequential-ok")

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
            results = synthesize_parallel(items, 2, _run_workers_fn=always_oom)

        # n_workers=2 -> OOM -> retry n=1 -> n<=1 dalı hep-güvenli sıralı yola düşer
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.words is None for result in results))

    def test_partial_worker_failure_retries_only_missing_audio(self):
        attempts = []
        progress = []

        def fake_run_workers(groups, progress_cb=None):
            texts = [text for group in groups for _index, text, _voice, _path in group]
            attempts.append(texts)
            if len(attempts) == 1:
                return {
                    index: (text != "missing", text == "missing", "worker closed" if text == "missing" else None)
                    for group in groups for index, text, _voice, _path in group
                }
            for done in range(1, len(texts) + 1):
                if progress_cb:
                    progress_cb(done, len(texts))
            return {index: (True, False, None) for group in groups for index, *_rest in group}

        items = [
            ("ready-a", "v", Path("a.mp3")),
            ("missing", "v", Path("b.mp3")),
            ("ready-c", "v", Path("c.mp3")),
        ]
        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=_FakeProvider("ok")):
            results = synthesize_parallel(
                items,
                2,
                _run_workers_fn=fake_run_workers,
                progress_cb=lambda done, total: progress.append((done, total)),
            )

        self.assertCountEqual(attempts[0], ["ready-a", "missing", "ready-c"])
        # Tek eksik öğe kaldığında güvenli sıralı yol doğrudan provider kullanır;
        # sahte process çalıştırıcısına ikinci kez gönderilmez.
        self.assertEqual(len(attempts), 1)
        self.assertEqual(progress[-1], (3, 3))
        self.assertEqual(len(results), 3)

    def test_non_oom_failure_raises_instead_of_silently_falling_back(self):
        def fake_run_workers(groups, progress_cb=None):
            return {i: (False, False, "coqui-tts internal API değişti") for g in groups for i, *_ in g}

        items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
        with self.assertRaises(RuntimeError):
            synthesize_parallel(items, 2, _run_workers_fn=fake_run_workers)

    def test_n_workers_is_clamped_to_the_hard_safety_ceiling(self):
        seen_group_counts = []

        def fake_run_workers(groups, progress_cb=None):
            seen_group_counts.append(len([g for g in groups if g]))
            return {i: (True, False, None) for g in groups for i, *_ in g}

        items = [("a", "v", Path(f"{i}.mp3")) for i in range(10)]
        synthesize_parallel(items, MAX_PARALLEL_WORKERS + 5, _run_workers_fn=fake_run_workers)

        self.assertLessEqual(seen_group_counts[0], MAX_PARALLEL_WORKERS)


if __name__ == "__main__":
    unittest.main()
