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
VERSION = "2.0.0"

# ⬇️ ПАРОЛЬ ДЛЯ АДМИНКИ
ADMIN_PASSWORD = "gdz2024admin"

# ⬇️ MONGODB
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


# Заглушка-картинка 1x1 белая — возвращаем вместо ошибки,
# чтобы img не ломался и не показывал alt-текст
PLACEHOLDER_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffc00011080001000103012200021101031101ffc4001f000001050101010101"
    "010000000000000000000102030405060708090a0bffc400b51000020103030204"
    "03050504040000017d01020300041105122131410613516107227114328191a108"
    "2342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a83"
    "8485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8"
    "b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
    "f2f3f4f5f6f7f8f9faffda000c03010002110311003f00fefa28a2803fffd9"
)


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
            print(f"[main] Ошибка MongoDB: {e}")

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
async def proxy_image(url: str, w: int = 1000):
    """
    Скачивает картинку с gdz.ru, сжимает и отдаёт.
    При ошибке — возвращает заглушку с 200 OK,
    чтобы браузер не показывал alt-текст.
    """
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")

    w = max(400, min(w, 2000))

    def fetch_once():
        """Одна попытка: скачать + сжать."""
        r = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=10,                       # быстро, чтобы не висеть
            headers={"Referer": "https://gdz.ru/"},
        )
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}")

        try:
            img = Image.open(BytesIO(r.content))
            if img.width > w:
                ratio = w / img.width
                img = img.resize((w, int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA"):
                    bg.paste(img, mask=img.split()[-1])
                else:
                    bg.paste(img)
                img = bg
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80, optimize=True)
            return buf.getvalue(), "image/jpeg"
        except Exception:
            # Если сжать не вышло — отдаём оригинал
            return r.content, r.headers.get("content-type", "image/jpeg")

    def fetch_with_retry():
        """2 попытки: если первая упала — ждём 1 сек, пробуем снова."""
        last_err = None
        for attempt in range(2):
            try:
                return fetch_once()
            except Exception as e:
                last_err = e
                print(f"[main] Попытка {attempt + 1} для {url}: {e}")
                if attempt == 0:
                    import time
                    time.sleep(1)
        raise last_err

    try:
        data, content_type = await asyncio.to_thread(fetch_with_retry)
    except Exception as e:
        print(f"[main] Не удалось скачать {url}: {e}")
        # Заглушка с 200 — img не сломается
        return Response(
            content=PLACEHOLDER_JPEG,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache",
                "X-Error": str(e)[:100],
            },
        )

    return Response(
        content=data,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )