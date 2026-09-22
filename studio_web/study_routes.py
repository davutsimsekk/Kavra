"""Study tracking API; registered ahead of the SPA static mount."""
import json
from pathlib import Path
from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from app.study_tracker import Conflict


def register_study_routes(app, get_store, get_courses):
    @app.get("/api/study")
    def snapshot():
        return get_store().snapshot()

    @app.get("/api/study/courses")
    def courses():
        return {"courses":get_courses()}

    @app.post("/api/study/commands")
    async def command(request:Request):
        if len(await request.body())>200_000:
            raise HTTPException(413,"İşlem çok büyük.")
        try:
            payload=await request.json()
            if not isinstance(payload,dict):raise ValueError("İşlem geçersiz.")
            data=payload.get("data",{})
            if payload.get("action")=="project.save" and isinstance(data,dict) and data.get("courseId"):
                if data["courseId"] not in {course["id"] for course in get_courses()}:
                    raise ValueError("Bağlanacak ders bulunamadı.")
            return await run_in_threadpool(get_store().command,payload.get("action"),data,payload.get("revision"))
        except Conflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except (ValueError,TypeError,KeyError) as exc:
            raise HTTPException(400,str(exc)) from exc

    @app.get("/api/study/export")
    def export(format:str="json"):
        if format=="csv":
            content=get_store().export_csv()
            mime="text/csv"
        elif format=="json":
            content=json.dumps(get_store().backup(),ensure_ascii=False,indent=2)
            mime="application/json"
        else:
            raise HTTPException(400,"JSON veya CSV seç.")
        return Response(content,media_type=mime,headers={"Content-Disposition":f'attachment; filename="calisma-takibi.{format}"'})

    @app.post("/api/study/restore")
    async def restore(request:Request):
        if len(await request.body())>10_000_000:raise HTTPException(413,"Yedek en fazla 10 MB olabilir.")
        try:
            payload=await request.json()
            if not isinstance(payload,dict):raise ValueError("Yedek isteği geçersiz.")
            return await run_in_threadpool(get_store().restore,payload.get("backup"),payload.get("revision"))
        except Conflict as exc:
            raise HTTPException(409,str(exc)) from exc
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            raise HTTPException(400,"Yedek yüklenemedi: "+str(exc)) from exc
