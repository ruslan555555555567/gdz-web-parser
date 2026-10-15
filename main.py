import os
import hashlib
import secrets
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

VERSION = "7.0.0"
ADMIN_PASSWORD = "gdz2024admin"
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gdz.ru").lower().strip()
DATABASE_URL = os.getenv("DATABASE_URL")

db_pool = None


async def init_db():
    global db_pool
    if not DATABASE_URL:
        print("[main] DATABASE_URL не задан")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, ssl="require")
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY, date TEXT, message TEXT,
                    contact TEXT, version TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, name TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS device TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT NOW()")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme TEXT DEFAULT 'light'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bg_url TEXT")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    subject TEXT, numbers TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        print("[main] PostgreSQL подключена")
    except Exception as e:
        print(f"[main] Ошибка PostgreSQL: {e}")
        db_pool = None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return check.hex() == h
    except Exception:
        return False


async def get_user_by_token(token: str):
    if not db_pool or not token:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT u.id, u.email, u.name, u.is_admin, u.theme, u.bg_url
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = $1
        """, token)
    return dict(row) if row else None


async def check_admin(data: dict):
    password = (data or {}).get("password", "")
    token = (data or {}).get("token", "")
    if token:
        user = await get_user_by_token(token)
        if user and user.get("is_admin"):
            return True
    if password and password == ADMIN_PASSWORD:
        return True
    return False


app = FastAPI(title="GDZ Парсер")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    device: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    device: str = ""


class TokenRequest(BaseModel):
    token: str


class HistorySaveRequest(BaseModel):
    token: str
    subject: str
    numbers: str


class UpdateProfileRequest(BaseModel):
    token: str
    name: str = ""
    theme: str = ""
    bg_url: str = ""


class ChangePasswordRequest(BaseModel):
    token: str
    old_password: str
    new_password: str


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": VERSION})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {"version": VERSION})


@app.get("/api/version")
async def get_version():
    return {"version": VERSION}


# ═══════ АВТОРИЗАЦИЯ ═══════

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    if db_pool is None:
        raise HTTPException(500, "БД не подключена")
    email = req.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(400, "Некорректный email")
    if len(req.password) < 6:
        raise HTTPException(400, "Пароль минимум 6 символов")

    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if existing:
            raise HTTPException(400, "Такой email уже зарегистрирован")

        is_admin = (email == ADMIN_EMAIL)
        user_id = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, is_admin, device, theme) "
            "VALUES ($1, $2, $3, $4, $5, 'light') RETURNING id",
            email, hash_password(req.password), req.name.strip(),
            is_admin, req.device.strip()
        )
        token = secrets.token_urlsafe(32)
        await conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES ($1, $2)",
            token, user_id
        )

    return {"ok": True, "token": token, "user": {
        "id": user_id, "email": email, "name": req.name.strip(),
        "is_admin": is_admin, "theme": "light", "bg_url": None
    }}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if db_pool is None:
        raise HTTPException(500, "БД не подключена")
    email = req.email.lower().strip()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash, name, is_admin, theme, bg_url "
            "FROM users WHERE email = $1", email
        )
        if not row or not verify_password(req.password, row["password_hash"]):
            raise HTTPException(401, "Неверный email или пароль")

        if req.device.strip():
            await conn.execute(
                "UPDATE users SET device = $1, last_seen = NOW() WHERE id = $2",
                req.device.strip(), row["id"]
            )
        else:
            await conn.execute("UPDATE users SET last_seen = NOW() WHERE id = $1", row["id"])

        token = secrets.token_urlsafe(32)
        await conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES ($1, $2)",
            token, row["id"]
        )

    return {"ok": True, "token": token, "user": {
        "id": row["id"], "email": row["email"], "name": row["name"],
        "is_admin": row["is_admin"], "theme": row["theme"] or "light",
        "bg_url": row["bg_url"]
    }}


@app.post("/api/auth/logout")
async def logout(req: TokenRequest):
    if db_pool is not None and req.token:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE token = $1", req.token)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(token: str = ""):
    user = await get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    return {"user": user}


@app.post("/api/auth/update")
async def update_profile(req: UpdateProfileRequest):
    """Обновление имени, темы, фона."""
    user = await get_user_by_token(req.token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    if db_pool is None:
        raise HTTPException(500, "БД недоступна")

    updates = []
    params = []
    idx = 1

    if req.name.strip():
        updates.append(f"name = ${idx}")
        params.append(req.name.strip())
        idx += 1
    if req.theme in ('light', 'dark', 'custom'):
        updates.append(f"theme = ${idx}")
        params.append(req.theme)
        idx += 1
        # Если переключились с custom — очистим bg_url
        if req.theme != 'custom':
            updates.append(f"bg_url = NULL")
    if req.theme == 'custom' and req.bg_url:
        updates.append(f"bg_url = ${idx}")
        params.append(req.bg_url)
        idx += 1

    if not updates:
        return {"ok": True, "user": user}

    params.append(user["id"])
    sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}"

    async with db_pool.acquire() as conn:
        await conn.execute(sql, *params)
        row = await conn.fetchrow(
            "SELECT id, email, name, is_admin, theme, bg_url FROM users WHERE id = $1",
            user["id"]
        )
    return {"ok": True, "user": dict(row)}


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest):
    user = await get_user_by_token(req.token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    if len(req.new_password) < 6:
        raise HTTPException(400, "Новый пароль минимум 6 символов")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1", user["id"])
        if not verify_password(req.old_password, row["password_hash"]):
            raise HTTPException(401, "Старый пароль неверный")
        await conn.execute(
            "UPDATE users SET password_hash = $1 WHERE id = $2",
            hash_password(req.new_password), user["id"]
        )
    return {"ok": True}


@app.post("/api/auth/delete")
async def delete_account(req: TokenRequest):
    """Удаление аккаунта (все сессии, история — через CASCADE)."""
    user = await get_user_by_token(req.token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    if db_pool is None:
        raise HTTPException(500, "БД недоступна")

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", user["id"])
    return {"ok": True}


# ═══════ ИСТОРИЯ ═══════

@app.post("/api/history/save")
async def history_save(req: HistorySaveRequest):
    user = await get_user_by_token(req.token)
    if not user:
        return {"ok": False, "reason": "not_authorized"}
    if db_pool is None:
        return {"ok": False}
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO history (user_id, subject, numbers) VALUES ($1, $2, $3)",
            user["id"], req.subject, req.numbers
        )
        await conn.execute("""
            DELETE FROM history WHERE user_id = $1 AND id NOT IN (
                SELECT id FROM history WHERE user_id = $1
                ORDER BY id DESC LIMIT 20
            )
        """, user["id"])
    return {"ok": True}


@app.get("/api/history/list")
async def history_list(token: str = ""):
    user = await get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    if db_pool is None:
        return {"items": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, subject, numbers,
                   to_char(created_at, 'DD.MM HH24:MI') AS created
            FROM history WHERE user_id = $1 ORDER BY id DESC LIMIT 20
        """, user["id"])
    return {"items": [dict(r) for r in rows]}


@app.post("/api/history/clear")
async def history_clear(req: TokenRequest):
    user = await get_user_by_token(req.token)
    if not user:
        raise HTTPException(401, "Не авторизован")
    if db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM history WHERE user_id = $1", user["id"])
    return {"ok": True}


# ═══════ ОБРАТНАЯ СВЯЗЬ ═══════

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
                    req.message.strip(), req.contact.strip(), VERSION
                )
        except Exception as e:
            print(f"[main] Ошибка feedback: {e}")
    return {"ok": True}


# ═══════ АДМИНКА ═══════

@app.post("/api/admin/check")
async def admin_check(data: dict):
    if await check_admin(data):
        return {"ok": True, "is_admin": True}
    raise HTTPException(401, "Не админ")


@app.post("/api/admin/messages")
async def admin_messages(data: dict):
    if not await check_admin(data):
        raise HTTPException(401, "Нет доступа")
    if db_pool is None:
        return {"messages": [], "total": 0}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, date, message, contact, version FROM feedback ORDER BY id DESC"
        )
    messages = [dict(r) for r in rows]
    return {"messages": messages, "total": len(messages)}


@app.post("/api/admin/users")
async def admin_users(data: dict):
    if not await check_admin(data):
        raise HTTPException(401, "Нет доступа")
    if db_pool is None:
        return {"users": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, email, name, device, is_admin,
                   to_char(created_at, 'DD.MM.YY HH24:MI') AS created,
                   to_char(last_seen, 'DD.MM.YY HH24:MI') AS last_seen
            FROM users ORDER BY id DESC
        """)
    return {"users": [dict(r) for r in rows]}


@app.post("/api/admin/delete/{msg_id}")
async def admin_delete(msg_id: int, data: dict):
    if not await check_admin(data):
        raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM feedback WHERE id = $1", msg_id)
    return {"ok": True}


@app.post("/api/admin/delete-user/{user_id}")
async def admin_delete_user(user_id: int, data: dict):
    if not await check_admin(data):
        raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    return {"ok": True}


@app.post("/api/admin/clear")
async def admin_clear(data: dict):
    if not await check_admin(data):
        raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM feedback")
    return {"ok": True}


# ═══════ ПАРСИНГ ═══════

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


# ═══════ ПРОКСИ КАРТИНОК ═══════

@app.get("/api/image")
async def proxy_image(url: str, w: int = 1000):
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")
    w = max(400, min(w, 2000))

    def fetch_and_compress():
        r = cffi_requests.get(url, impersonate="chrome", timeout=8,
                              headers={"Referer": "https://gdz.ru/"})
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
        return Response(content=PLACEHOLDER_JPEG, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})

    return Response(content=data, media_type=content_type or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})