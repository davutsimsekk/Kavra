"""NVENC donanım kodlama tespiti/düşüşü — bkz. app/video/video_builder.py.

_encoder_cache süreç ömrü boyunca paylaşılan bir modül değişkeni olduğundan her test onu
kendi değeriyle patch'ler; testler birbirinin tespit sonucunu miras almaz.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import VideoOptions
from app.video import video_builder as vb


class EncoderProbeTests(unittest.TestCase):
    def test_successful_probe_returns_nvenc(self):
        ok = MagicMock(returncode=0)
        with patch.object(vb.subprocess, "run", return_value=ok), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat", return_value=MagicMock(st_size=100)):
            self.assertEqual(vb._probe_hardware_encoder(), "h264_nvenc")

    def test_nonzero_exit_falls_back_to_libx264(self):
        bad = MagicMock(returncode=1)
        with patch.object(vb.subprocess, "run", return_value=bad):
            self.assertEqual(vb._probe_hardware_encoder(), "libx264")

    def test_missing_ffmpeg_falls_back_without_raising(self):
        with patch.object(vb.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(vb._probe_hardware_encoder(), "libx264")

    def test_timeout_falls_back_without_raising(self):
        with patch.object(vb.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 15)):
            self.assertEqual(vb._probe_hardware_encoder(), "libx264")

    def test_empty_output_file_is_not_trusted_as_success(self):
        ok = MagicMock(returncode=0)
        with patch.object(vb.subprocess, "run", return_value=ok), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.stat", return_value=MagicMock(st_size=0)):
            self.assertEqual(vb._probe_hardware_encoder(), "libx264")


class PickEncoderCachingTests(unittest.TestCase):
    def test_probe_runs_once_and_result_is_cached(self):
        with patch.object(vb, "_encoder_cache", None), \
             patch.object(vb, "_probe_hardware_encoder", return_value="h264_nvenc") as probe:
            first = vb.pick_video_encoder()
            second = vb.pick_video_encoder()
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(first, ("h264_nvenc", vb._NVENC_ARGS))

    def test_cached_libx264_result_is_reused(self):
        with patch.object(vb, "_encoder_cache", "libx264"), \
             patch.object(vb, "_probe_hardware_encoder") as probe:
            self.assertEqual(vb.pick_video_encoder(), ("libx264", vb._X264_ARGS))
            probe.assert_not_called()

    def test_quality_profile_selects_matching_encoder_arguments(self):
        with patch.object(vb, "_encoder_cache", "libx264"):
            encoder, args = vb.pick_video_encoder("fast")
        self.assertEqual(encoder, "libx264")
        self.assertIn("veryfast", args)
        self.assertIn("23", args)


class BuildSegmentFallbackTests(unittest.TestCase):
    """build_segment'ın gerçek ffmpeg çağırdığı kısmı burada mock'lanır; gerçek encode
    test_video_themes.py'de zaten yapılıyor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "slide.png"
        self.audio = Path(self.tmp.name) / "a.mp3"
        self.image.write_bytes(b"x")
        self.audio.write_bytes(b"x")
        self.out = Path(self.tmp.name) / "seg.mp4"

    def test_nvenc_runtime_failure_retries_with_libx264_without_poisoning_the_cache(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            encoder = cmd[cmd.index("-c:v") + 1]
            return MagicMock(returncode=0 if encoder == "libx264" else 1, stderr="NVENC oturum sınırı")

        with patch.object(vb, "_encoder_cache", "h264_nvenc"), \
             patch.object(vb.subprocess, "run", side_effect=fake_run):
            vb.build_segment(self.image, self.audio, 2.0, self.out, VideoOptions())
            # Tek seferlik arıza sonraki segmentin yine NVENC denemesini engellememeli
            # (patch.object with-bloğu bitince eski değeri geri yükler; kontrol içeride olmalı).
            self.assertEqual(vb._encoder_cache, "h264_nvenc")

        self.assertEqual([c[c.index("-c:v") + 1] for c in calls], ["h264_nvenc", "libx264"])

    def test_both_encoders_failing_raises_with_ffmpeg_output(self):
        bad = MagicMock(returncode=1, stderr="ffmpeg tamamen bozuk")
        with patch.object(vb, "_encoder_cache", "h264_nvenc"), \
             patch.object(vb.subprocess, "run", return_value=bad):
            with self.assertRaises(RuntimeError) as raised:
                vb.build_segment(self.image, self.audio, 2.0, self.out, VideoOptions())
        self.assertIn("ffmpeg tamamen bozuk", str(raised.exception))

    def test_libx264_failure_does_not_retry_a_second_time(self):
        """libx264 zaten en güvenli düşüş; başarısız olursa tekrar denemeden hemen hata verilmeli."""
        bad = MagicMock(returncode=1, stderr="disk dolu")
        with patch.object(vb, "_encoder_cache", "libx264"), \
             patch.object(vb.subprocess, "run", return_value=bad) as run:
            with self.assertRaises(RuntimeError):
                vb.build_segment(self.image, self.audio, 2.0, self.out, VideoOptions())
        self.assertEqual(run.call_count, 1)

    def test_high_quality_command_normalizes_audio_and_marks_bt709(self):
        ok = MagicMock(returncode=0)
        with patch.object(vb, "_encoder_cache", "libx264"), \
             patch.object(vb.subprocess, "run", return_value=ok) as run:
            vb.build_segment(self.image, self.audio, 2.0, self.out, VideoOptions(quality_preset="high"))
        cmd = run.call_args[0][0]
        self.assertIn(vb._NARRATION_AUDIO_FILTER, cmd)
        self.assertNotIn("afade", " ".join(cmd))
        self.assertIn("bt709", cmd)
        self.assertIn("+faststart", cmd)
        self.assertIn("slow", cmd)
        self.assertIn("lanczos", cmd[cmd.index("-vf") + 1])

    def test_ken_burns_is_centered_and_uses_high_resolution_source(self):
        ok = MagicMock(returncode=0)
        with patch.object(vb, "_encoder_cache", "libx264"), \
             patch.object(vb.subprocess, "run", return_value=ok) as run:
            vb.build_segment(self.image, self.audio, 2.0, self.out, VideoOptions(ken_burns=True))
        vf = run.call_args[0][0][run.call_args[0][0].index("-vf") + 1]
        self.assertIn("scale=3840:2160:flags=lanczos", vf)
        self.assertIn("iw/2-(iw/zoom/2)", vf)


class BuildSegmentRevealTests(unittest.TestCase):
    """Aşamalı madde gösterimi (bkz. app/pipeline.py _build_bullet_reveal_stages) etkinken
    build_segment tek görsel yerine çoklu-girişli bir filter_complex kuruyor; burada da
    gerçek ffmpeg çağrısı mock'lanır, yalnızca kurulan komutun şekli doğrulanır."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage1 = Path(self.tmp.name) / "s1.png"
        self.stage2 = Path(self.tmp.name) / "s2.png"
        self.stage1.write_bytes(b"x")
        self.stage2.write_bytes(b"x")
        self.audio = Path(self.tmp.name) / "a.mp3"
        self.audio.write_bytes(b"x")
        self.image = Path(self.tmp.name) / "unused.png"  # reveal modunda hiç dokunulmaz
        self.out = Path(self.tmp.name) / "seg.mp4"

    def test_reveal_stages_build_a_multi_input_concat_filter_complex(self):
        ok = MagicMock(returncode=0)
        with patch.object(vb, "_encoder_cache", "libx264"), \
             patch.object(vb.subprocess, "run", return_value=ok) as run:
            vb.build_segment(
                self.image, self.audio, 4.0, self.out, VideoOptions(),
                reveal_stages=[(self.stage1, 2.0), (self.stage2, 2.0)],
            )
        cmd = run.call_args[0][0]
        self.assertEqual(cmd.count("-i"), 3)  # 2 aşama görseli + 1 ses
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("concat=n=2:v=1:a=0", filter_complex)
        self.assertIn("[vout]", cmd)
        self.assertIn("2:a", cmd)  # ses girişi son index (0,1 aşama görselleri, 2 ses)

    def test_reveal_nvenc_failure_still_retries_with_libx264(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            encoder = cmd[cmd.index("-c:v") + 1]
            return MagicMock(returncode=0 if encoder == "libx264" else 1, stderr="NVENC hatası")

        with patch.object(vb, "_encoder_cache", "h264_nvenc"), \
             patch.object(vb.subprocess, "run", side_effect=fake_run):
            vb.build_segment(
                self.image, self.audio, 4.0, self.out, VideoOptions(),
                reveal_stages=[(self.stage1, 2.0), (self.stage2, 2.0)],
            )
        self.assertEqual([c[c.index("-c:v") + 1] for c in calls], ["h264_nvenc", "libx264"])


class ChaptersFileTests(unittest.TestCase):
    """bkz. app/pipeline.py render_video — "chapter" seviyeli slaytlar bu ffmetadata
    dosyası aracılığıyla final videoya VLC/mpv'nin gösterdiği atlanabilir bölüm
    işaretleri olarak gömülüyor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "chapters.txt"

    def test_writes_ffmetadata_header_and_chapter_blocks(self):
        vb.write_chapters_file([(0.0, "Giriş"), (12.5, "Pointer'lar")], 20.0, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(";FFMETADATA1"))
        self.assertEqual(text.count("[CHAPTER]"), 2)
        self.assertIn("START=0", text)
        self.assertIn("START=12500", text)
        self.assertIn("title=Giriş", text)
        self.assertIn("title=Pointer'lar", text)

    def test_each_chapter_ends_where_the_next_one_starts(self):
        vb.write_chapters_file([(0.0, "A"), (5.0, "B"), (9.0, "C")], 15.0, self.path)
        text = self.path.read_text(encoding="utf-8")
        blocks = text.split("[CHAPTER]")[1:]
        self.assertIn("END=5000", blocks[0])
        self.assertIn("END=9000", blocks[1])
        self.assertIn("END=15000", blocks[2])  # son bölüm toplam süreye kadar sürer

    def test_special_characters_in_title_are_escaped(self):
        vb.write_chapters_file([(0.0, "A=B; C#D\\E")], 10.0, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("title=A\\=B\\; C\\#D\\\\E", text)

    def test_single_chapter_still_gets_a_valid_nonzero_length_block(self):
        vb.write_chapters_file([(3.0, "Tek bölüm")], 3.0, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("START=3000", text)
        self.assertIn("END=3010", text)  # total_duration == start olsa da en az 10ms uzunluk garanti


class ConcatVideosChaptersTests(unittest.TestCase):
    """concat_videos'a chapters_file verildiğinde ffmpeg komutuna metadata girişi ve
    -map_metadata eklendiğini doğrular — gerçek ffmpeg burada mock'lanır (segment
    dosyaları gerçek değil), gerçek gömme test_pass3_parallel.py'de uçtan uca doğrulanır."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)
        self.seg = self.tmpdir / "seg1.mp4"
        self.seg.write_bytes(b"x")
        self.out = self.tmpdir / "out.mp4"
        self.list_file = self.tmpdir / "list.txt"

    def test_no_chapters_file_omits_metadata_flags(self):
        with patch.object(vb.subprocess, "run") as run:
            vb.concat_videos([self.seg], self.out, self.list_file)
        cmd = run.call_args[0][0]
        self.assertNotIn("-map_metadata", cmd)

    def test_chapters_file_adds_metadata_input_and_mapping(self):
        chapters_file = self.tmpdir / "chapters.txt"
        chapters_file.write_text(";FFMETADATA1\n", encoding="utf-8")
        with patch.object(vb.subprocess, "run") as run:
            vb.concat_videos([self.seg], self.out, self.list_file, chapters_file=chapters_file)
        cmd = run.call_args[0][0]
        self.assertIn("-map_metadata", cmd)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "1")
        self.assertIn(str(chapters_file), cmd)

    def test_nonexistent_chapters_file_is_silently_ignored(self):
        missing = self.tmpdir / "does_not_exist.txt"
        with patch.object(vb.subprocess, "run") as run:
            vb.concat_videos([self.seg], self.out, self.list_file, chapters_file=missing)
        cmd = run.call_args[0][0]
        self.assertNotIn("-map_metadata", cmd)


if __name__ == "__main__":
    unittest.main()
