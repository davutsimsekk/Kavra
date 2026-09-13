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


if __name__ == "__main__":
    unittest.main()
