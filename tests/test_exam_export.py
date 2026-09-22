from io import BytesIO
from copy import deepcopy
from unittest.mock import patch
import unittest
import pymupdf
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.exam_export import export_docx, export_pdf
from studio_web.exam_routes import register_exam_routes


def sample():
    return {"name":"Biyoloji Çalışma Sınavı", "sourceIds":["s1"], "sourceNames":["Hücre.pdf"],
            "questions":[{"type":"mcq", "prompt":"Çığ, ışık, şeker: 2 < 3 ve 5 > 4 & hücre?", "options":["İlk seçenek", "İkinci seçenek"],
                          "correctOption":1, "answer":"İkinci seçenek", "explanation":"GİZLİ GEREKÇE", "rubric":[],
                          "evidence":[{"sourceId":"s1", "quote":"GİZLİ DAYANAK"}]},
                         {"type":"classic", "prompt":"Hücre zarının görevini açıklayınız.", "options":[],
                          "correctOption":None, "answer":"GİZLİ YANIT", "explanation":"GİZLİ AÇIKLAMA", "rubric":["GİZLİ ÖLÇÜT"]}]}


class ExamExportTests(unittest.TestCase):
    def text(self, data, fmt):
        if fmt == "docx":
            return "\n".join(p.text for p in Document(BytesIO(data)).paragraphs)
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)

    def test_unicode_and_solution_separation_in_both_formats(self):
        for fmt, render in [("pdf",export_pdf),("docx",export_docx)]:
            with self.subTest(format=fmt):
                paper=self.text(render(sample()),fmt)
                key=self.text(render(sample(),True),fmt)
                self.assertIn("Biyoloji Çalışma Sınavı",paper)
                self.assertIn("2 < 3 ve 5 > 4 & hücre?",paper)
                self.assertIn("Cevabınız",paper)
                self.assertNotIn("GİZLİ",paper)
                for secret in ["GİZLİ YANIT","GİZLİ GEREKÇE","GİZLİ DAYANAK","GİZLİ ÖLÇÜT"]:
                    self.assertIn(secret,key)
                self.assertIn("Doğru cevap: B)",key)

    def test_long_exam_paginates_without_losing_last_question(self):
        exam=sample()
        exam["questions"]=[deepcopy(exam["questions"][1]) for _ in range(40)]
        exam["questions"][-1]["prompt"]="SON SORU KONTROLÜ"
        data=export_pdf(exam)
        with pymupdf.open(stream=data,filetype="pdf") as pdf:
            self.assertGreater(len(pdf),3)
            self.assertIn("SON SORU KONTROLÜ",pdf[-1].get_text())
            self.assertIn(f"{len(pdf)} / {len(pdf)}",pdf[-1].get_text())

    def test_routes_default_markdown_and_explicit_download_formats(self):
        app=FastAPI()
        register_exam_routes(app,lambda _:None,lambda:None)
        with patch("app.exams.get_exam",return_value=sample()):
            client=TestClient(app)
            base="/api/projects/course/exams/exam/export"
            self.assertIn("text/markdown",client.get(base).headers["content-type"])
            for fmt in ("pdf","docx"):
                response=client.get(base,params={"format":fmt,"answers":True})
                self.assertEqual(response.status_code,200)
                self.assertIn(f'exam-exam-answers.{fmt}',response.headers['content-disposition'])
                self.assertIn("GİZLİ YANIT",self.text(response.content,fmt))
            self.assertEqual(client.get(base,params={"format":"html"}).status_code,422)

    def test_oversized_question_flows_across_pages(self):
        exam=sample()
        exam["questions"]=exam["questions"][1:]
        exam["questions"][0]["prompt"]=("Uzun sorunun açıklaması ve verilen deney koşulları. "*250)+" BİTİŞ İŞARETİ"
        data=export_pdf(exam)
        with pymupdf.open(stream=data,filetype="pdf") as pdf:
            self.assertGreater(len(pdf),1)
            text="\n".join(page.get_text() for page in pdf)
            self.assertIn("BİTİŞ İŞARETİ",text)
            self.assertIn("Cevabınız",text)
