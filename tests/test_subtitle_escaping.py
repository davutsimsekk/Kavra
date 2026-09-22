import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import export
from app.models import Slide, WordTiming
from app.video.subtitle import escape_ass_text, write_ass

ZWSP = "\u200b"


class AssEscapingTests(unittest.TestCase):
    def test_plain_text_is_untouched(self):
        self.assertEqual(escape_ass_text("Merhaba dünya, 50% ve 'tırnak'"), "Merhaba dünya, 50% ve 'tırnak'")

    def test_braces_are_escaped_so_they_are_not_override_blocks(self):
        self.assertEqual(escape_ass_text('sözlük {"a": 1} tipi'), 'sözlük \\{"a": 1\\} tipi')

    def test_backslash_sequences_are_no_longer_ass_escapes(self):
        # `\N` satır sonu, `\h` boşluk olarak yorumlanmamalı: ters eğik çizgiden sonra U+200B gelir.
        self.assertEqual(escape_ass_text("C:\\Nesne\\hata"), f"C:\\{ZWSP}Nesne\\{ZWSP}hata")

    def test_backslash_followed_by_brace_keeps_both_literal(self):
        self.assertEqual(escape_ass_text("\\{"), f"\\{ZWSP}\\{{")

    def test_written_dialogue_has_no_unescaped_braces_or_line_break_codes(self):
        text = 'kod {"a": 1} ve C:\\Nesne \\h son'
        words = [WordTiming(text=w, start=i * 0.4, end=i * 0.4 + 0.4) for i, w in enumerate(text.split(" "))]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.ass"
            write_ass(words, path, 1280, 720)
            dialogue = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
        self.assertTrue(dialogue)
        for line in dialogue:
            body = line.split(",", 9)[9]
            self.assertNotRegex(re.sub(r"\\[{}]", "", body), r"[{}]")
            self.assertNotRegex(body, r"\\[Nnh]")


class VttEscapingTests(unittest.TestCase):
    def test_ampersand_and_less_than_are_escaped_in_webvtt(self):
        with patch.object(export, "_full_video_captions", return_value=[(0.0, 1.5, "a < b & c > d")]):
            vtt = export.build_vtt(Path("."), [Slide(title="x")])
        self.assertIn("a &lt; b &amp; c > d", vtt)
        self.assertTrue(vtt.startswith("WEBVTT"))


if __name__ == "__main__":
    unittest.main()
