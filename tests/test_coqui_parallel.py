"""app.tts.coqui_parallel testleri: dağıtım, OOM tespiti ve geri çekilme
mantığı (worker sayısını yarıya indirip yeniden deneme).

Gerçek multiprocessing/GPU/model KULLANILMAZ — synthesize_parallel'a sahte
bir _run_workers_fn enjekte edilerek yalnızca orkestrasyon mantığı (kaç
worker ile denendi, OOM'da ne oldu, OOM olmayan hatada ne oldu) test edilir.
Gerçek process açma mekanizması bu depoda kapsamlı biçimde elle doğrulandı
(RTX 4060 üzerinde gerçek render'larla) — burada tekrar test edilmiyor.
"""

import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tts.coqui_parallel import (
    MAX_PARALLEL_WORKERS,
    _distribute,
    _is_oom_text,
    _run_workers,
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

    def test_detects_host_memory_pressure_during_model_loading(self):
        # Windows'ta RAM/sayfa dosyası yetmediğinde model yükleme bu metinlerle düşer;
        # bunlar "daha az worker'la yeniden denenebilir" sayılmalı.
        self.assertTrue(_is_oom_text("OSError: [WinError 1455] The paging file is too small"))
        self.assertTrue(_is_oom_text("MemoryError: not enough memory"))
        self.assertTrue(_is_oom_text("[enforce fail at alloc_cpu.cpp] DefaultCPUAllocator: not enough memory"))


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
            results = synthesize_parallel(
                items, 3, _run_workers_fn=fake_run_workers, retry_incomplete=True,
            )

        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.words is None for result in results))
        self.assertEqual(attempted_worker_counts, [3, 2])

    def test_oom_failure_eventually_falls_back_to_single_sequential_worker(self):
        def always_oom(groups, progress_cb=None):
            return {i: (False, True, "CUDA out of memory") for g in groups for i, *_ in g}

        fake_provider = _FakeProvider("sequential-ok")

        with patch("app.tts.coqui_provider.CoquiTTSProvider", return_value=fake_provider):
            items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
            results = synthesize_parallel(
                items, 2, _run_workers_fn=always_oom, retry_incomplete=True,
            )

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
                retry_incomplete=True,
            )

        self.assertCountEqual(attempts[0], ["ready-a", "missing", "ready-c"])
        # Tek eksik öğe kaldığında güvenli sıralı yol doğrudan provider kullanır;
        # sahte process çalıştırıcısına ikinci kez gönderilmez.
        self.assertEqual(len(attempts), 1)
        self.assertEqual(progress[-1], (3, 3))
        self.assertEqual(len(results), 3)

    def test_oom_retry_is_disabled_by_default(self):
        def always_oom(groups, progress_cb=None):
            return {i: (False, True, "CUDA out of memory") for g in groups for i, *_ in g}

        items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]
        with self.assertRaisesRegex(RuntimeError, "out of memory"):
            synthesize_parallel(items, 2, _run_workers_fn=always_oom)

    @patch("app.tts.coqui_parallel._run_workers")
    def test_optional_retry_flag_reaches_real_worker_runner(self, run_workers):
        run_workers.return_value = {0: (True, False, None), 1: (True, False, None)}
        items = [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))]

        synthesize_parallel(items, 2, retry_incomplete=True)

        self.assertTrue(run_workers.call_args.kwargs["retry_incomplete"])

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


class _FakeProcess:
    """Gerçek process yerine bir iş parçacığı çalıştıran, mp.Process arayüzünü taklit eden sahte."""

    def __init__(self, target, args, stop):
        self._target, self._args, self._stop = target, args, stop
        self.exitcode = None
        self.pid = id(self)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self._target(*self._args)
            if self.exitcode is None:
                self.exitcode = 0
        except BaseException:
            self.exitcode = 1

    def start(self):
        self._thread.start()

    def is_alive(self):
        return self._thread.is_alive()

    def terminate(self):
        self._stop.set()
        if self.exitcode is None:
            self.exitcode = -15

    def join(self, timeout=None):
        self._thread.join(timeout)


class _FakeCtx:
    def __init__(self):
        self.stop = threading.Event()
        self.Queue = queue.Queue
        self.Event = threading.Event

    def Process(self, target, args):
        return _FakeProcess(target, args, self.stop)


class RunWorkersOrchestrationTests(unittest.TestCase):
    """Gerçek _run_workers orkestrasyonu (bariyer, yükleme hatası, ölen worker, zaman aşımı) —
    process/GPU/model açılmadan, iş parçacıklı sahte bir bağlamla.

    Bu sınıf gerçek bir kullanıcı hatasının regresyon testidir: eskiden bir worker modeli
    YÜKLEYEMEZSE (bellek yetersizliği vb.) hiç "hazır" olayı gelmiyor, bariyer (start_event)
    hiç açılmıyor, önceki worker'lar sonsuza dek bekliyor ve ana döngü sonsuza dek dönüyordu —
    render "0/N slayt"ta sonsuza dek takılı kalıyordu. Her test bir gözcü süresiyle çalışır:
    orkestrasyon sonsuz beklerse test AÇIKÇA başarısız olur, sessizce asılı kalmaz."""

    def setUp(self):
        patcher = patch("app.tts.coqui_parallel._POLL_SECONDS", 0.03)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ctx = _FakeCtx()
        self.addCleanup(self.ctx.stop.set)
        self.log: list[tuple[str, int]] = []
        self.statuses: list[str] = []

    def _groups(self, item_count=6, workers=3):
        items = [(f"metin{i}", "v", f"{i}.mp3") for i in range(item_count)]
        return _distribute(items, workers)

    def _worker(self, behaviors):
        ctx, log = self.ctx, self.log

        def worker(worker_index, items, result_queue, retry_incomplete, start_event):
            behavior = behaviors.get(worker_index, "ok")
            log.append(("spawned", worker_index))
            result_queue.put(("status", "loading", worker_index, None))
            if behavior == "load_fail":
                result_queue.put(("status", "load_failed", worker_index, "RuntimeError: not enough memory"))
                return
            if behavior == "load_fail_config":
                result_queue.put(("status", "load_failed", worker_index, "ImportError: TTS kurulu değil"))
                return
            if behavior == "die_before_ready":
                return
            if behavior == "load_forever":
                while not ctx.stop.wait(0.02):
                    pass
                return
            result_queue.put(("status", "ready", worker_index, None))
            log.append(("ready", worker_index))
            while not start_event.wait(0.02):
                if ctx.stop.is_set():
                    return
            log.append(("start", worker_index))
            if behavior == "die_after_start":
                return
            if behavior == "hang_after_start":
                while not ctx.stop.wait(0.02):
                    pass
                return
            for idx, *_rest in items:
                if ctx.stop.is_set():
                    return
                result_queue.put(("status", "started", worker_index, idx))
                result_queue.put(("result", idx, True, False, None))

        return worker

    def _run(self, behaviors, retry=False, watchdog=10.0, **kwargs):
        holder = {}

        def target():
            holder["outcomes"] = _run_workers(
                self._groups(**kwargs), status_cb=self.statuses.append,
                retry_incomplete=retry, _ctx=self.ctx, _worker=self._worker(behaviors),
            )

        runner = threading.Thread(target=target, daemon=True)
        runner.start()
        runner.join(watchdog)
        self.assertFalse(runner.is_alive(), "orkestrasyon sonsuza dek bekledi (0/N'de takılma)")
        return holder["outcomes"]

    def test_all_workers_load_then_start_together_and_finish(self):
        outcomes = self._run({})
        self.assertEqual(len(outcomes), 6)
        self.assertTrue(all(ok for ok, _oom, _detail in outcomes.values()))
        events = [name for name, _n in self.log]
        # Bariyer: HİÇBİR worker üretime, hepsi hazır olmadan başlamamalı.
        last_ready = max(i for i, name in enumerate(events) if name == "ready")
        first_start = min(i for i, name in enumerate(events) if name == "start")
        self.assertLess(last_ready, first_start)
        self.assertIn("3/3 hazır", self.statuses[-1] if "hazır" in self.statuses[-1] else
                      next(s for s in self.statuses if "3/3 hazır" in s))

    def test_second_worker_load_failure_stops_the_run_instead_of_hanging(self):
        outcomes = self._run({2: "load_fail"})
        self.assertEqual(len(outcomes), 6)
        self.assertFalse(any(ok for ok, _oom, _detail in outcomes.values()))
        # İlk kaydedilen hata KÖK NEDEN olmalı (RuntimeError için failures[0] gösterilir).
        first_detail = next(iter(outcomes.values()))[2]
        self.assertIn("worker 2 modeli yükleyemedi", first_detail)
        self.assertIn("not enough memory", first_detail)
        # 3. worker'ın modeli hiç yüklenmeye çalışılmamalı.
        self.assertNotIn(("spawned", 3), self.log)

    def test_with_retry_the_ready_worker_finishes_and_the_rest_is_recoverable(self):
        outcomes = self._run({2: "load_fail"}, retry=True)
        # 1. worker (idx 0 ve 3) hazırdı ve işini bitirir.
        self.assertTrue(outcomes[0][0])
        self.assertTrue(outcomes[3][0])
        # 2. worker yükleyemedi, 3. worker hiç başlatılmadı: eksikler "yeniden denenebilir".
        for idx in (1, 4, 2, 5):
            ok, recoverable, detail = outcomes[idx]
            self.assertFalse(ok)
            self.assertTrue(recoverable)
        self.assertNotIn(("spawned", 3), self.log)

    def test_non_memory_load_failure_of_first_worker_is_not_recoverable(self):
        # Kurulum/yapılandırma hatası az worker'la tekrar denenince düzelmez — açıkça çıksın.
        outcomes = self._run({1: "load_fail_config"})
        ok, recoverable, detail = outcomes[0]
        self.assertFalse(ok)
        self.assertFalse(recoverable)
        self.assertIn("TTS kurulu değil", detail)

    def test_worker_that_exits_silently_before_ready_does_not_hang_the_others(self):
        outcomes = self._run({2: "die_before_ready"})
        self.assertEqual(len(outcomes), 6)
        self.assertTrue(any("sonuç üretmeden kapandı" in (d or "") for _ok, _r, d in outcomes.values()))

    def test_worker_that_dies_mid_run_only_loses_its_own_items(self):
        outcomes = self._run({2: "die_after_start"}, retry=True)
        self.assertTrue(outcomes[0][0] and outcomes[3][0])   # 1. worker
        self.assertTrue(outcomes[2][0] and outcomes[5][0])   # 3. worker
        for idx in (1, 4):                                    # 2. worker sonuçsuz öldü
            ok, recoverable, detail = outcomes[idx]
            self.assertFalse(ok)
            self.assertTrue(recoverable)
            self.assertIn("sonuç üretmeden kapandı", detail)

    def test_model_load_that_never_finishes_times_out_instead_of_hanging(self):
        with patch("app.tts.coqui_parallel.LOAD_TIMEOUT_SECONDS", 0.25):
            outcomes = self._run({2: "load_forever"})
        self.assertFalse(any(ok for ok, _oom, _detail in outcomes.values()))
        self.assertTrue(any("zaman aşımı" in (d or "") for _ok, _r, d in outcomes.values()))

    def test_generation_that_makes_no_progress_times_out_instead_of_hanging(self):
        with patch("app.tts.coqui_parallel.STALL_TIMEOUT_SECONDS", 0.25):
            outcomes = self._run({1: "hang_after_start", 2: "hang_after_start", 3: "hang_after_start"})
        self.assertFalse(any(ok for ok, _oom, _detail in outcomes.values()))
        self.assertTrue(any("hiç ilerlemedi" in (d or "") for _ok, _r, d in outcomes.values()))

    def test_load_failure_surfaces_as_a_clear_error_from_synthesize_parallel(self):
        def real_runner_with_fake_workers(groups, progress_cb=None, status_cb=None, retry_incomplete=False):
            return _run_workers(groups, progress_cb, status_cb, retry_incomplete,
                                _ctx=self.ctx, _worker=self._worker({2: "load_fail"}))

        items = [(f"m{i}", "v", Path(f"{i}.mp3")) for i in range(6)]
        with patch("app.tts.coqui_parallel._run_workers", side_effect=real_runner_with_fake_workers):
            with self.assertRaisesRegex(RuntimeError, "worker 2 modeli yükleyemedi"):
                synthesize_parallel(items, 3)


if __name__ == "__main__":
    unittest.main()


class MultiGpuAndLimitTests(unittest.TestCase):
    def test_max_workers_defaults_to_three_and_can_be_raised_for_cloud_gpus(self):
        from app.tts import coqui_parallel as cp

        self.assertEqual(cp._max_workers_from_env({}), 3)
        self.assertEqual(cp._max_workers_from_env({"KAVRA_COQUI_MAX_WORKERS": "7"}), 7)
        self.assertEqual(cp._max_workers_from_env({"KAVRA_COQUI_MAX_WORKERS": "999"}), cp.HARD_MAX_PARALLEL_WORKERS)
        for bad in ("0", "-2", "abc", " "):
            with self.subTest(bad=bad):
                self.assertEqual(cp._max_workers_from_env({"KAVRA_COQUI_MAX_WORKERS": bad}), 3)

    def test_workers_are_spread_round_robin_across_gpus(self):
        import sys
        import types

        from app.tts import coqui_parallel as cp

        chosen = []
        cuda = types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 2, set_device=chosen.append)
        with patch.dict(sys.modules, {"torch": types.SimpleNamespace(cuda=cuda)}):
            devices = [cp._select_cuda_device(worker) for worker in (1, 2, 3, 4, 5)]
        self.assertEqual(devices, [0, 1, 0, 1, 0])
        self.assertEqual(chosen, [0, 1, 0, 1, 0])

    def test_single_gpu_keeps_every_worker_on_device_zero(self):
        import sys
        import types

        from app.tts import coqui_parallel as cp

        cuda = types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, set_device=lambda _d: None)
        with patch.dict(sys.modules, {"torch": types.SimpleNamespace(cuda=cuda)}):
            self.assertEqual([cp._select_cuda_device(w) for w in (1, 2, 3)], [0, 0, 0])
        cpu = types.SimpleNamespace(is_available=lambda: False)
        with patch.dict(sys.modules, {"torch": types.SimpleNamespace(cuda=cpu)}):
            self.assertIsNone(cp._select_cuda_device(1))
