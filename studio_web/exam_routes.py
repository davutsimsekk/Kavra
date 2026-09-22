"""HTTP routes for course exams. Registered before the SPA mount."""
from pathlib import Path
from typing import Literal
import uuid
from fastapi import Body, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from app import exams
from app.config import CACHE_DIR, load_settings
from app.cost_ledger import record

def register_exam_routes(app, project_dir, get_jobs):
    def checked(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except (ValueError,TypeError) as exc:
            raise HTTPException(400,str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404,"Sınav veya örnek dosya bulunamadı.") from exc

    @app.get("/api/projects/{project_id}/exam-references")
    def references(project_id:str):
        return {"references":checked(exams.list_references,project_dir(project_id))}

    @app.post("/api/projects/{project_id}/exam-references")
    async def upload_reference(project_id:str,file:UploadFile=File(...)):
        pdir=project_dir(project_id)
        checked(exams.require_course,pdir)
        suffix=Path(file.filename or "").suffix.lower()
        if suffix not in (".pdf",".txt",".md",".pptx"):
            raise HTTPException(400,"PDF, TXT, Markdown veya PowerPoint yükle.")
        target=CACHE_DIR/"uploads"/(uuid.uuid4().hex+suffix)
        try:
            size=0
            with target.open("wb") as output:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>20*1024*1024:
                        raise HTTPException(413,"Çıkmış sınav dosyası en fazla 20 MB olabilir.")
                    output.write(chunk)
            return await run_in_threadpool(checked,exams.import_reference,pdir,target,Path(file.filename or "Çıkmış sınav").name)
        finally:
            target.unlink(missing_ok=True)
            await file.close()

    @app.post("/api/projects/{project_id}/exams/estimate")
    def estimate(project_id:str,payload:dict=Body(...)):
        ctx=checked(exams.prepare,project_dir(project_id),payload)
        return {"characters":ctx["characters"],"requests":1,"maxGenerationRequests":2,
                "sourceCount":len(ctx["sources"]),"referenceCount":len(ctx["references"]),
                "settings":ctx["settings"],"warnings":ctx["warnings"]}

    @app.get("/api/projects/{project_id}/exams")
    def listing(project_id:str):
        return {"exams":checked(exams.list_exams,project_dir(project_id))}

    @app.post("/api/projects/{project_id}/exams")
    def create(project_id:str,payload:dict=Body(...)):
        pdir=project_dir(project_id)
        ctx=checked(exams.prepare,pdir,payload)
        provider=payload.get("provider","gemini")
        if provider not in ("gemini","openai","agent"):
            raise HTTPException(400,"Geçersiz sağlayıcı.")
        settings=load_settings()
        jobs=get_jobs()
        def work(job_id):
            if provider=="gemini":
                from app.llm.gemini_provider import GeminiNarrationGenerator
                generator=GeminiNarrationGenerator(model=str(payload.get("geminiModel") or settings["gemini_model"]),
                                                    api_key=payload.get("apiKey") or None)
            elif provider=="openai":
                from app.llm.openai_compatible_provider import OpenAICompatibleNarrationGenerator
                generator=OpenAICompatibleNarrationGenerator(
                    endpoint=str(payload.get("openaiEndpoint") or settings["openai_endpoint"]),
                    model=str(payload.get("openaiModel") or settings["openai_model"]),
                    api_key=payload.get("apiKey") or None,timeout=300)
            else:
                from app.llm.agent_cli_provider import AgentCliNarrationGenerator
                generator=AgentCliNarrationGenerator(command=str(payload.get("agentCommand") or settings["agent_command"]),
                                                     timeout=300,reuse_session=False)
            requests=0
            def call(prompt):
                nonlocal requests
                requests+=1
                if provider=="openai":
                    # generator._call()'ın OpenRouter'da varsayılan olarak
                    # zorladığı yapılandırılmış çıktı şeması DERS SLAYTLARI
                    # içindir — sınav sorusu isterken bunu zorlamak modelin
                    # "prompt" alanı olmayan slayt-şekilli nesneler
                    # döndürmesine (ve doğrulamanın başarısız olmasına) yol
                    # açar. Sınav sorularının kendi (farklı) şekli var; şema
                    # zorlamadan, istemin kendi JSON talimatına güveniyoruz.
                    return generator._call(prompt, json_schema=None)
                return generator._call(prompt)
            try:
                exam=exams.generate(pdir,str(payload.get("name","")),ctx,call,
                                    lambda message:jobs.update(job_id,message=message))
                exam["provider"]=provider
                exam["model"]=str(payload.get("geminiModel") or settings["gemini_model"]) if provider=="gemini" else str(payload.get("openaiModel") or settings["openai_model"]) if provider=="openai" else "agent"
                from app.course_projects import write_json
                write_json(pdir/"exams"/exam["id"]/"exam.json",exam)
                return {"exam":exam}
            finally:
                if requests:
                    record(pdir,provider=provider,kind="exam",requests=requests,
                           usd=getattr(generator,"total_cost_usd",None) if provider=="agent" else None)
        return {"jobId":jobs.create("exam",work)}

    @app.get("/api/projects/{project_id}/exams/{exam_id}")
    def get(project_id:str,exam_id:str):
        return checked(exams.get_exam,project_dir(project_id),exam_id)

    @app.get("/api/projects/{project_id}/exams/{exam_id}/export")
    def export(project_id:str,exam_id:str,answers:bool=False,format:Literal["md","docx","pdf"]="md"):
        exam=checked(exams.get_exam,project_dir(project_id),exam_id)
        if format=="md":
            content=exams.export_markdown(exam,answers)
            media_type="text/markdown"
        else:
            from app.exam_export import export_docx, export_pdf
            content=(export_docx if format=="docx" else export_pdf)(exam,answers)
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" if format=="docx" else "application/pdf"
        return Response(content,media_type=media_type,
                        headers={"Content-Disposition":f'attachment; filename="exam-{exam_id}'+('-answers' if answers else '')+'.'+format+'"'})
