"""LLM'in içeriğe göre seçtiği slayt formatları (bkz. app/models.py Slide.layout,
app/video/slide_renderer.py _CONTENT_LAYOUTS, prompts/lecture_script_prompt.md).

Render tarafı LLM çıktısına güvenmez: tanınmayan bir "layout" değeri ya da o formatın
beklediği yapıya uymayan "bullets" içeriği her zaman düz madde listesine (bullets) düşer,
asla istisna fırlatmaz — bu dosyadaki testlerin çoğu tam olarak bunu doğruluyor."""
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.config import CACHE_DIR
from app.models import Slide
from app.video.slide_renderer import (
    _CONTENT_LAYOUTS,
    _parse_comparison_columns,
    _parse_definition_pairs,
    render_slide,
)


class ParseDefinitionPairsTests(unittest.TestCase):
    def test_valid_pairs_are_parsed_and_trimmed(self):
        bullets = ["Pointer :  Bir değişkenin adresini tutan değişken", "Dereference: Adresteki değeri okumak"]
        self.assertEqual(
            _parse_definition_pairs(bullets),
            [("Pointer", "Bir değişkenin adresini tutan değişken"), ("Dereference", "Adresteki değeri okumak")],
        )

    def test_more_than_three_pairs_is_rejected(self):
        bullets = [f"Terim{i}: Tanım{i}" for i in range(4)]
        self.assertIsNone(_parse_definition_pairs(bullets))

    def test_empty_list_is_rejected(self):
        self.assertIsNone(_parse_definition_pairs([]))

    def test_missing_colon_is_rejected(self):
        self.assertIsNone(_parse_definition_pairs(["Pointer bir adres saklar"]))

    def test_empty_term_or_definition_is_rejected(self):
        self.assertIsNone(_parse_definition_pairs([": boş terim"]))
        self.assertIsNone(_parse_definition_pairs(["Boş tanım: "]))

    def test_colon_inside_definition_uses_first_colon_only(self):
        # split(":", 1) -> yalnızca ilk ':' ayraç sayılır, tanımın içindeki ':' bozmaz.
        self.assertEqual(
            _parse_definition_pairs(["O(n): karmaşıklık, n: eleman sayısı"]),
            [("O(n)", "karmaşıklık, n: eleman sayısı")],
        )


class ParseComparisonColumnsTests(unittest.TestCase):
    def test_valid_split_returns_both_sides(self):
        bullets = ["Yığın (Stack)", "Hızlı erişim", "---", "Öbek (Heap)", "Esnek boyut", "Elle yönetilir"]
        left, right = _parse_comparison_columns(bullets)
        self.assertEqual(left, ["Yığın (Stack)", "Hızlı erişim"])
        self.assertEqual(right, ["Öbek (Heap)", "Esnek boyut", "Elle yönetilir"])

    def test_missing_separator_is_rejected(self):
        self.assertIsNone(_parse_comparison_columns(["A", "B", "C"]))

    def test_multiple_separators_are_rejected(self):
        self.assertIsNone(_parse_comparison_columns(["A", "---", "B", "---", "C"]))

    def test_empty_side_is_rejected(self):
        self.assertIsNone(_parse_comparison_columns(["---", "Sağ başlık"]))
        self.assertIsNone(_parse_comparison_columns(["Sol başlık", "---"]))


class SlideLayoutModelTests(unittest.TestCase):
    def test_default_layout_is_bullets(self):
        self.assertEqual(Slide(title="x").layout, "bullets")

    def test_round_trips_through_to_dict_and_from_dict(self):
        slide = Slide(title="x", layout="emphasis", bullets=["Tek cümle"])
        self.assertEqual(Slide.from_dict(slide.to_dict()).layout, "emphasis")

    def test_old_saved_data_without_layout_key_defaults_to_bullets(self):
        old_style = {"title": "Eski proje slaydı", "bullets": ["a", "b"]}
        self.assertEqual(Slide.from_dict(old_style).layout, "bullets")

    def test_explicit_null_layout_falls_back_to_bullets(self):
        self.assertEqual(Slide.from_dict({"title": "x", "layout": None}).layout, "bullets")


class LayoutRenderSmokeTests(unittest.TestCase):
    """Her format gerçek PIL ile render edilip görselin bozulmadığı doğrulanır — bkz.
    tests/test_video_themes.py::ThemeRenderTests ile aynı desen."""

    def setUp(self):
        (CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=CACHE_DIR / "tmp")
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _render(self, slide: Slide, name: str):
        out = self.out_dir / f"{name}.png"
        render_slide(slide, 1, 3, "TEST", out, theme_preset="notebook")
        self.assertTrue(out.exists())
        with Image.open(out) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.mode, "RGB")

    def test_all_known_layouts_render_without_crashing(self):
        samples = {
            "bullets": ["Birinci madde", "İkinci madde", "Üçüncü madde"],
            "emphasis": ["Pointer, bir değişkenin bellekteki adresini tutan özel bir değişkendir."],
            "definition": ["Pointer: Bir değişkenin adresini tutan değişken", "NULL: Geçersiz adres değeri"],
            "comparison": ["Yığın (Stack)", "Hızlı erişim", "---", "Öbek (Heap)", "Esnek boyut"],
            "process": ["Adresi al", "Pointer'a ata", "Dereference et", "Değeri kullan"],
            "formula": ["F = m * a: Kuvvet, kütle ile ivmenin çarpımına eşittir"],
            "callout": ["UYARI: NULL bir pointer'ı dereference etmek programı çökertir"],
        }
        self.assertEqual(set(samples), _CONTENT_LAYOUTS)
        for layout, bullets in samples.items():
            with self.subTest(layout=layout):
                slide = Slide(title=f"Test - {layout}", layout=layout, bullets=bullets, narration="test")
                self._render(slide, layout)

    def test_unknown_layout_value_falls_back_to_bullets_without_crashing(self):
        slide = Slide(title="Bilinmeyen format", layout="uydurma-format",
                      bullets=["a", "b", "c"], narration="test")
        self._render(slide, "unknown-layout")

    def test_emphasis_with_more_than_one_bullet_falls_back_to_bullets(self):
        # "emphasis" TEK öğe bekler; LLM kurala uymayıp birden fazla madde yazarsa
        # sessizce normal madde listesine düşülür.
        slide = Slide(title="Yanlış kullanım", layout="emphasis",
                      bullets=["Birinci", "İkinci"], narration="test")
        self._render(slide, "emphasis-fallback")

    def test_malformed_definition_content_falls_back_to_bullets(self):
        slide = Slide(title="Kolonsuz tanım", layout="definition",
                      bullets=["Kolon yok burada"], narration="test")
        self._render(slide, "definition-fallback")

    def test_malformed_comparison_content_falls_back_to_bullets(self):
        slide = Slide(title="Ayraç yok", layout="comparison",
                      bullets=["A", "B", "C"], narration="test")
        self._render(slide, "comparison-fallback")

    def test_comparison_fallback_never_shows_the_bare_separator_as_a_bullet(self):
        # Regresyon: "comparison" madde listesine düşerken (ör. slide.code ile birlikte, dar
        # panelde comparison hiç denenmediği için) "---" ayracı süzülmezse anlamsız, çıplak
        # bir madde kartı olarak ekrana çıkıyordu — bkz. _draw_content_layout'un fallback dalı.
        from unittest.mock import patch as _patch
        from app.video import slide_renderer as sr
        slide = Slide(title="Stack vs Heap", layout="comparison", code="int a = 5;",
                      bullets=["Stack", "Hızlı erişim", "---", "Heap", "Esnek boyut"], narration="test")
        with _patch.object(sr, "_draw_bullet_cards", wraps=sr._draw_bullet_cards) as spy:
            self._render(slide, "comparison-code-no-bare-separator")
        rendered_bullets = spy.call_args[0][1]
        self.assertNotIn("---", rendered_bullets)

    def test_formula_with_more_than_one_pair_falls_back_to_bullets(self):
        # "formula" TEK ifade bekler; birden fazla öğe gelirse (definition ile karıştırılmış
        # olabilir) normal madde listesine düşülür.
        slide = Slide(title="İki ifade", layout="formula",
                      bullets=["F = m*a: açıklama", "E = mc^2: açıklama2"], narration="test")
        self._render(slide, "formula-fallback")

    def test_formula_without_colon_falls_back_to_bullets(self):
        slide = Slide(title="Kolonsuz ifade", layout="formula",
                      bullets=["F = m * a"], narration="test")
        self._render(slide, "formula-no-colon-fallback")

    def test_callout_with_more_than_one_pair_falls_back_to_bullets(self):
        slide = Slide(title="İki callout", layout="callout",
                      bullets=["UYARI: bir şey", "NOT: başka bir şey"], narration="test")
        self._render(slide, "callout-fallback")

    def test_callout_recognizes_warning_tip_and_note_labels(self):
        for label in ("UYARI", "İPUCU", "NOT", "BİLİNMEYEN ETİKET"):
            with self.subTest(label=label):
                slide = Slide(title="Callout", layout="callout",
                              bullets=[f"{label}: kısa bir mesaj"], narration="test")
                self._render(slide, f"callout-{label}")

    def test_empty_bullets_with_any_layout_falls_back_to_bullets_placeholder(self):
        for layout in _CONTENT_LAYOUTS:
            with self.subTest(layout=layout):
                slide = Slide(title="Boş", layout=layout, bullets=[], narration="test")
                self._render(slide, f"empty-{layout}")

    def test_process_steps_with_llm_added_self_numbering_still_render(self):
        # LLM promptun "kendi numaranı ekleme" talimatına uymayıp "1. Şunu yap" yazarsa bile
        # (gerçek Gemini çıktısında görüldü) render çökmemeli — çift numaralanma
        # _strip_leading_ordinal ile ayrıca engellenir, bkz. o testler.
        slide = Slide(title="Adımlar", layout="process",
                      bullets=["1. İlk adım", "2) İkinci adım", "3 - Üçüncü adım"], narration="test")
        self._render(slide, "process-self-numbered")


class CodeLayoutRenderSmokeTests(unittest.TestCase):
    """"code_output" (kod solda, terminal çıktısı sağda) bullets/emphasis vb.'den ayrı:
    slide.code doluyken devreye girer, _CONTENT_LAYOUTS'ta YOK (bkz. app/video/slide_renderer.py
    yorum notu) — bu yüzden ayrı bir test sınıfında.

    Ayrıca: slide.code varken diğer metin formatlarının (definition/callout/formula/process/
    emphasis) artık SOL PANELDE de denendiğini doğrular (bkz. _draw_content_layout, compact=True) —
    eskiden `code` her zaman düz madde listesine zorlardı, bu "kodlama dersi" kullanımında
    (ör. bir terimi tanımlayıp yanına örnek kod koymak) gereksiz bir kayıptı. "comparison" tek
    istisna: iki alt-sütun dar panelde okunaksız kalacağından hep madde listesine düşer.
    """

    def setUp(self):
        (CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=CACHE_DIR / "tmp")
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _render(self, slide: Slide, name: str):
        out = self.out_dir / f"{name}.png"
        render_slide(slide, 1, 3, "TEST", out, theme_preset="notebook")
        self.assertTrue(out.exists())
        with Image.open(out) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.mode, "RGB")

    def test_code_output_renders_without_crashing(self):
        slide = Slide(
            title="printf Örneği", layout="code_output", narration="test",
            code='printf("Merhaba dünya\\n");', bullets=["Merhaba dünya"],
        )
        self._render(slide, "code-output")

    def test_code_output_without_bullets_falls_back_to_default_code_layout(self):
        slide = Slide(title="Çıktısız", layout="code_output", narration="test",
                      code='printf("Merhaba dünya\\n");')
        self._render(slide, "code-output-fallback")

    def test_bullets_layout_with_code_uses_default_split_layout(self):
        slide = Slide(title="Normal kod slaydı", layout="bullets", narration="test",
                      code="int a = 5;", bullets=["Bir madde"])
        self._render(slide, "code-default-with-text-layout")

    def test_definition_layout_with_code_renders_in_the_left_panel(self):
        slide = Slide(
            title="Pointer Tanımı", layout="definition", narration="test",
            code="int x = 5;\nint *p = &x;",
            bullets=["Pointer: Bir değişkenin bellekteki adresini tutan değişken"],
        )
        self._render(slide, "code-with-definition")

    def test_callout_layout_with_code_renders_in_the_left_panel(self):
        slide = Slide(
            title="NULL Pointer Tehlikesi", layout="callout", narration="test",
            code="int *p = NULL;\n// *p = 10; -> çöker",
            bullets=["UYARI: NULL pointer dereference programı çökertir"],
        )
        self._render(slide, "code-with-callout")

    def test_comparison_layout_with_code_falls_back_to_bullets_in_the_left_panel(self):
        # comparison dar panelde denenmez (iki alt-sütun okunaksız kalır) — her zaman
        # düz madde listesine düşer, ama code YİNE DE sağda gösterilmeye devam eder.
        slide = Slide(
            title="Stack vs Heap", layout="comparison", narration="test",
            code="int a = 5;  // stack\nint *p = malloc(4);  // heap",
            bullets=["Stack", "Hızlı", "---", "Heap", "Esnek"],
        )
        self._render(slide, "code-with-comparison-fallback")


class StripLeadingOrdinalTests(unittest.TestCase):
    def test_strips_common_ordinal_prefixes(self):
        from app.video.slide_renderer import _strip_leading_ordinal
        self.assertEqual(_strip_leading_ordinal("1. İlk adım"), "İlk adım")
        self.assertEqual(_strip_leading_ordinal("2) İkinci adım"), "İkinci adım")
        self.assertEqual(_strip_leading_ordinal("3 - Üçüncü adım"), "Üçüncü adım")

    def test_leaves_text_without_a_leading_ordinal_untouched(self):
        from app.video.slide_renderer import _strip_leading_ordinal
        self.assertEqual(_strip_leading_ordinal("Değişkeni tanımla"), "Değişkeni tanımla")

    def test_does_not_touch_a_number_that_is_not_a_leading_ordinal(self):
        from app.video.slide_renderer import _strip_leading_ordinal
        self.assertEqual(_strip_leading_ordinal("O(n) karmaşıklığı önemlidir"), "O(n) karmaşıklığı önemlidir")


if __name__ == "__main__":
    unittest.main()
