import tempfile
import unittest
from pathlib import Path

from app.models import Slide
from app.quality_gate import analyze_slides, load_or_analyze_quality, save_quality_report


class QualityGateTests(unittest.TestCase):
    def test_clean_lecture_is_allowed(self):
        report = analyze_slides([
            Slide(
                title="Kesme işleyişi",
                bullets=["Kesme isteği", "Bağlamın korunması", "Kesme yordamı"],
                narration="İşlemci bir kesme isteği aldığında yürütülen komutu güvenli bir noktada durdurur, bağlamı korur ve ilgili kesme yordamına geçer.",
            )
        ])
        self.assertTrue(report["renderAllowed"])
        self.assertEqual(report["status"], "passed")

    def test_empty_and_corrupted_narration_blocks_render(self):
        report = analyze_slides([
            Slide(title="Boş", narration=""),
            Slide(title="Kodlama", narration="TÃ¼rkÃ§e metin bozuldu ve artÄ±k okunamÄ±yor."),
        ])
        self.assertFalse(report["renderAllowed"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("missing_narration", codes)
        self.assertIn("encoding_damage", codes)

    def test_duplicate_and_dense_slides_are_review_warnings(self):
        narration = " ".join(["Açıklama"] * 20)
        report = analyze_slides([
            Slide(title="Aynı başlık", narration=narration),
            Slide(title="Aynı başlık", bullets=[str(i) for i in range(8)], narration=narration),
        ])
        self.assertTrue(report["renderAllowed"])
        self.assertEqual(report["status"], "review")
        self.assertGreaterEqual(report["warningCount"], 3)

    def test_saved_report_is_invalidated_when_slides_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            slides = [Slide(title="Bir", narration=" ".join(["anlatım"] * 20))]
            first = save_quality_report(pdir, slides)
            changed = [Slide(title="İki", narration=" ".join(["anlatım"] * 20))]
            second = load_or_analyze_quality(pdir, changed)
            self.assertNotEqual(first["slideFingerprint"], second["slideFingerprint"])

    def test_repeated_sentence_across_two_slides_is_flagged(self):
        repeated = "Bu cümle iki farklı slaytta birebir aynı şekilde tekrar ediyor."
        report = analyze_slides([
            Slide(title="Bir", narration=f"{repeated} Bu ilk slaydın devamı burada."),
            Slide(title="İki", narration=f"Farklı bir giriş cümlesi burada. {repeated}"),
        ])
        sentence_issues = [i for i in report["issues"] if i["code"] == "duplicate_sentence"]
        self.assertEqual(len(sentence_issues), 1)
        self.assertEqual(sentence_issues[0]["slideIndex"], 1)
        self.assertIn("slayt 1", sentence_issues[0]["message"])

    def test_repeated_sentence_within_same_slide_is_flagged(self):
        repeated = "Bu oldukça uzun ve tekrar eden bir cümle örneğidir."
        report = analyze_slides([
            Slide(title="Bir", narration=f"{repeated} Başka bir cümle. {repeated}"),
        ])
        sentence_issues = [i for i in report["issues"] if i["code"] == "duplicate_sentence"]
        self.assertEqual(len(sentence_issues), 1)
        self.assertEqual(sentence_issues[0]["slideIndex"], 0)
        self.assertIn("aynı slaydın", sentence_issues[0]["message"])

    def test_short_transition_sentences_are_not_flagged_as_duplicates(self):
        report = analyze_slides([
            Slide(title="Bir", narration="Şimdi devam edelim. " + " ".join(["kavram"] * 15)),
            Slide(title="İki", narration="Şimdi devam edelim. " + " ".join(["başka"] * 15)),
        ])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertNotIn("duplicate_sentence", codes)

    def test_version_bump_forces_reanalysis_of_unchanged_slides(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            path = pdir / "quality_report.json"
            import json
            slides = [Slide(title="Bir", narration=" ".join(["anlatım"] * 20))]
            from app.quality_gate import _fingerprint
            stale = {"version": 1, "slideFingerprint": _fingerprint(slides), "issues": []}
            path.write_text(json.dumps(stale), encoding="utf-8")

            report = load_or_analyze_quality(pdir, slides)
            from app.quality_gate import QUALITY_VERSION
            self.assertEqual(report["version"], QUALITY_VERSION)

    def test_paraphrased_sentence_across_slides_is_flagged_as_near_duplicate(self):
        report = analyze_slides([
            Slide(title="Bir", narration=(
                "İşlemci bir kesme isteği aldığında yürütülen komutu güvenli bir "
                "noktada durdurur ve bağlamı saklar."
            )),
            Slide(title="İki", narration=(
                "Farklı bir konuya geçelim şimdi. İşlemci kesme isteği geldiğinde "
                "çalışan komutu güvenli bir noktada durdurur ve bağlamı saklar."
            )),
        ])
        near_dup = [i for i in report["issues"] if i["code"] == "near_duplicate_sentence"]
        self.assertEqual(len(near_dup), 1)
        self.assertEqual(near_dup[0]["slideIndex"], 1)
        self.assertIn("slayt 1", near_dup[0]["message"])
        self.assertEqual(near_dup[0]["severity"], "info")

    def test_unrelated_sentences_sharing_a_few_words_are_not_flagged(self):
        report = analyze_slides([
            Slide(title="Bir", narration=(
                "Değişken tanımlarken bellek üzerinde bir alan ayrılır ve derleyici "
                "bu alana erişim sağlar."
            )),
            Slide(title="İki", narration=(
                "Fonksiyon çağrısında yığın üzerinde yeni bir çerçeve oluşturulur ve "
                "dönüş adresi saklanır."
            )),
        ])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertNotIn("near_duplicate_sentence", codes)

    def test_exact_duplicate_sentence_is_not_also_reported_as_near_duplicate(self):
        repeated = "Bu cümle iki farklı slaytta birebir aynı şekilde tekrar ediyor efendim."
        report = analyze_slides([
            Slide(title="Bir", narration=repeated),
            Slide(title="İki", narration=repeated),
        ])
        codes = [issue["code"] for issue in report["issues"]]
        self.assertIn("duplicate_sentence", codes)
        self.assertNotIn("near_duplicate_sentence", codes)

    def test_similar_sentences_within_the_same_slide_are_not_flagged_as_near_duplicate(self):
        report = analyze_slides([
            Slide(title="Bir", narration=(
                "İşlemci bir kesme isteği aldığında yürütülen komutu güvenli bir "
                "noktada durdurur. İşlemci kesme isteği geldiğinde çalışan komutu "
                "güvenli bir noktada durdurur."
            )),
        ])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertNotIn("near_duplicate_sentence", codes)


class LayoutMismatchTests(unittest.TestCase):
    """bkz. app/video/slide_renderer.py'nin format ayrıştırma mantığı — bu kontrol o
    mantığın AYNISINI (kopyasını değil) kullanır, böylece "render'da sessizce bullets'a
    düşecek" durumu render'dan ÖNCE, editörde yakalanır."""

    def _issue_for(self, slide: Slide) -> dict | None:
        report = analyze_slides([slide])
        matches = [i for i in report["issues"] if i["code"] == "layout_mismatch"]
        return matches[0] if matches else None

    def test_well_formed_definition_layout_has_no_mismatch_issue(self):
        slide = Slide(title="x", layout="definition", narration="test anlatım metni yeterince uzun",
                      bullets=["Pointer: Bir değişkenin bellekteki adresini tutan değişken"])
        self.assertIsNone(self._issue_for(slide))

    def test_definition_without_a_colon_is_flagged(self):
        slide = Slide(title="x", layout="definition", narration="test anlatım metni yeterince uzun",
                      bullets=["Kolon yok burada"])
        issue = self._issue_for(slide)
        self.assertIsNotNone(issue)
        self.assertEqual(issue["severity"], "warning")
        self.assertEqual(issue["field"], "layout")
        self.assertIn("Tanım kartı", issue["message"])

    def test_comparison_missing_the_separator_is_flagged(self):
        slide = Slide(title="x", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["A", "B", "C"])
        issue = self._issue_for(slide)
        self.assertIsNotNone(issue)
        self.assertIn("Karşılaştırma", issue["message"])

    def test_well_formed_comparison_has_no_mismatch_issue(self):
        slide = Slide(title="x", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["Stack", "Hızlı", "---", "Heap", "Esnek"])
        self.assertIsNone(self._issue_for(slide))

    def test_well_formed_comparison_with_an_embedded_image_has_no_mismatch_issue(self):
        """§10.0.15/§10.0.16: görsel artık ALT şeride yerleştiriliyor (bkz.
        app/video/slide_renderer.py _draw_topic'in "elif slide.embedded_image:" dalı,
        compact=False veriyor) — "comparison" görsel varken de TAM olarak render edilir,
        bu yüzden burada bir uyumsuzluktan söz etmek artık YANLIŞ olurdu (eskiden dar bir
        panelde hiç denenmediği için burada özel olarak flag'leniyordu, o davranış
        değişince bu test de tersine çevrildi)."""
        slide = Slide(title="x", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["Stack", "Hızlı", "---", "Heap", "Esnek"], embedded_image="gorsel.png")
        self.assertIsNone(self._issue_for(slide))

    def test_emphasis_with_more_than_one_bullet_is_flagged(self):
        slide = Slide(title="x", layout="emphasis", narration="test anlatım metni yeterince uzun",
                      bullets=["Birinci", "İkinci"])
        issue = self._issue_for(slide)
        self.assertIsNotNone(issue)
        self.assertIn("Vurgu cümlesi", issue["message"])

    def test_formula_without_a_single_pair_is_flagged(self):
        slide = Slide(title="x", layout="formula", narration="test anlatım metni yeterince uzun",
                      bullets=["F = m * a"])
        self.assertIsNotNone(self._issue_for(slide))

    def test_callout_with_two_pairs_is_flagged(self):
        slide = Slide(title="x", layout="callout", narration="test anlatım metni yeterince uzun",
                      bullets=["UYARI: a", "NOT: b"])
        self.assertIsNotNone(self._issue_for(slide))

    def test_process_with_no_bullets_is_flagged(self):
        slide = Slide(title="x", layout="process", narration="test anlatım metni yeterince uzun", bullets=[])
        self.assertIsNotNone(self._issue_for(slide))

    def test_process_with_exactly_six_steps_is_not_flagged(self):
        # app/video/slide_renderer.py MAX_VISIBLE_BULLET_CARDS=6 ile aynı sınır — tam
        # sınırda hiçbir adım kırpılmaz.
        slide = Slide(title="x", layout="process", narration="test anlatım metni yeterince uzun",
                      bullets=[f"Adım {i}" for i in range(6)])
        self.assertIsNone(self._issue_for(slide))

    def test_process_with_seven_steps_is_flagged_with_the_exact_dropped_count(self):
        """render'ın kendisi 7. adımı SESSİZCE kırpar (bkz. app/video/slide_renderer.py
        _draw_process_layout çağrısı, slide.bullets[:MAX_VISIBLE_BULLET_CARDS]) — eski
        kontrol ('ok = bool(bullets)') bunu hiç yakalamıyordu, sadece liste boş mu diye
        bakıyordu."""
        slide = Slide(title="x", layout="process", narration="test anlatım metni yeterince uzun",
                      bullets=[f"Adım {i}" for i in range(7)])
        issue = self._issue_for(slide)
        self.assertIsNotNone(issue)
        self.assertIn("son 1 adım", issue["message"])
        self.assertIn("madde listesine düşme değil", issue["message"])

    def test_plain_bullets_layout_is_never_flagged(self):
        slide = Slide(title="x", layout="bullets", narration="test anlatım metni yeterince uzun",
                      bullets=["Tek madde"])
        self.assertIsNone(self._issue_for(slide))

    def test_unknown_layout_value_is_not_flagged_here(self):
        # Render zaten tanınmayan bir değeri sessizce "bullets"a düşürüyor — bu kontrol
        # SADECE tanınan ama YANLIŞ YAPILANDIRILMIŞ formatları bildirir.
        slide = Slide(title="x", layout="uydurma-format", narration="test anlatım metni yeterince uzun",
                      bullets=["a"])
        self.assertIsNone(self._issue_for(slide))

    def test_chapter_slide_is_never_flagged_regardless_of_layout(self):
        # _draw_chapter "layout"u hiç okumuyor — burada bir uyumsuzluktan söz etmek yanıltıcı olur.
        slide = Slide(title="x", level="chapter", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["A", "B", "C"])
        self.assertIsNone(self._issue_for(slide))

    def test_slide_with_code_is_never_flagged_regardless_of_layout(self):
        # slide.code varken "layout" (code_output hariç) render'ı hiç etkilemiyor.
        slide = Slide(title="x", code="int a = 1;", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["A", "B", "C"])
        self.assertIsNone(self._issue_for(slide))

    def test_mismatch_does_not_block_render_only_warns(self):
        slide = Slide(title="x", layout="comparison", narration="test anlatım metni yeterince uzun",
                      bullets=["A", "B", "C"])
        report = analyze_slides([slide])
        self.assertTrue(report["renderAllowed"])


class UnexpectedScriptTests(unittest.TestCase):
    """Gerçek bir uçtan uca üretimde (bkz. KAVRA_PROJECT_HANDOFF.md) bir bullet içine tek bir
    Bengalce kelime sızdığı görüldü ("kararlı durum" yerine "kar অবস্থা") — eski kontrol hem bu
    yazı sistemini kapsamıyordu hem de "yoğunluk" eşiği (>=8 karakter VE >%3 oran) tek bir sızmış
    kelimeyi asla yakalamayacak kadar yüksekti. Bu testler her iki açığı da kapatıyor."""

    def _issue_for(self, **slide_kwargs) -> dict | None:
        slide_kwargs.setdefault("narration", "test anlatım metni yeterince uzun")
        slide = Slide(title="x", **slide_kwargs)
        report = analyze_slides([slide])
        matches = [i for i in report["issues"] if i["code"] == "unexpected_language"]
        return matches[0] if matches else None

    def test_single_stray_bengali_word_in_a_bullet_is_flagged(self):
        issue = self._issue_for(bullets=["Uzun vadede aynı kar অবস্থা düzeylerine ulaşılır"])
        self.assertIsNotNone(issue)
        self.assertEqual(issue["severity"], "error")

    def test_single_devanagari_character_is_still_caught(self):
        issue = self._issue_for(narration="test anlatım metni अ yeterince uzun")
        self.assertIsNotNone(issue)

    def test_cjk_and_hangul_are_caught(self):
        self.assertIsNotNone(self._issue_for(narration="test anlatım metni 你好 yeterince uzun"))
        self.assertIsNotNone(self._issue_for(narration="test anlatım metni 안녕 yeterince uzun"))

    def test_clean_turkish_text_is_not_flagged(self):
        self.assertIsNone(self._issue_for(
            bullets=["Şık, öğün, güç gibi Türkçe özel karakterler sorun değil"],
        ))

    def test_greek_letters_in_a_formula_are_not_flagged(self):
        # Yunan alfabesi (α, β) formüllerde MEŞRU olarak kullanılabildiğinden bilinçli olarak
        # kapsam dışı bırakıldı.
        self.assertIsNone(self._issue_for(layout="formula", bullets=["F = m * α: Kuvvet formülü"]))


class MislabeledCalloutTests(unittest.TestCase):
    """Gerçek bir üretimde bir slayt "DİKKAT: ..." içerikli tek bir madde yazıp formatı
    "callout" DEĞİL "emphasis" seçmişti — render'da bu, özel renkli bir uyarı kutusu yerine
    "DİKKAT:" önekiyle birlikte dev bir alıntı cümlesi olarak görünüyor."""

    def _issue_for(self, **slide_kwargs) -> dict | None:
        slide_kwargs.setdefault("narration", "test anlatım metni yeterince uzun")
        slide = Slide(title="x", **slide_kwargs)
        report = analyze_slides([slide])
        matches = [i for i in report["issues"] if i["code"] == "mislabeled_callout"]
        return matches[0] if matches else None

    def test_dikkat_label_in_emphasis_layout_is_flagged(self):
        issue = self._issue_for(
            layout="emphasis",
            bullets=["DİKKAT: Sodyum benzerliği sayesinde iyon pompalarını kontrol eder"],
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue["severity"], "warning")
        self.assertIn("DİKKAT", issue["message"])

    def test_all_callout_labels_are_recognized_case_insensitively(self):
        for label in ("UYARI", "Dikkat", "ipucu", "TAVSİYE", "not"):
            with self.subTest(label=label):
                issue = self._issue_for(layout="bullets", bullets=[f"{label}: bir mesaj burada"])
                self.assertIsNotNone(issue)

    def test_same_label_text_inside_callout_layout_is_not_flagged(self):
        self.assertIsNone(self._issue_for(layout="callout", bullets=["DİKKAT: gerçek bir uyarı"]))

    def test_label_prefix_without_a_colon_is_not_flagged(self):
        # "callout" formatına özgü sözdizimi kesinlikle "ETİKET:" — kolon yoksa bu sadece
        # cümlenin doğal bir parçası (ör. "Not almayı unutma..."), yanlış pozitif olmasın.
        self.assertIsNone(self._issue_for(layout="emphasis", bullets=["Not almayı unutmayın bu konuda"]))

    def test_chapter_slide_is_never_flagged_regardless_of_label(self):
        self.assertIsNone(self._issue_for(level="chapter", layout="emphasis", bullets=["DİKKAT: x"]))

    def test_word_that_merely_starts_with_a_label_is_not_a_false_match(self):
        # "Notasyon:" içindeki "Not" gövdesi kolondan hemen önce değil, bu yüzden eşleşmemeli.
        self.assertIsNone(self._issue_for(layout="emphasis", bullets=["Notasyon: x = y anlamına gelir"]))


if __name__ == "__main__":
    unittest.main()
