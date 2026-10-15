import os
from datetime import datetime
from io import BytesIO
from typing import List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from curl_cffi import requests as cffi_requests
from PIL import Image
import asyncio

from parser import parse_tasks

# ⬇️ МЕНЯЙ ПРИ ОБНОВЛЕНИИ
VERSION = "1.7.0"

# ⬇️ ПАРОЛЬ ДЛЯ ВХОДА В АДМИНКУ
ADMIN_PASSWORD = "gdz2024admin"

# ⬇️ ПОДКЛЮЧЕНИЕ К MONGODB
MONGO_URL = os.getenv("MONGO_URL")

feedback_collection = None
if MONGO_URL:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(MONGO_URL)
        db = client.gdz_parser
        feedback_collection = db.feedback
    except Exception as e:
        print(f"[main] MongoDB не подключилась: {e}")

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
        request, "index.html", {"version": VERSION}
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(
        request, "admin.html", {"version": VERSION}
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

    if feedback_collection is not None:
        try:
            await feedback_collection.insert_one(doc)
        except Exception as e:
            print(f"[main] Ошибка сохранения в MongoDB: {e}")

    return {"ok": True}


@app.post("/api/admin/messages")
async def admin_messages(data: dict):
    password = (data or {}).get("password", "")
    if password != ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")

    if feedback_collection is None:
        return {"messages": [], "total": 0}

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

    if feedback_collection is not None:
        await feedback_collection.delete_many({})
    return {"ok": True}


@app.get("/api/image")
async def proxy_image(url: str):
    """Скачивает картинку с gdz.ru и сжимает её для быстрой отдачи."""
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")

    def fetch_and_compress():
        # 1. Скачиваем картинку
        r = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=25,
            headers={"Referer": "https://gdz.ru/"},
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}", None

        content_type = r.headers.get("content-type", "image/jpeg")

        # 2. Пробуем сжать через Pillow
        try:
            img = Image.open(BytesIO(r.content))

            # Уменьшаем до 1200px по ширине
            if img.width > 1200:
                ratio = 1200 / img.width
                new_h = int(img.height * ratio)
                img = img.resize((1200, new_h), Image.LANCZOS)

            # Конвертируем в RGB (если PNG с прозрачностью)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            buf.seek(0)
            return buf.read(), None, "image/jpeg"
        except Exception as e:
            # Если не получилось сжать — отдаём оригинал
            print(f"[main] Не удалось сжать: {e}")
            return r.content, None, content_type

    try:
        data, err, content_type = await asyncio.to_thread(fetch_and_compress)
    except Exception as e:
        raise HTTPException(500, f"Ошибка загрузки: {e}")

    if err:
        raise HTTPException(500, err)

    return Response(
        content=data,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )