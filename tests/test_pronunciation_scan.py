"""app/pronunciation_scan.py — bir dersin TÜM anlatımını tarayıp yanlış okunabilecek
İngilizce/teknik terimleri ve fonetik öneri döndürür, HİÇBİR ŞEY KAYDETMEZ."""
import json
import unittest
from unittest.mock import MagicMock, patch

from app.pronunciation_scan import (
    _candidate_words,
    _extract_json_object,
    _SUGGEST_PROMPT,
    suggest_pronunciations,
)


class SuggestPromptSafetyTests(unittest.TestCase):
    def test_forbids_flagging_turkish_suffixed_naturalized_terms(self):
        # Regresyon: gerçek bir çağrıda model "hiperkalsemiye"/"insipidusa"/"polidipsiye"
        # gibi Türkçe hâl eki almış, zaten doğru okunan tıbbi terimleri yanlışlıkla
        # önerdi. Bu kural olmadan tekrar aynı şeye düşülmesin diye bekleniyor.
        self.assertIn("hâl eki", _SUGGEST_PROMPT)

    def test_forbids_space_only_insertions_as_a_correction(self):
        self.assertIn("boşluk eklemek", _SUGGEST_PROMPT)

    def test_requires_spelling_out_every_letter_of_an_abbreviation(self):
        self.assertIn("hiçbir harfi atlama", _SUGGEST_PROMPT)


class CandidateWordsTests(unittest.TestCase):
    def test_extracts_unique_words_not_in_the_known_map(self):
        narrations = ["Bir widget, bir adresi tutar.", "Pointer'ı dereference edelim."]
        result = _candidate_words(narrations, known_map={})
        lowered = {w.lower() for w in result}
        self.assertIn("widget", lowered)
        self.assertIn("dereference", lowered)
        # "bir" iki kez geçiyor ama listede sadece bir kez olmalı.
        self.assertEqual(len([w for w in result if w.lower() == "bir"]), 1)

    def test_words_already_in_the_known_map_are_excluded(self):
        narrations = ["Bir widget kullanalım."]
        result = _candidate_words(narrations, known_map={"widget": "vidgıt"})
        self.assertNotIn("widget", [w.lower() for w in result])

    def test_short_words_are_excluded(self):
        narrations = ["ve ya da bu"]
        result = _candidate_words(narrations, known_map={})
        self.assertEqual(result, [])

    def test_empty_narration_list_returns_empty(self):
        self.assertEqual(_candidate_words([], {}), [])


class ExtractJsonObjectTests(unittest.TestCase):
    def test_parses_a_clean_json_object(self):
        self.assertEqual(_extract_json_object('{"widget": "vidgıt"}'), {"widget": "vidgıt"})

    def test_strips_markdown_code_fences(self):
        self.assertEqual(_extract_json_object('```json\n{"a": "b"}\n```'), {"a": "b"})

    def test_extracts_object_embedded_in_extra_text(self):
        self.assertEqual(_extract_json_object('Here you go: {"a": "b"} thanks'), {"a": "b"})

    def test_malformed_json_returns_empty_dict_not_a_crash(self):
        self.assertEqual(_extract_json_object("not json at all"), {})

    def test_json_array_response_is_rejected_since_an_object_was_expected(self):
        self.assertEqual(_extract_json_object('["a", "b"]'), {})

    def test_none_input_does_not_crash(self):
        self.assertEqual(_extract_json_object(None), {})


class SuggestPronunciationsTests(unittest.TestCase):
    def test_missing_api_key_raises(self):
        with self.assertRaises(ValueError):
            suggest_pronunciations(["widget kullan"], api_key="")

    def test_no_candidate_words_returns_empty_without_calling_the_api(self):
        with patch("google.genai.Client") as client_cls:
            result = suggest_pronunciations(["ve ya da bu"], api_key="key")
        self.assertEqual(result, {})
        client_cls.assert_not_called()

    @patch("google.genai.Client")
    def test_valid_suggestion_is_returned(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text=json.dumps({"widget": "vidgıt"})
        )
        result = suggest_pronunciations(["Bir widget kullanalım."], api_key="key")
        self.assertEqual(result, {"widget": "vidgıt"})

    @patch("google.genai.Client")
    def test_hallucinated_term_outside_the_candidate_list_is_dropped(self, client_cls):
        # Model aday listesinde OLMAYAN bir terim uydurabilir (halüsinasyon) — bu
        # sessizce atlanmalı, sonuçlara asla karışmamalı.
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text=json.dumps({"widget": "vidgıt", "uydurma_terim": "xyz"})
        )
        result = suggest_pronunciations(["Bir widget kullanalım."], api_key="key")
        self.assertEqual(result, {"widget": "vidgıt"})

    @patch("google.genai.Client")
    def test_suggestion_identical_to_the_original_word_is_dropped(self, client_cls):
        # Model "fonetik dönüşüm" yapmayıp kelimeyi olduğu gibi geri döndürürse
        # (anlamsız bir öneri) bu da atlanmalı.
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text=json.dumps({"widget": "widget"})
        )
        result = suggest_pronunciations(["Bir widget kullanalım."], api_key="key")
        self.assertEqual(result, {})

    @patch("google.genai.Client")
    def test_suggestion_that_only_inserts_a_space_is_dropped(self, client_cls):
        # Gerçek bir Gemini çağrısında görüldü: model "hiperkalsemiye" (= "hiperkalsemi" +
        # Türkçe hâl eki "-ye", zaten doğru okunan yerleşik bir tıbbi terim) için sadece
        # "hiperkalsemi ye" (boşluk eklenmiş hâli) önerdi — gerçek bir ses değişikliği yok,
        # anlamsız bir "düzeltme". Bu, prompt kuralına ek olarak yerelde de reddedilmeli.
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text=json.dumps({"hiperkalsemiye": "hiperkalsemi ye"})
        )
        result = suggest_pronunciations(["Bu hiperkalsemiye yol açabilir."], api_key="key")
        self.assertEqual(result, {})

    @patch("google.genai.Client")
    def test_words_already_known_are_never_sent_to_the_model(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(text="{}")
        # "array" yerleşik sözlükte zaten var, "widget" yok — sadece "widget" gönderilmeli.
        suggest_pronunciations(["widget array kullanımı"], api_key="key")
        prompt_sent = client_cls.return_value.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("widget", prompt_sent)
        self.assertNotIn("array", prompt_sent)

    @patch("google.genai.Client")
    def test_large_candidate_list_is_split_into_chunks(self, client_cls):
        from app.pronunciation_scan import CHUNK_SIZE

        client_cls.return_value.models.generate_content.return_value = MagicMock(text="{}")
        # _WORD_RE sadece harfleri eşleştirir (rakam değil) — benzersizlik için iki
        # harfli bir sonek kullanılıyor, "kelimefoo0"/"kelimefoo1" gibi rakamlı bir
        # sonek regex'te aynı "kelimefoo" kelimesine indirgenip yanlışlıkla tekilleşirdi.
        narration = " ".join(f"kelime{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(CHUNK_SIZE + 10))
        calls = []
        suggest_pronunciations([narration], api_key="key", progress_cb=lambda done, total: calls.append((done, total)))
        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 2)
        self.assertEqual(calls, [(1, 2), (2, 2)])

    @patch("time.sleep", return_value=None)
    @patch("google.genai.Client")
    def test_retries_on_rate_limit_then_succeeds(self, client_cls, _sleep):
        from google.genai import errors as genai_errors
        rate_limit_error = genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})
        client_cls.return_value.models.generate_content.side_effect = [
            rate_limit_error, MagicMock(text=json.dumps({"widget": "vidgıt"})),
        ]
        result = suggest_pronunciations(["Bir widget kullanalım."], api_key="key")
        self.assertEqual(result, {"widget": "vidgıt"})


if __name__ == "__main__":
    unittest.main()
