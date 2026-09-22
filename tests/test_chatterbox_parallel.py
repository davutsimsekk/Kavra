"""Chatterbox worker bölüştürme ve OOM geri çekilme testleri.

Gerçek model/GPU açılmaz; yalıtılmış Python worker'ları sahte çalıştırıcıyla
değiştirilir.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tts.chatterbox_parallel import (
    MAX_PARALLEL_WORKERS,
    _chatterbox_python,
    _distribute,
    synthesize_parallel,
)


class ChatterboxParallelTests(unittest.TestCase):
    def test_configured_python_path_supports_container_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = Path(tmp) / "bin" / "python"
            python.parent.mkdir()
            python.touch()
            with patch.dict(os.environ, {"CHATTERBOX_PYTHON": str(python)}):
                self.assertEqual(_chatterbox_python(), python)

    def test_missing_configured_python_fails_clearly(self):
        with patch.dict(os.environ, {"CHATTERBOX_PYTHON": "/missing/kavra-python"}):
            with self.assertRaisesRegex(RuntimeError, "CHATTERBOX_PYTHON bulunamadı"):
                _chatterbox_python()

    def test_round_robin_keeps_original_slide_indices(self):
        groups = _distribute([("a", "v", "a.mp3"), ("b", "v", "b.mp3"), ("c", "v", "c.mp3")], 2)
        self.assertEqual(groups[0], [(0, "a", "v", "a.mp3"), (2, "c", "v", "c.mp3")])
        self.assertEqual(groups[1], [(1, "b", "v", "b.mp3")])

    def test_success_returns_one_result_per_slide_and_reports_progress(self):
        seen = []

        def fake_run(groups, progress_cb=None):
            total = sum(len(group) for group in groups)
            for done in range(1, total + 1):
                progress_cb(done, total)
            return {index: (True, False, None) for group in groups for index, *_rest in group}

        results = synthesize_parallel(
            [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))],
            2,
            _run_workers_fn=fake_run,
            progress_cb=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(seen, [(1, 2), (2, 2)])

    def test_partial_worker_failure_retries_only_missing_audio(self):
        attempts = []
        progress = []

        def fake_run(groups, progress_cb=None):
            texts = [text for group in groups for _index, text, _voice, _path in group]
            attempts.append(texts)
            if len(attempts) == 1:
                outcomes = {}
                for group in groups:
                    for index, text, _voice, _path in group:
                        outcomes[index] = (text != "missing", text == "missing", "worker closed" if text == "missing" else None)
                return outcomes
            for done in range(1, len(texts) + 1):
                if progress_cb:
                    progress_cb(done, len(texts))
            return {index: (True, False, None) for group in groups for index, *_rest in group}

        results = synthesize_parallel(
            [
                ("ready-a", "v", Path("a.mp3")),
                ("missing", "v", Path("b.mp3")),
                ("ready-c", "v", Path("c.mp3")),
            ],
            2,
            _run_workers_fn=fake_run,
            progress_cb=lambda done, total: progress.append((done, total)),
        )

        self.assertCountEqual(attempts[0], ["ready-a", "missing", "ready-c"])
        self.assertEqual(attempts[1], ["missing"])
        self.assertEqual(progress[-1], (3, 3))
        self.assertEqual(len(results), 3)

    def test_oom_with_two_models_retries_once_with_single_model(self):
        attempts = []

        def fake_run(groups, progress_cb=None):
            workers = len([group for group in groups if group])
            attempts.append(workers)
            if workers == 2:
                return {index: (False, True, "CUDA out of memory") for group in groups for index, *_rest in group}
            return {index: (True, False, None) for group in groups for index, *_rest in group}

        results = synthesize_parallel(
            [("a", "v", Path("a.mp3")), ("b", "v", Path("b.mp3"))],
            MAX_PARALLEL_WORKERS,
            _run_workers_fn=fake_run,
        )
        self.assertEqual(attempts, [2, 1])
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
