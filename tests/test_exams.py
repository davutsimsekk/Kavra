import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from app import exams, course_projects
from app.config import load_settings
from studio_web import api
from studio_web.job_store import PersistentJobStore

class ExamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pdir=course_projects.create_course(self.root,"Ders")
        src=self.root/"source.md"
        src.write_text("# Hücre\n\nHücre zarı seçici geçirgendir. DNA kalıtsal bilgiyi taşır.",encoding="utf-8")
        self.source=course_projects.import_source(self.pdir,src,"Hücre.md")
        self.payload={"sourceIds":[self.source["id"]],"count":3,"kind":"mixed","mcqCount":2,"choiceCount":4}
        for name,value in [("PROJECTS_DIR",self.root),("jobs",PersistentJobStore(self.root/"jobs"))]:
            p=patch.object(api,name,value);p.start();self.addCleanup(p.stop)
        self.client=TestClient(api.app,base_url="http://localhost")
        self.base="/api/projects/"+self.pdir.name+"/exams"

    def questions(self):
        return [{"type":"mcq" if i<2 else "classic","prompt":f"Soru {i+1}?",
                 "options":["Bir","İki","Üç","Dört"] if i<2 else [],
                 "correctOption":i if i<2 else None,"answer":(["Bir","İki"][i] if i<2 else "Örnek cevap"),"explanation":"Kaynağa dayalı gerekçe",
                 "rubric":["Temel kavramı açıklar"],"sourceIds":[self.source["id"]],
                 "evidence":[{"sourceId":self.source["id"],"quote":"Hücre zarı seçici geçirgendir."}]} for i in range(3)]

    def test_options_reject_invalid_counts_types_and_distribution(self):
        for update in [{"count":0},{"count":41},{"count":True},{"count":3.5},{"kind":"unknown"},
                       {"mcqCount":3},{"choiceCount":2},{"difficulty":"bad"}]:
            with self.subTest(update=update):
                response=self.client.post(self.base,json={**self.payload,**update})
                self.assertEqual(response.status_code,400,response.text)

    def test_reference_upload_is_separate_and_used_only_when_selected(self):
        text="1. Aşağıdaki durumu değerlendiriniz. Hücre zarının işlevini nedenleriyle açıklayınız."
        response=self.client.post(self.base.replace("/exams","/exam-references"),
                                 files={"file":("vize.txt",text.encode("utf-8"),"text/plain")})
        self.assertEqual(response.status_code,200,response.text)
        reference=response.json()
        self.assertEqual(len(course_projects.list_sources(self.pdir)),1)
        context=exams.prepare(self.pdir,{**self.payload,"referenceIds":[reference["id"]],"difficulty":"reference"})
        self.assertEqual(context["references"][0]["text"],text)
        self.assertIn(text,exams.prompt_for(context))
        self.assertEqual(exams.prepare(self.pdir,self.payload)["references"],[])
        self.assertEqual(self.client.post(self.base+"/estimate",json={**self.payload,"difficulty":"reference"}).status_code,400)

    def test_reference_scanned_pdf_is_rejected_without_publication(self):
        import pymupdf
        with pymupdf.open() as doc:
            doc.new_page()
            data=doc.tobytes()
        response=self.client.post(self.base.replace("/exams","/exam-references"),files={"file":("scan.pdf",data)})
        self.assertEqual(response.status_code,400)
        self.assertEqual(exams.list_references(self.pdir),[])

    def test_validation_enforces_correct_answers_question_count_and_provenance(self):
        ctx=exams.prepare(self.pdir,self.payload)
        valid=self.questions()
        normalized=exams.validate(json.dumps(valid),ctx)
        self.assertEqual(normalized[0]["answer"],"Bir")
        variants=[]
        for field,value in [("correctOption",4),("correctOption",True),("sourceIds",["unknown"]),("options",["x"]*4),("explanation","")]:
            modified=json.loads(json.dumps(valid));modified[0][field]=value;variants.append(modified)
        modified=json.loads(json.dumps(valid));modified[1]["prompt"]=modified[0]["prompt"];variants.append(modified)
        variants.append(valid[:2])
        for invalid in variants:
            with self.assertRaises(ValueError):exams.validate(json.dumps(invalid),ctx)

    def test_one_repair_then_persist_no_secret_and_safe_exports(self):
        ctx=exams.prepare(self.pdir,self.payload)
        call=Mock(side_effect=["not json",json.dumps(self.questions())])
        exam=exams.generate(self.pdir,"Vize",ctx,call)
        self.assertEqual(call.call_count,2)
        self.assertEqual(len(exam["questions"]),3)
        self.assertEqual(len(exams.list_exams(self.pdir)),1)
        self.assertNotIn("gerekçe",exams.export_markdown(exam))
        self.assertIn("gerekçe",exams.export_markdown(exam,True))
        call=Mock(return_value="invalid")
        with self.assertRaises(ValueError):exams.generate(self.pdir,"Bad",ctx,call)
        self.assertEqual(call.call_count,2)
        self.assertEqual(len(exams.list_exams(self.pdir)),1)

    def test_invalid_scope_never_starts_a_job(self):
        with patch.object(api.jobs,"create") as create:
            for ids in [[],["../bad"],[self.source["id"],self.source["id"]]]:
                response=self.client.post(self.base,json={**self.payload,"sourceIds":ids})
                self.assertIn(response.status_code,[400,404])
            create.assert_not_called()

    def test_cross_project_reference_and_exam_ids_cannot_be_read(self):
        other=course_projects.create_course(self.root,"Başka")
        exam=exams.generate(self.pdir,"Vize",exams.prepare(self.pdir,self.payload),
                            lambda _:json.dumps(self.questions()))
        response=self.client.get("/api/projects/"+other.name+"/exams/"+exam["id"])
        self.assertEqual(response.status_code,404)

    def test_end_to_end_job_uses_selected_sources_and_saves_actual_counts(self):
        mock=Mock()
        mock._call.return_value=json.dumps(self.questions())
        with patch("app.llm.gemini_provider.GeminiNarrationGenerator",return_value=mock):
            response=self.client.post(self.base,json={**self.payload,"provider":"gemini","apiKey":"secret-not-persisted"})
            self.assertEqual(response.status_code,200,response.text)
            for _ in range(300):
                job=self.client.get("/api/jobs/"+response.json()["jobId"]).json()
                if job["status"] in ("complete","failed"):break
                time.sleep(0.01)
        self.assertEqual(job["status"],"complete",job.get("error"))
        exam=job["result"]["exam"]
        self.assertEqual(exam["settings"]["mcqCount"],2)
        self.assertEqual(self.client.get(self.base+"/"+exam["id"]).status_code,200)
        self.assertNotIn("secret-not-persisted",(self.pdir/"exams"/exam["id"]/"exam.json").read_text(encoding="utf-8"))
        ledger=json.loads((self.pdir/"cost_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["entries"][0]["requests"],1)
        download=self.client.get(self.base+"/"+exam["id"]+"/export")
        self.assertEqual(download.status_code,200)
        self.assertNotIn("Kaynağa dayalı gerekçe",download.text)

    def test_openai_provider_disables_the_slide_json_schema(self):
        """Regresyon testi: gerçek bir kullanıcı denemesinde OpenRouter
        (openai-uyumlu sağlayıcı) ile sınav üretimi "1. soruda prompt eksik"
        hatasıyla başarısız oldu. Sebep: generator._call() OpenRouter'da
        varsayılan olarak DERS SLAYTI şemasını zorluyordu — sınav sorusu
        isterken bu yanlış; model "prompt" alanı olmayan slayt-şekilli
        nesneler döndürüyordu. Route artık provider=="openai" için
        json_schema=None geçirmeli."""
        mock=Mock()
        mock._call.return_value=json.dumps(self.questions())
        with patch("app.llm.openai_compatible_provider.OpenAICompatibleNarrationGenerator",return_value=mock):
            response=self.client.post(self.base,json={**self.payload,"provider":"openai","apiKey":"secret"})
            self.assertEqual(response.status_code,200,response.text)
            for _ in range(300):
                job=self.client.get("/api/jobs/"+response.json()["jobId"]).json()
                if job["status"] in ("complete","failed"):break
                time.sleep(0.01)
        self.assertEqual(job["status"],"complete",job.get("error"))
        mock._call.assert_called_once()
        call_args,call_kwargs=mock._call.call_args
        self.assertEqual(call_kwargs.get("json_schema"),None)
        self.assertIn("json_schema",call_kwargs, "call() json_schema'yı AÇIKÇA None geçirmeli, hiç geçirmemek "
                     "OpenAICompatibleNarrationGenerator._call'ın varsayılanı olan slayt şemasına düşer.")

    def test_limits_reject_content_instead_of_silently_truncating(self):
        sdir=self.pdir/"sources"/self.source["id"]
        raw=course_projects.read_json(sdir/"raw_sections.json")
        raw[0]["text"]="a"*(exams.SOURCE_LIMIT+1)
        course_projects.write_json(sdir/"raw_sections.json",raw)
        self.assertEqual(self.client.post(self.base+"/estimate",json=self.payload).status_code,400)


    def test_source_evidence_must_match_each_cited_source(self):
        ctx=exams.prepare(self.pdir,self.payload)
        for evidence in [[], [{"sourceId":self.source["id"],"quote":"Kaynakta bulunmayan uydurma bir açıklama."}],
                         [{"sourceId":"unknown","quote":"Hücre zarı seçici geçirgendir."}]]:
            with self.subTest(evidence=evidence):
                questions=self.questions();questions[0]["evidence"]=evidence
                with self.assertRaises(ValueError):
                    exams.validate(json.dumps(questions),ctx)
        questions=self.questions()
        questions[0]["evidence"][0]["quote"]="Hücre  zarı\tseçici  geçirgendir."
        validated=exams.validate(json.dumps(questions),ctx)
        self.assertEqual(validated[0]["evidence"][0]["quote"],"Hücre zarı seçici geçirgendir.")

    def test_answer_index_contradiction_requires_correction(self):
        ctx=exams.prepare(self.pdir,self.payload)
        questions=self.questions()
        questions[0]["answer"]="İki"
        with self.assertRaisesRegex(ValueError,"çelişiyor"):
            exams.validate(json.dumps(questions),ctx)

    def test_copied_reference_question_is_not_published(self):
        ctx=exams.prepare(self.pdir,self.payload)
        stem="Bir araştırmacı hücre zarının seçici geçirgen özelliğini incelemek için farklı büyüklükte molekülleri aynı derişimde kullanmıştır bu deneyin bağımlı değişkeni nedir"
        ctx["references"]=[{"text":stem,"id":"example","name":"Geçmiş sınav"}]
        questions=self.questions();questions[0]["prompt"]=stem
        with self.assertRaisesRegex(ValueError,"kopyalıyor"):
            exams.validate(json.dumps(questions),ctx)

    def test_partial_unreadable_source_reports_warning(self):
        sdir=self.pdir/"sources"/self.source["id"]
        raw=course_projects.read_json(sdir/"raw_sections.json")
        raw.append({"breadcrumb":"","title":"Görsel sayfa","text":"","code_blocks":[],"level":3})
        course_projects.write_json(sdir/"raw_sections.json",raw)
        response=self.client.post(self.base+"/estimate",json=self.payload)
        self.assertEqual(response.status_code,200)
        self.assertIn("1 bölümde",response.json()["warnings"][0])

    def test_quality_report_and_answer_export_include_verified_evidence(self):
        ctx=exams.prepare(self.pdir,self.payload)
        exam=exams.generate(self.pdir,"Alıntılı sınav",ctx,lambda _:json.dumps(self.questions()))
        self.assertTrue(exam["quality"]["evidenceVerified"])
        self.assertIn("Dayanak (Hücre.md)",exams.export_markdown(exam,True))
        self.assertNotIn("Dayanak",exams.export_markdown(exam,False))



    def test_passage_ids_resolve_to_original_text_without_model_transcription(self):
        ctx=exams.prepare(self.pdir,self.payload)
        questions=self.questions()
        passage=exams.evidence_passages(ctx["sources"][0])[0]
        for q in questions:
            q["evidence"]=[{"sourceId":self.source["id"],"passageId":passage["passageId"]}]
        result=exams.validate(json.dumps(questions),ctx)
        self.assertEqual(result[0]["evidence"][0]["quote"],passage["text"])
        questions[0]["evidence"][0]["passageId"]="unknown"
        with self.assertRaisesRegex(ValueError,"parçası"):
            exams.validate(json.dumps(questions),ctx)



    def test_source_passages_preserve_code_and_short_content(self):
        source={"sourceId":"example","name":"Kod","sections":[
            {"title":"Kod","text":"","code":["if ready:\\n    run()"]},
            {"title":"Kısa","text":"ATP","code":[]}]}
        passages=exams.evidence_passages(source)
        self.assertIn("    run()",passages[0]["text"])
        self.assertEqual(passages[-1]["text"],"ATP")


if __name__=="__main__":
    unittest.main()
