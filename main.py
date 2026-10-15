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
import asyncpg

from parser import parse_tasks

# ⬇️ МЕНЯЙ ПРИ ОБНОВЛЕНИИ
VERSION = "4.0.0"

# ⬇️ ПАРОЛЬ ДЛЯ АДМИНКИ (ОБЯЗАТЕЛЬНО СМЕНИ!)
ADMIN_PASSWORD = "gdz2024admin"

# ⬇️ ПОДКЛЮЧЕНИЕ К ONREZA (PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")

db_pool = None


async def init_db():
    """Создаёт пул соединений и таблицу feedback, если её нет."""
    global db_pool
    if not DATABASE_URL:
        print("[main] DATABASE_URL не задан — работа без БД")
        return
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=5, ssl="require"
        )
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    date TEXT,
                    message TEXT,
                    contact TEXT,
                    version TEXT
                )
            """)
        print("[main] PostgreSQL подключена, таблица feedback готова")
    except Exception as e:
        print(f"[main] Ошибка подключения к PostgreSQL: {e}")
        db_pool = None


app = FastAPI(title="GDZ Парсер")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Заглушка-картинка 1x1 (белая) — возвращаем вместо ошибки
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


@app.on_event("startup")
async def startup():
    await init_db()


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

    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO feedback (date, message, contact, version) "
                    "VALUES ($1, $2, $3, $4)",
                    datetime.now().strftime("%d.%m.%Y %H:%M"),
                    req.message.strip(),
                    req.contact.strip(),
                    VERSION,
                )
        except Exception as e:
            print(f"[main] Ошибка записи в БД: {e}")

    return {"ok": True}


@app.post("/api/admin/messages")
async def admin_messages(data: dict):
    password = (data or {}).get("password", "")
    if password != ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")

    if db_pool is None:
        return {"messages": [], "total": 0}

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, date, message, contact, version "
            "FROM feedback ORDER BY id DESC"
        )

    messages = [
        {
            "id": r["id"],
            "date": r["date"],
            "message": r["message"],
            "contact": r["contact"],
            "version": r["version"],
        }
        for r in rows
    ]
    return {"messages": messages, "total": len(messages)}


@app.post("/api/admin/clear")
async def admin_clear(data: dict):
    password = (data or {}).get("password", "")
    if password != ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")

    if db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM feedback")
    return {"ok": True}


@app.get("/api/image")
async def proxy_image(url: str, w: int = 1000):
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")

    w = max(400, min(w, 2000))

    def fetch_and_compress():
        r = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=8,
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
            return r.content, r.headers.get("content-type", "image/jpeg")

    try:
        data, content_type = await asyncio.to_thread(fetch_and_compress)
    except Exception as e:
        print(f"[main] Ошибка {url}: {e}")
        return Response(
            content=PLACEHOLDER_JPEG,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache", "X-Error": str(e)[:100]},
        )

    return Response(
        content=data,
        media_type=content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )