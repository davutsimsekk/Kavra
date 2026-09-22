"""Local study planning and time tracking with atomic SQLite transactions."""
from __future__ import annotations

from contextlib import contextmanager, closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import calendar
import csv
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid


class Conflict(ValueError):
    pass


def identifier():
    return uuid.uuid4().hex[:16]


def text(value, limit=200, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Metin en fazla {limit} karakter olmalı.")
    value = value.strip()
    if required and not value:
        raise ValueError("Ad boş bırakılamaz.")
    return value


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Sayı {low} ile {high} arasında olmalı.")
    return value


def day(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalı.")
    date.fromisoformat(value)
    return value


def choice(value, allowed):
    if value not in allowed:
        raise ValueError("Geçersiz seçenek.")
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError("Açık/kapalı değeri geçersiz.")
    return value


def initial():
    return {"version":1, "revision":0, "projects":[], "folders":[], "tasks":[], "entries":[],
            "timer":None, "dailyNotes":{},
            "settings":{"focusMinutes":25,"shortBreakMinutes":5,"longBreakMinutes":15,
                        "longBreakEvery":4,"dailyGoalMinutes":120,"sound":True,"timezoneOffset":180}}


def find(state, collection, key):
    item = next((item for item in state[collection] if item["id"] == key), None)
    if item is None:
        raise ValueError("Kayıt bulunamadı.")
    return item


def descendants(state, task_id):
    result = {task_id}
    while True:
        more = {t["id"] for t in state["tasks"] if t.get("parentId") in result}
        if more <= result:
            return result
        result |= more


def available(state, task):
    project = find(state, "projects", task["projectId"])
    return not project["archived"] and not task["archived"] and task["status"] != "done"


def timer_elapsed(timer, now):
    seconds = timer["elapsedSeconds"]
    if timer["status"] == "running":
        seconds += max(0, now-timer["startedAt"])
    if timer["targetSeconds"]:
        seconds = min(seconds, timer["targetSeconds"])
    return seconds


def capture(state, now):
    timer = state["timer"]
    if not timer or timer["status"] != "running":
        return
    stop = max(timer["startedAt"], now)
    if timer["targetSeconds"]:
        stop = min(stop, timer["startedAt"]+max(0, timer["targetSeconds"]-timer["elapsedSeconds"]))
    seconds = stop-timer["startedAt"]
    if seconds > 0 and timer["phase"] == "focus":
        task = find(state,"tasks",timer["taskId"])
        state["entries"].append({"id":identifier(),"taskId":task["id"],"projectId":task["projectId"],
                                 "startedAt":timer["startedAt"],"seconds":seconds,"kind":timer["mode"],"note":""})
    timer["elapsedSeconds"] += seconds
    timer["startedAt"] = None
    timer["status"] = "paused"


def reconcile(state, now):
    timer = state["timer"]
    if timer and timer["status"] == "running" and timer["targetSeconds"] and timer_elapsed(timer,now) >= timer["targetSeconds"]:
        capture(state,now)
        timer["status"] = "finished"
        if timer["phase"] == "focus":
            timer["completedFocus"] += 1
        return True
    return False


def next_day(previous, repeat, today):
    current = date.fromisoformat(previous or today)
    until = date.fromisoformat(today)
    if repeat == "daily":
        return (max(current,until)+timedelta(days=1)).isoformat()
    if repeat == "weekly":
        current += timedelta(days=max(1,(until-current).days//7+1)*7)
    else:
        anchor = current.day
        while True:
            year,month = (current.year+1,1) if current.month == 12 else (current.year,current.month+1)
            current = date(year,month,min(anchor,calendar.monthrange(year,month)[1]))
            if current > until:
                break
    return current.isoformat()


class StudyStore:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=15)) as conn:
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO workspace VALUES (1,?)",(json.dumps(initial()),))
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _load(self,conn):
        return json.loads(conn.execute("SELECT payload FROM workspace WHERE id=1").fetchone()[0])

    def _save(self,conn,state):
        state["revision"] += 1
        conn.execute("UPDATE workspace SET payload=? WHERE id=1",(json.dumps(state,ensure_ascii=False),))

    def snapshot(self):
        with self.connection() as conn:
            state = self._load(conn)
            now = self.clock()
            if reconcile(state,now):
                self._save(conn,state)
            return {**state,"serverNow":now}

    def command(self,action,data,revision):
        if not isinstance(data,dict):
            raise ValueError("İşlem verisi geçersiz.")
        with self.connection() as conn:
            state = self._load(conn)
            if type(revision) is not int or revision != state["revision"]:
                raise Conflict("Veri başka bir sekmede değişti. Güncel kayıtlar yüklendi; işlemi tekrar yap.")
            now = self.clock()
            reconcile(state,now)
            self.apply(state,action,data,now)
            self._save(conn,state)
            return {**state,"serverNow":now}

    def apply(self,s,action,d,now):
        if action in ("project.save","folder.save","task.save"):
            collection = action.split('.')[0]+"s"
            item = deepcopy(find(s,collection,d["id"])) if d.get("id") else {"id":identifier()}
            is_new = not d.get("id")
            if collection == "projects":
                item.update(name=text(d.get("name",item.get("name","")),160,True),
                            color=text(d.get("color",item.get("color","#39836b")),7),
                            courseId=text(d.get("courseId",item.get("courseId","")),200),
                            notes=text(d.get("notes",item.get("notes","")),20000),
                            archived=item.get("archived",False))
                if not re.fullmatch(r"#[0-9a-fA-F]{6}",item["color"]):
                    raise ValueError("Renk geçersiz.")
            elif collection == "folders":
                project = find(s,"projects",d.get("projectId",item.get("projectId")))
                item.update(name=text(d.get("name",item.get("name","")),160,True),projectId=project["id"],parentId=d.get("parentId",item.get("parentId")))
                parent_id = item["parentId"]
                seen = {item["id"]}
                while parent_id:
                    if parent_id in seen or len(seen)>8:
                        raise ValueError("Klasör kendi içine taşınamaz; en fazla 8 seviye kullanılabilir.")
                    seen.add(parent_id)
                    parent = find(s,"folders",parent_id)
                    if parent["projectId"] != item["projectId"]:
                        raise ValueError("Üst klasör aynı projede olmalı.")
                    parent_id = parent["parentId"]
                if not is_new and find(s,collection,item["id"])["projectId"] != item["projectId"]:
                    raise ValueError("Klasör farklı projeye taşınamaz.")
            else:
                project = find(s,"projects",d.get("projectId",item.get("projectId")))
                if project["archived"]:
                    raise ValueError("Önce projeyi arşivden çıkar.")
                item.update(title=text(d.get("title",item.get("title","")),240,True),projectId=project["id"],
                            parentId=d.get("parentId",item.get("parentId")),folderId=d.get("folderId",item.get("folderId")),
                            notes=text(d.get("notes",item.get("notes","")),20000),
                            plannedDate=day(d.get("plannedDate",item.get("plannedDate"))),
                            dueDate=day(d.get("dueDate",item.get("dueDate"))),
                            plannedTime=text(d.get("plannedTime",item.get("plannedTime","")),5),
                            estimateMinutes=number(d.get("estimateMinutes",item.get("estimateMinutes",25)),0,100000),
                            priority=choice(d.get("priority",item.get("priority","normal")),["low","normal","high","urgent"]),
                            repeat=choice(d.get("repeat",item.get("repeat","none")),["none","daily","weekly","monthly"]),
                            status=item.get("status","todo"),archived=item.get("archived",False),
                            createdAt=item.get("createdAt",now),order=item.get("order",len(s["tasks"])),completedAt=item.get("completedAt"))
                tags = d.get("tags",item.get("tags",[]))
                if not isinstance(tags,list) or len(tags)>12:
                    raise ValueError("En fazla 12 etiket eklenebilir.")
                item["tags"] = list(dict.fromkeys(text(tag,40,True) for tag in tags))
                if item["plannedTime"] and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",item["plannedTime"]):
                    raise ValueError("Plan saati geçersiz.")
                if item["folderId"] and find(s,"folders",item["folderId"])["projectId"] != project["id"]:
                    raise ValueError("Klasör aynı projede olmalı.")
                parent_id = item["parentId"]
                seen = {item["id"]}
                while parent_id:
                    if parent_id in seen or len(seen)>8:
                        raise ValueError("Alt görev döngüsü veya 8 seviye sınırı.")
                    seen.add(parent_id)
                    parent = find(s,"tasks",parent_id)
                    if parent["projectId"] != project["id"] or parent["archived"] or parent["status"] == "done":
                        raise ValueError("Üst görev aynı projede ve açık olmalı.")
                    parent_id = parent["parentId"]
                if not is_new and find(s,collection,item["id"])["projectId"] != project["id"]:
                    raise ValueError("Kayıt geçmişini korumak için görevin projesi değiştirilemez.")
            if is_new:
                if len(s[collection])>=10000:
                    raise ValueError("Kayıt sınırına ulaşıldı.")
                s[collection].append(item)
            else:
                s[collection][next(i for i,x in enumerate(s[collection]) if x["id"]==item["id"])] = item
        elif action == "project.archive":
            project=find(s,"projects",d.get("id"))
            archived=boolean(d.get("archived"))
            if archived and s["timer"] and find(s,"tasks",s["timer"]["taskId"])["projectId"]==project["id"]:
                capture(s,now);s["timer"]=None
            project["archived"]=archived
        elif action == "folder.delete":
            folder=find(s,"folders",d.get("id"))
            for task in s["tasks"]:
                if task["folderId"]==folder["id"]:task["folderId"]=folder["parentId"]
            for child in s["folders"]:
                if child["parentId"]==folder["id"]:child["parentId"]=folder["parentId"]
            s["folders"].remove(folder)
        elif action in ("task.status","task.archive"):
            task=find(s,"tasks",d.get("id"))
            ids=descendants(s,task["id"])
            if action=="task.archive":
                archived=boolean(d.get("archived"))
                if not archived and task["parentId"] and find(s,"tasks",task["parentId"])["archived"]:
                    raise ValueError("Önce üst görevi arşivden çıkar.")
                for child in s["tasks"]:
                    if child["id"] in ids:child["archived"]=archived
                stop=archived
            else:
                if task["archived"] or find(s,"projects",task["projectId"])["archived"]:
                    raise ValueError("Önce görevi ve projesini arşivden çıkar.")
                status=choice(d.get("status"),["todo","doing","done"])
                if status=="done" and any(t["id"] in ids and t["id"]!=task["id"] and not t["archived"] and t["status"]!="done" for t in s["tasks"]):
                    raise ValueError("Önce açık alt görevleri tamamla.")
                if status!="done" and task["parentId"] and find(s,"tasks",task["parentId"])["status"]=="done":
                    raise ValueError("Önce üst görevi yeniden aç.")
                was_done=task["status"]=="done"
                task["status"]=status
                task["completedAt"]=now if status=="done" else None
                stop=status=="done"
                if stop and not was_done and task["repeat"]!="none" and not task.get("nextOccurrenceId"):
                    today=(datetime.fromtimestamp(now,timezone.utc)+timedelta(minutes=s["settings"]["timezoneOffset"])).date().isoformat()
                    planned=next_day(task["plannedDate"] or task["dueDate"],task["repeat"],today)
                    clone=deepcopy(task)
                    clone.update(id=identifier(),status="todo",completedAt=None,createdAt=now,plannedDate=planned,
                                 dueDate=planned if task["dueDate"] else None,order=len(s["tasks"]),parentId=None)
                    clone.pop("nextOccurrenceId",None)
                    task["nextOccurrenceId"]=clone["id"]
                    s["tasks"].append(clone)
            if stop and s["timer"] and s["timer"]["taskId"] in ids:
                capture(s,now);s["timer"]=None
        elif action=="task.move":
            task=find(s,"tasks",d.get("id"))
            other=find(s,"tasks",d.get("otherId"))
            task["order"],other["order"]=other["order"],task["order"]
        elif action=="timer.start":
            task=find(s,"tasks",d.get("taskId"))
            if not available(s,task):raise ValueError("Tamamlanan veya arşivdeki görev başlatılamaz.")
            mode=choice(d.get("mode","pomodoro"),["pomodoro","stopwatch","countdown"])
            target=s["settings"]["focusMinutes"]*60 if mode=="pomodoro" else number(d.get("minutes",25),1,720)*60 if mode=="countdown" else None
            if s["timer"] and s["timer"]["taskId"]==task["id"] and s["timer"]["status"]=="running":return
            capture(s,now)
            task["status"]="doing"
            s["timer"]={"id":identifier(),"taskId":task["id"],"mode":mode,"phase":"focus","status":"running",
                        "startedAt":now,"elapsedSeconds":0,"targetSeconds":target,"completedFocus":0}
        elif action in ("timer.pause","timer.resume","timer.stop","timer.next","timer.skip"):
            timer=s["timer"]
            if not timer:return
            if d.get("timerId")!=timer["id"]:raise Conflict("Aktif oturum değişti; güncel sayacı kullan.")
            if action=="timer.pause":capture(s,now)
            elif action=="timer.resume":
                if timer["status"]=="paused":timer.update(startedAt=now,status="running")
            elif action=="timer.stop":capture(s,now);s["timer"]=None
            elif action=="timer.skip":
                capture(s,now);timer["status"]="finished"
            else:
                if timer["status"]!="finished":raise ValueError("Önce mevcut aralığı bitir veya atla.")
                if timer["mode"]!="pomodoro":raise ValueError("Yeni bir oturum başlat.")
                phase=("longBreak" if timer["completedFocus"] and timer["completedFocus"]%s["settings"]["longBreakEvery"]==0 else "shortBreak") if timer["phase"]=="focus" else "focus"
                timer.update(id=identifier(),phase=phase,status="running",elapsedSeconds=0,startedAt=now,
                             targetSeconds=s["settings"][phase+"Minutes"]*60)
        elif action=="entry.save":
            task=find(s,"tasks",d.get("taskId"))
            entry=deepcopy(find(s,"entries",d["id"])) if d.get("id") else {"id":identifier()}
            start=number(d.get("startedAt"),0,now)
            seconds=number(d.get("seconds"),1,86400)
            if start+seconds>now+1:raise ValueError("Geleceğe çalışma süresi eklenemez.")
            end=start+seconds
            for other in s["entries"]:
                if other["id"]!=entry["id"] and start<other["startedAt"]+other["seconds"] and end>other["startedAt"]:
                    raise ValueError("Bu zaman aralığı başka bir çalışma kaydıyla çakışıyor.")
            timer=s["timer"]
            if timer and timer["status"]=="running" and timer["phase"]=="focus" and end>timer["startedAt"]:
                raise ValueError("Aktif sayaçla çakışan kayıt eklenemez; önce sayacı duraklat.")
            entry.update(taskId=task["id"],projectId=task["projectId"],startedAt=start,seconds=seconds,
                         kind="manual",note=text(d.get("note",""),2000))
            s["entries"]=[x for x in s["entries"] if x["id"]!=entry["id"]]+[entry]
        elif action=="entry.delete":
            entry=find(s,"entries",d.get("id"));s["entries"].remove(entry)
        elif action=="settings":
            for key in ("focusMinutes","shortBreakMinutes","longBreakMinutes","longBreakEvery","dailyGoalMinutes","timezoneOffset"):
                if key in d:
                    low,high=(-840,840) if key=="timezoneOffset" else (1,12) if key=="longBreakEvery" else (1,1440) if key=="dailyGoalMinutes" else (1,180)
                    value=number(d[key],low,high)
                    if int(value)!=value:raise ValueError("Ayarlar tam sayı olmalı.")
                    s["settings"][key]=value
            if "sound" in d:s["settings"]["sound"]=boolean(d["sound"])
        elif action=="daily.note":
            key=day(d.get("date"))
            if not key:raise ValueError("Not tarihi gerekli.")
            s["dailyNotes"][key]=text(d.get("text",""),20000)
        else:
            raise ValueError("Bilinmeyen çalışma takip işlemi.")

    def export_csv(self):
        state=self.snapshot()
        stream=io.StringIO(newline="")
        writer=csv.writer(stream)
        writer.writerow(["Proje","Görev","Başlangıç (UTC)","Dakika","Tür","Not"])
        def safe(value):
            value=str(value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        for entry in sorted(state["entries"],key=lambda e:e["startedAt"]):
            writer.writerow([safe(find(state,"projects",entry["projectId"])["name"]),safe(find(state,"tasks",entry["taskId"])["title"]),
                             datetime.fromtimestamp(entry["startedAt"],timezone.utc).isoformat(),round(entry["seconds"]/60,2),entry["kind"],safe(entry["note"])])
        return '\ufeff'+stream.getvalue()

    def backup(self):
        state=self.snapshot()
        capture(state,state.pop("serverNow"))
        state["timer"]=None
        return state

    def restore(self,backup,revision):
        if not isinstance(backup,dict) or backup.get("version")!=1:
            raise ValueError("Kavra çalışma takip yedeği gerekli.")
        candidate=initial()
        now=self.clock()
        seen=set()
        for collection,action in [("projects","project.save"),("folders","folder.save"),("tasks","task.save")]:
            values=backup.get(collection)
            if not isinstance(values,list) or len(values)>10000:raise ValueError("Kayıt listesi geçersiz.")
            pending=list(values)
            while pending:
                progress=False
                for item in list(pending):
                    if not isinstance(item,dict):raise ValueError("Kayıt geçersiz.")
                    key=item.get("id")
                    if not isinstance(key,str) or not re.fullmatch(r"[a-f0-9]{16}",key) or key in seen:
                        raise ValueError("Kayıt kimliği geçersiz veya yinelenmiş.")
                    if item.get("parentId") and not any(p["id"]==item["parentId"] for p in candidate[collection]):continue
                    data={k:v for k,v in item.items() if k!="id"}
                    self.apply(candidate,action,data,now)
                    created=candidate[collection][-1]
                    created["id"]=key
                    seen.add(key);pending.remove(item);progress=True
                if not progress:raise ValueError("Üst kayıt eksik veya döngü var.")
        for collection in ("projects","tasks"):
            for item in backup[collection]:
                restored=find(candidate,collection,item["id"])
                restored["archived"]=boolean(item.get("archived",False))
                if collection=="tasks":
                    restored["status"]=choice(item.get("status"),["todo","doing","done"])
                    restored["createdAt"]=number(item.get("createdAt"),0,now+60)
                    restored["completedAt"]=number(item["completedAt"],0,now+60) if item.get("completedAt") is not None else None
                    restored["order"]=number(item.get("order",0),0,1000000)
                    if item.get("nextOccurrenceId"):
                        find(candidate,"tasks",item["nextOccurrenceId"])
                        restored["nextOccurrenceId"]=item["nextOccurrenceId"]
        entries=backup.get("entries")
        if not isinstance(entries,list) or len(entries)>100000:raise ValueError("Süre kayıtları geçersiz.")
        for entry in entries:
            if not isinstance(entry,dict):raise ValueError("Süre kaydı geçersiz.")
            key=entry.get("id")
            if not isinstance(key,str) or not re.fullmatch(r"[a-f0-9]{16}",key) or key in seen:raise ValueError("Süre kimliği geçersiz.")
            task=find(candidate,"tasks",entry.get("taskId"))
            seconds=number(entry.get("seconds"),0.000001,315360000)
            start=number(entry.get("startedAt"),0,now)
            if start+seconds>now+60:raise ValueError("Gelecek tarihli süre.")
            candidate["entries"].append({"id":key,"taskId":task["id"],"projectId":task["projectId"],"startedAt":start,"seconds":seconds,
                                         "kind":choice(entry.get("kind"),["manual","pomodoro","countdown","stopwatch"]),"note":text(entry.get("note",""),2000)})
            seen.add(key)
        ordered=sorted(candidate["entries"],key=lambda e:e["startedAt"])
        if any(a["startedAt"]+a["seconds"]>b["startedAt"]+0.001 for a,b in zip(ordered,ordered[1:])):
            raise ValueError("Yedekte çakışan çalışma süreleri var.")
        settings=backup.get("settings")
        if not isinstance(settings,dict):raise ValueError("Ayarlar eksik.")
        self.apply(candidate,"settings",settings,now)
        notes=backup.get("dailyNotes",{})
        if not isinstance(notes,dict) or len(notes)>10000:raise ValueError("Günlük notlar geçersiz.")
        for key,value in notes.items():self.apply(candidate,"daily.note",{"date":key,"text":value},now)
        with self.connection() as conn:
            old=self._load(conn)
            if type(revision) is not int or revision!=old["revision"]:raise Conflict("Veriler değişti. Yenileyip tekrar dene.")
            conn.execute("CREATE TABLE IF NOT EXISTS recovery (created REAL PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO recovery VALUES (?,?)",(now,json.dumps(old,ensure_ascii=False)))
            conn.execute("DELETE FROM recovery WHERE created NOT IN (SELECT created FROM recovery ORDER BY created DESC LIMIT 5)")
            candidate["revision"]=old["revision"]
            self._save(conn,candidate)
        return {**candidate,"serverNow":now}
