import os
from datetime import datetime
from io import BytesIO
from typing import List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from curl_cffi import requests as cffi_requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from parser import parse_tasks

# ⬇️ МЕНЯЙ ПРИ ОБНОВЛЕНИИ
VERSION = "1.50"

# ⬇️ ПАРОЛЬ ДЛЯ ВХОДА В АДМИНКУ
ADMIN_PASSWORD = "gdz2024admin"

# ⬇️ ПОДКЛЮЧЕНИЕ К MONGODB (переменная окружения MONGO_URL на Render)
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.gdz_parser
feedback_collection = db.feedback

app = FastAPI(title="GDZ Парсер")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class Job(BaseModel):
    subject: str
    url: str
    numbers: str = ""


class ParseRequest(BaseModel):
    jobs: List[Job]


class FeedbackRequest(BaseModel):
    message: str
    contact: str = ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"version": VERSION},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"version": VERSION},
    )


@app.get("/api/version")
async def get_version():
    return {"version": VERSION}


@app.post("/api/parse")
async def parse(req: ParseRequest):
    jobs = [j for j in req.jobs if j.numbers.strip()]
    if not jobs:
        raise HTTPException(400, "Введи номера хотя бы для одного предмета")

    try:
        results = await parse_tasks([j.dict() for j in jobs])
    except Exception as e:
        raise HTTPException(500, f"Ошибка парсинга: {e}")

    return {"results": results, "total": len(results)}


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    if not req.message.strip():
        raise HTTPException(400, "Сообщение пустое")

    doc = {
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "message": req.message.strip(),
        "contact": req.contact.strip(),
        "version": VERSION,
    }
    await feedback_collection.insert_one(doc)
    return {"ok": True}


@app.post("/api/admin/messages")
async def admin_messages(data: dict):
    password = (data or {}).get("password", "")
    if password != ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")

    cursor = feedback_collection.find().sort("_id", -1)
    messages = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        messages.append(doc)
    return {"messages": messages, "total": len(messages)}


@app.post("/api/admin/clear")
async def admin_clear(data: dict):
    password = (data or {}).get("password", "")
    if password != ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")

    await feedback_collection.delete_many({})
    return {"ok": True}


@app.get("/api/image")
async def proxy_image(url: str):
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")

    def fetch():
        return cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=30,
            headers={"Referer": "https://gdz.ru/"},
        )

    try:
        r = await asyncio.to_thread(fetch)
    except Exception as e:
        raise HTTPException(500, f"Ошибка загрузки: {e}")

    if r.status_code != 200:
        raise HTTPException(r.status_code, "Не удалось загрузить картинку")

    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
    )