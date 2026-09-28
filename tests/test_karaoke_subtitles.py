"""Kelime bazlı vurgulama (kayan dolgu/\\kf karaoke altyazı) — bkz. app/video/subtitle.py.

ASS \\k/\\kf semantiği sezgiye ters olduğu için (süre dolana kadar SecondaryColour'da durur,
süre dolunca PrimaryColour'a GEÇER ve kalıcı olur) gerçek ffmpeg+libass render'ıyla ampirik
olarak doğrulandı (bkz. commit notu); burada yalnızca üretilen ASS metninin doğruluğu test edilir."""
import re
import tempfile
import unittest
from pathlib import Path

from app.models import WordTiming
from app.video.subtitle import _ass_color, _DEFAULT_ACCENT_ASS_COLOR, _UNREAD_ASS_COLOR, write_ass


def _dialogue_lines(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]


def _kf_tokens(body: str) -> list[tuple[int, str]]:
    """[(süre_santisaniye, kelime), ...] — sondaki boşluk dahil değil."""
    return [(int(dur), word) for dur, word in re.findall(r"\{\\kf(\d+)\}(\S+)", body)]


class AssColorConversionTests(unittest.TestCase):
    def test_rgb_converts_to_ass_bgr_hex(self):
        # Kavra mint #8ED6B1 -> R=8E G=D6 B=B1 -> ASS &H00B1D68E
        self.assertEqual(_ass_color((0x8E, 0xD6, 0xB1)), "&H00B1D68E")

    def test_black_and_white_round_trip(self):
        self.assertEqual(_ass_color((0, 0, 0)), "&H00000000")
        self.assertEqual(_ass_color((255, 255, 255)), "&H00FFFFFF")

    def test_none_falls_back_to_default_mint_accent(self):
        self.assertEqual(_ass_color(None), _DEFAULT_ACCENT_ASS_COLOR)


class KaraokeLineTimingTests(unittest.TestCase):
    def test_each_word_duration_extends_to_the_next_words_start(self):
        words = [WordTiming(text="bir", start=0.0, end=0.3), WordTiming(text="iki", start=0.5, end=0.7),
                 WordTiming(text="uc", start=1.0, end=1.4)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720)
            tokens = _kf_tokens(_dialogue_lines(path)[0].split(",", 9)[9])
        # "bir": sonraki kelime 0.5'te başlıyor -> 50cs (kendi 0.3sn'lik süresi değil, arayı da kapsar).
        # "iki": sonraki kelime 1.0'da başlıyor -> 50cs. "uc": son kelime, kendi end-start'ı -> 40cs.
        self.assertEqual(tokens, [(50, "bir"), (50, "iki"), (40, "uc")])

    def test_zero_or_negative_span_is_floored_to_minimum(self):
        # Savunma amaçlı: gerçek TTS zamanlamasında olmaz ama üst üste binen/ters sıralı
        # zamanlama libass'i bozmasın diye en az 1 santisaniyeye sabitlenir.
        words = [WordTiming(text="a", start=1.0, end=1.0), WordTiming(text="b", start=0.9, end=1.1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720)
            tokens = _kf_tokens(_dialogue_lines(path)[0].split(",", 9)[9])
        self.assertEqual([dur for dur, _ in tokens], [1, 20])

    def test_words_appear_in_original_order_within_the_line(self):
        words = [WordTiming(text=f"kelime{i}", start=i * 0.3, end=i * 0.3 + 0.25) for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720)
            tokens = _kf_tokens(_dialogue_lines(path)[0].split(",", 9)[9])
        self.assertEqual([w for _, w in tokens], [f"kelime{i}" for i in range(5)])


class WriteAssHeaderTests(unittest.TestCase):
    def test_header_uses_given_accent_as_primary_and_default_unread_as_secondary(self):
        words = [WordTiming(text="x", start=0.0, end=0.5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720, accent=(210, 91, 62))  # "Sıcak Kağıt" teması accent'i
            content = path.read_text(encoding="utf-8")
        style_line = [l for l in content.splitlines() if l.startswith("Style: Default,")][0]
        fields = style_line.split(",")
        self.assertEqual(fields[3], _ass_color((210, 91, 62)))  # PrimaryColour = okunmuş = accent
        self.assertEqual(fields[4], _UNREAD_ASS_COLOR)          # SecondaryColour = henüz okunmamış

    def test_no_accent_given_falls_back_to_default_mint(self):
        words = [WordTiming(text="x", start=0.0, end=0.5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720)
            content = path.read_text(encoding="utf-8")
        style_line = [l for l in content.splitlines() if l.startswith("Style: Default,")][0]
        self.assertEqual(style_line.split(",")[3], _DEFAULT_ACCENT_ASS_COLOR)

    def test_empty_word_list_still_writes_a_valid_header_with_no_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass([], path, 1280, 720, accent=(1, 2, 3))
            content = path.read_text(encoding="utf-8")
            dialogue = _dialogue_lines(path)
        self.assertIn("[V4+ Styles]", content)
        self.assertEqual(dialogue, [])


if __name__ == "__main__":
    unittest.main()
