import os
import hashlib
import secrets
from datetime import datetime, timedelta
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

VERSION = "62.0.0"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gdz.ru").lower().strip()
DATABASE_URL = os.getenv("DATABASE_URL")

db_pool = None

DEFAULT_SUBJECTS = [
    {"name": "Алгебра", "url": "https://gdz.ru/class-7/algebra/makarichev-18/", "type": "numbers", "group_name": "🧮 Точные науки", "sort_order": 1, "max_number": 1000, "note": ""},
    {"name": "Геометрия", "url": "https://gdz.ru/class-7/geometria/merzljak/", "type": "numbers", "group_name": "🧮 Точные науки", "sort_order": 2, "max_number": 1000, "note": ""},
    {"name": "Вероятность и статистика", "url": "https://gdz.ru/class-7/matematika/vysockij-yashenko-bazovij-uroven/", "type": "numbers", "group_name": "🧮 Точные науки", "sort_order": 3, "max_number": 500, "note": ""},
    {"name": "Физика", "url": "https://gdz.ru/class-7/fizika/peryshkin-7/", "type": "numbers", "group_name": "🧮 Точные науки", "sort_order": 4, "max_number": 1000, "note": ""},
    {"name": "Информатика", "url": "https://gdz.ru/class-7/informatika/reshebnik-bosova-l-l-7/", "type": "numbers", "group_name": "🧮 Точные науки", "sort_order": 5, "max_number": 500, "note": ""},
    {"name": "Биология", "url": "https://gdz.ru/class-7/biologiya/pasechnik-shvecov-bazovij-uroven/", "type": "numbers", "group_name": "🌿 Естественные науки", "sort_order": 6, "max_number": 500, "note": ""},
    {"name": "Русский язык", "url": "https://gdz.ru/class-7/russkii_yazik/baranova/", "type": "numbers", "group_name": "📖 Гуманитарные науки", "sort_order": 7, "max_number": 560, "note": ""},
    {"name": "Английский язык", "url": "https://gdz.ru/class-7/english/spotlight-7/", "type": "numbers", "group_name": "📖 Гуманитарные науки", "sort_order": 8, "max_number": 500, "note": ""},
    {"name": "География", "url": "", "type": "text", "group_name": "🌿 Естественные науки", "sort_order": 9, "max_number": 0, "note": ""},
    {"name": "Литература", "url": "", "type": "text", "group_name": "📖 Гуманитарные науки", "sort_order": 10, "max_number": 0, "note": ""},
    {"name": "История", "url": "", "type": "text", "group_name": "📖 Гуманитарные науки", "sort_order": 11, "max_number": 0, "note": ""},
    {"name": "Музыка", "url": "", "type": "text", "group_name": "🎨 Остальные предметы", "sort_order": 12, "max_number": 0, "note": ""},
    {"name": "Труд (технология)", "url": "", "type": "text", "group_name": "🎨 Остальные предметы", "sort_order": 13, "max_number": 0, "note": ""},
    {"name": "Изобразительное искусство", "url": "", "type": "text", "group_name": "🎨 Остальные предметы", "sort_order": 14, "max_number": 0, "note": ""},
]

DEFAULT_SETTINGS = {"site_title": "GDZ Парсер", "announcement": "", "footer_text": "", "custom_css": ""}
ALLOWED_TEMPLATES = ("index.html", "admin.html")
MAX_TEMPLATE_SIZE = 2_000_000

async def init_db():
    global db_pool
    if not DATABASE_URL:
        print("[main] DATABASE_URL не задан")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, ssl="require")
        async with db_pool.acquire() as conn:
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
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_bg_url TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_btn_color TEXT DEFAULT '#4299e1'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_theme TEXT DEFAULT 'auto'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS btn_style TEXT DEFAULT 'light'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT TRUE")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS subjects_configured BOOLEAN DEFAULT FALSE")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_devices (
                    device TEXT PRIMARY KEY,
                    blocked_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, url TEXT DEFAULT '',
                    type TEXT DEFAULT 'numbers', group_name TEXT DEFAULT '',
                    sort_order INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS max_number INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''")
            await conn.execute("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_subjects (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    subject_id INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
                    PRIMARY KEY (user_id, subject_id)
                )
            """)
            # НОВОЕ: Таблица для расписания
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_schedule (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    day_of_week INTEGER NOT NULL, -- 1=Пн, 2=Вт, ..., 7=Вс
                    subject_id INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
                    PRIMARY KEY (user_id, day_of_week, subject_id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    subject TEXT, numbers TEXT, created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY, date TEXT, message TEXT, contact TEXT, version TEXT
                )
            """)
            await conn.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS image TEXT")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY, user_id INTEGER, action TEXT, details TEXT, ip TEXT, created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE TABLE IF NOT EXISTS templates (name TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TIMESTAMP DEFAULT NOW())")
            await conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT NOW())")

            count = await conn.fetchval("SELECT COUNT(*) FROM subjects WHERE user_id IS NULL")
            if count == 0:
                for s in DEFAULT_SUBJECTS:
                    await conn.execute("""
                        INSERT INTO subjects (name, url, type, group_name, sort_order, max_number, note)
                        VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (name) DO NOTHING
                    """, s["name"], s["url"], s["type"], s["group_name"], s["sort_order"], s["max_number"], s["note"])
        print("[main] PostgreSQL подключена")
    except Exception as e:
        print(f"[main] Ошибка PostgreSQL: {e}")
        db_pool = None

async def log_action(user_id, action: str, details: str = "", ip: str = ""):
    if db_pool is None: return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO logs (user_id, action, details, ip) VALUES ($1, $2, $3, $4)", user_id, action, details[:500], ip[:50])
            await conn.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 5000)")
    except Exception as e: print(f"[log] {e}")

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${h.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return check.hex() == h
    except Exception: return False

async def get_user_by_token(token: str):
    if not db_pool or not token: return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT u.id, u.email, u.name, u.is_admin, u.theme, u.bg_url, u.avatar,
                   u.admin_bg_url, u.admin_btn_color, u.admin_theme, u.btn_style, u.subjects_configured
            FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = $1
        """, token)
        if row: await conn.execute("UPDATE users SET last_seen = NOW() WHERE id = $1", row["id"])
    return dict(row) if row else None

async def get_admin_password_hash():
    if db_pool is None: return None
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchval("SELECT value FROM settings WHERE key = 'admin_password_hash'")
    except Exception: return None

async def check_admin(data: dict):
    password = (data or {}).get("password", "")
    token = (data or {}).get("token", "")
    if token:
        user = await get_user_by_token(token)
        if user and (user.get("is_admin") or user.get("email") == ADMIN_EMAIL): return True
    if password:
        stored_hash = await get_admin_password_hash()
        if stored_hash and verify_password(password, stored_hash): return True
        if not stored_hash and ADMIN_PASSWORD and password == ADMIN_PASSWORD: return True
    return False

async def check_main_admin(data: dict):
    password = (data or {}).get("password", "")
    token = (data or {}).get("token", "")
    if token:
        user = await get_user_by_token(token)
        if user and user.get("email") == ADMIN_EMAIL: return True
        return False
    if password:
        stored_hash = await get_admin_password_hash()
        if stored_hash and verify_password(password, stored_hash): return True
        if not stored_hash and ADMIN_PASSWORD and password == ADMIN_PASSWORD: return True
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
    subject: str; url: str = ""; numbers: str = ""; raw_text: str = ""; type: str = "numbers"
class ParseRequest(BaseModel): jobs: List[Job]
class FeedbackRequest(BaseModel): message: str; contact: str = ""; image: str = ""
class RegisterRequest(BaseModel): email: str; password: str; name: str = ""; device: str = ""
class LoginRequest(BaseModel): email: str; password: str; device: str = ""
class TokenRequest(BaseModel): token: str
class HistorySaveRequest(BaseModel): token: str; subject: str; numbers: str
class UpdateProfileRequest(BaseModel): token: str; name: str = ""; theme: str = ""; bg_url: str = ""; admin_bg_url: str = ""; admin_btn_color: str = ""; admin_theme: str = ""; btn_style: str = ""
class ChangePasswordRequest(BaseModel): token: str; old_password: str; new_password: str
class AvatarRequest(BaseModel): token: str; avatar: str
class ChangeAdminPasswordRequest(BaseModel): current_password: str = ""; new_password: str; token: str = ""
class TemplateSaveRequest(BaseModel): password: str = ""; token: str = ""; name: str; content: str
class SettingsSaveRequest(BaseModel): password: str = ""; token: str = ""; settings: dict
class ScheduleSaveRequest(BaseModel): token: str; schedule: dict # {"1": [1, 2], "2": [3]} (1=Пн..7=Вс)

@app.on_event("startup")
async def startup(): await init_db()

async def render_page(request: Request, name: str, context: dict):
    html = None
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                html = await conn.fetchval("SELECT content FROM templates WHERE name = $1", name)
        except Exception: pass
    if html:
        try:
            tmpl = templates.env.from_string(html)
            return HTMLResponse(tmpl.render(request=request, **context))
        except Exception as e: print(f"[template] ошибка рендера {name}: {e}")
    return templates.TemplateResponse(request, name, context)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request): return await render_page(request, "index.html", {"version": VERSION})
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request): return await render_page(request, "admin.html", {"version": VERSION})
@app.get("/api/version")
async def get_version(): return {"version": VERSION}

@app.get("/health")
async def health():
    db_status = "no_pool"
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn: await conn.fetchval("SELECT 1")
            db_status = "ok"
        except Exception as e: db_status = f"error: {type(e).__name__}"
    return {"status": "ok", "db": db_status, "version": VERSION, "time": datetime.utcnow().isoformat() + "Z"}

@app.head("/health")
async def health_head(): return Response(status_code=200)
@app.get("/ping")
async def ping(): return await health()

# ═══════ ПРЕДМЕТЫ ═══════
@app.get("/api/subjects")
async def get_subjects(token: str = ""):
    if db_pool is None: return {"subjects": DEFAULT_SUBJECTS}
    user = await get_user_by_token(token) if token else None
    async with db_pool.acquire() as conn:
        if not user:
            rows = await conn.fetch("SELECT id, name, url, type, group_name, sort_order, max_number, note FROM subjects WHERE user_id IS NULL ORDER BY sort_order, id")
        else:
            if user.get("subjects_configured"):
                assigned = await conn.fetch("SELECT subject_id FROM user_subjects WHERE user_id = $1", user["id"])
                s_ids = [r["subject_id"] for r in assigned]
                if s_ids:
                    global_rows = await conn.fetch("SELECT id, name, url, type, group_name, sort_order, max_number, note FROM subjects WHERE user_id IS NULL AND id = ANY($1::int[]) ORDER BY sort_order, id", s_ids)
                else: global_rows = []
            else:
                global_rows = await conn.fetch("SELECT id, name, url, type, group_name, sort_order, max_number, note FROM subjects WHERE user_id IS NULL ORDER BY sort_order, id")
            custom_rows = await conn.fetch("SELECT id, name, url, type, group_name, sort_order, max_number, note FROM subjects WHERE user_id = $1 ORDER BY sort_order, id", user["id"])
            rows = list(global_rows) + list(custom_rows)
    return {"subjects": [dict(r) for r in rows]}

# ═══════ РАСПИСАНИЕ ═══════
@app.get("/api/schedule")
async def get_schedule(token: str = ""):
    user = await get_user_by_token(token)
    if not user or db_pool is None: return {"schedule": {}}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT day_of_week, subject_id FROM user_schedule WHERE user_id = $1", user["id"])
    schedule = {}
    for r in rows:
        day = str(r["day_of_week"])
        if day not in schedule: schedule[day] = []
        schedule[day].append(r["subject_id"])
    return {"schedule": schedule}

@app.post("/api/schedule/save")
async def save_schedule(req: ScheduleSaveRequest):
    user = await get_user_by_token(req.token)
    if not user or db_pool is None: raise HTTPException(401, "Не авторизован")
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM user_schedule WHERE user_id = $1", user["id"])
        for day_str, subject_ids in req.schedule.items():
            day = int(day_str)
            for sid in subject_ids:
                await conn.execute("INSERT INTO user_schedule (user_id, day_of_week, subject_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", user["id"], day, int(sid))
    return {"ok": True}

# ═══════ АДМИНКА (ПРЕДМЕТЫ) ═══════
@app.post("/api/admin/subjects/add")
async def admin_add_subject(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if data.get("user_id") and not await check_main_admin(data): raise HTTPException(403, "Только главный админ")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    name = (data.get("name") or "").strip(); url = (data.get("url") or "").strip()
    s_type = (data.get("type") or "numbers").strip(); group_name = (data.get("group_name") or "").strip()
    sort_order = int(data.get("sort_order") or 999); max_number = int(data.get("max_number") or 0)
    note = (data.get("note") or "").strip(); user_id = data.get("user_id")
    if not name: raise HTTPException(400, "Нужно название")
    if s_type not in ("numbers", "text"): raise HTTPException(400, "Неверный type")
    if s_type == "numbers" and not url: raise HTTPException(400, "Нужна ссылка")
    async with db_pool.acquire() as conn:
        try:
            new_id = await conn.fetchval("INSERT INTO subjects (name, url, type, group_name, sort_order, max_number, note, user_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id", name, url, s_type, group_name, sort_order, max_number, note, user_id)
        except asyncpg.UniqueViolationError: raise HTTPException(400, "Такой предмет уже есть")
    await log_action(None, "subject_add", name)
    return {"ok": True, "id": new_id}

@app.post("/api/admin/subjects/update")
async def admin_update_subject(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    subj_id = data.get("id")
    if not subj_id: raise HTTPException(400, "Нужен id")
    fields, params, idx = [], [], 1
    for key in ("name", "url", "type", "group_name", "note"):
        if key in data and data[key] is not None:
            fields.append(f"{key} = ${idx}"); params.append(str(data[key]).strip()); idx += 1
    for key in ("sort_order", "max_number"):
        if key in data and data[key] is not None:
            try: fields.append(f"{key} = ${idx}"); params.append(int(data[key])); idx += 1
            except: pass
    if not fields: raise HTTPException(400, "Нечего обновлять")
    params.append(int(subj_id))
    sql = f"UPDATE subjects SET {', '.join(fields)} WHERE id = ${idx}"
    async with db_pool.acquire() as conn: await conn.execute(sql, *params)
    await log_action(None, "subject_update", f"id={subj_id}")
    return {"ok": True}

@app.post("/api/admin/subjects/delete")
async def admin_delete_subject(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    subj_id = data.get("id")
    if not subj_id: raise HTTPException(400, "Нужен id")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM subjects WHERE id = $1", int(subj_id))
    await log_action(None, "subject_delete", f"id={subj_id}")
    return {"ok": True}

@app.post("/api/admin/subjects/reorder")
async def admin_reorder_subjects(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    ids = data.get("ids", [])
    if not ids: raise HTTPException(400, "Нужен список ids")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    async with db_pool.acquire() as conn:
        for index, subj_id in enumerate(ids, start=1):
            try: await conn.execute("UPDATE subjects SET sort_order = $1 WHERE id = $2", index, int(subj_id))
            except: continue
    await log_action(None, "subject_reorder", f"count={len(ids)}")
    return {"ok": True, "updated": len(ids)}

@app.post("/api/admin/subjects/reset")
async def admin_reset_subjects(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM subjects WHERE user_id IS NULL")
        for s in DEFAULT_SUBJECTS:
            await conn.execute("INSERT INTO subjects (name, url, type, group_name, sort_order, max_number, note) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (name) DO NOTHING", s["name"], s["url"], s["type"], s["group_name"], s["sort_order"], s["max_number"], s["note"])
    await log_action(None, "subjects_reset")
    return {"ok": True, "count": len(DEFAULT_SUBJECTS)}

# ═══════ АВТОРИЗАЦИЯ ═══════
@app.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
    if db_pool is None: raise HTTPException(500, "БД не подключена")
    async with db_pool.acquire() as conn:
        is_blocked = await conn.fetchval("SELECT 1 FROM blocked_devices WHERE device = $1", req.device.strip())
        if is_blocked: raise HTTPException(403, "Доступ с устройства заблокирован")
    email = req.email.lower().strip()
    if not email or "@" not in email: raise HTTPException(400, "Некорректный email")
    if len(req.password) < 6: raise HTTPException(400, "Пароль минимум 6 символов")
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if existing: raise HTTPException(400, "Email уже зарегистрирован")
        is_admin = (email == ADMIN_EMAIL)
        user_id = await conn.fetchval("INSERT INTO users (email, password_hash, name, is_admin, device, theme, is_verified) VALUES ($1, $2, $3, $4, $5, 'light', TRUE) RETURNING id", email, hash_password(req.password), req.name.strip(), is_admin, req.device.strip())
        token = secrets.token_urlsafe(32)
        await conn.execute("INSERT INTO sessions (token, user_id) VALUES ($1, $2)", token, user_id)
    return {"ok": True, "token": token, "user": {"id": user_id, "email": email, "name": req.name.strip(), "is_admin": is_admin, "theme": "light", "bg_url": None, "avatar": None, "admin_bg_url": None, "admin_btn_color": "#4299e1", "admin_theme": "auto", "btn_style": "light"}}

@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    if db_pool is None: raise HTTPException(500, "БД не подключена")
    async with db_pool.acquire() as conn:
        is_blocked = await conn.fetchval("SELECT 1 FROM blocked_devices WHERE device = $1", req.device.strip())
        if is_blocked: raise HTTPException(403, "Доступ с устройства заблокирован")
    email = req.email.lower().strip()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, password_hash, name, is_admin, theme, bg_url, avatar, admin_bg_url, admin_btn_color, admin_theme, btn_style FROM users WHERE email = $1", email)
        if not row or not verify_password(req.password, row["password_hash"]):
            ip = request.client.host if request.client else ""
            await log_action(None, "login_fail", email, ip)
            raise HTTPException(401, "Неверный email или пароль")
        if req.device.strip(): await conn.execute("UPDATE users SET device = $1, last_seen = NOW() WHERE id = $2", req.device.strip(), row["id"])
        else: await conn.execute("UPDATE users SET last_seen = NOW() WHERE id = $1", row["id"])
        token = secrets.token_urlsafe(32)
        await conn.execute("INSERT INTO sessions (token, user_id) VALUES ($1, $2)", token, row["id"])
    ip = request.client.host if request.client else ""
    await log_action(row["id"], "login", f"{email} · {req.device[:80]}", ip)
    return {"ok": True, "token": token, "user": {"id": row["id"], "email": row["email"], "name": row["name"], "is_admin": row["is_admin"], "theme": row["theme"] or "light", "bg_url": row["bg_url"], "avatar": row["avatar"], "admin_bg_url": row["admin_bg_url"], "admin_btn_color": row["admin_btn_color"] or "#4299e1", "admin_theme": row["admin_theme"] or "auto", "btn_style": row["btn_style"] or "light"}}

@app.post("/api/auth/logout")
async def logout(req: TokenRequest):
    user = await get_user_by_token(req.token)
    if user: await log_action(user["id"], "logout")
    if db_pool is not None and req.token:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM sessions WHERE token = $1", req.token)
    return {"ok": True}

@app.get("/api/auth/me")
async def me(token: str = "", device: str = ""):
    if device and db_pool is not None:
        async with db_pool.acquire() as conn:
            is_blocked = await conn.fetchval("SELECT 1 FROM blocked_devices WHERE device = $1", device.strip())
            if is_blocked: raise HTTPException(status_code=403, detail="Доступ с устройства заблокирован")
    user = await get_user_by_token(token)
    if not user: raise HTTPException(401, "Не авторизован")
    user["is_main_admin"] = (user["email"] == ADMIN_EMAIL)
    return {"user": user}

@app.post("/api/auth/update")
async def update_profile(req: UpdateProfileRequest):
    user = await get_user_by_token(req.token)
    if not user: raise HTTPException(401, "Не авторизован")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    updates, params, idx = [], [], 1
    if req.name.strip(): updates.append(f"name = ${idx}"); params.append(req.name.strip()); idx += 1
    if req.theme in ('light', 'dark', 'custom'):
        updates.append(f"theme = ${idx}"); params.append(req.theme); idx += 1
        if req.theme != 'custom': updates.append("bg_url = NULL")
    if req.theme == 'custom' and req.bg_url:
        if len(req.bg_url) > 5_000_000: raise HTTPException(400, "Фон слишком большой")
        updates.append(f"bg_url = ${idx}"); params.append(req.bg_url); idx += 1
    if user.get("is_admin") or user.get("email") == ADMIN_EMAIL:
        if req.admin_bg_url:
            if len(req.admin_bg_url) > 5_000_000: raise HTTPException(400, "Фон слишком большой")
            updates.append(f"admin_bg_url = ${idx}"); params.append(req.admin_bg_url); idx += 1
        elif req.admin_bg_url == '': updates.append("admin_bg_url = NULL")
        if req.admin_btn_color:
            color = req.admin_btn_color.strip(); import re as _re
            if _re.match(r'^#[0-9a-fA-F]{6}$', color): updates.append(f"admin_btn_color = ${idx}"); params.append(color); idx += 1
        if req.admin_theme in ('auto', 'light', 'dark', 'custom'): updates.append(f"admin_theme = ${idx}"); params.append(req.admin_theme); idx += 1
    if req.btn_style in ('light', 'dark'): updates.append(f"btn_style = ${idx}"); params.append(req.btn_style); idx += 1
    if not updates: return {"ok": True, "user": user}
    params.append(user["id"])
    async with db_pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}", *params)
        row = await conn.fetchrow("SELECT id, email, name, is_admin, theme, bg_url, avatar, admin_bg_url, admin_btn_color, admin_theme, btn_style FROM users WHERE id = $1", user["id"])
    await log_action(user["id"], "profile_update", f"{req.theme} / {req.btn_style}")
    return {"ok": True, "user": dict(row)}

@app.post("/api/auth/avatar")
async def set_avatar(req: AvatarRequest):
    user = await get_user_by_token(req.token)
    if not user: raise HTTPException(401, "Не авторизован")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    if req.avatar == "":
        async with db_pool.acquire() as conn: await conn.execute("UPDATE users SET avatar = NULL WHERE id = $1", user["id"])
        return {"ok": True, "avatar": None}
    if not req.avatar.startswith("data:image/"): raise HTTPException(400, "Неверный формат")
    if len(req.avatar) > 500_000: raise HTTPException(400, "Аватар слишком большой")
    async with db_pool.acquire() as conn: await conn.execute("UPDATE users SET avatar = $1 WHERE id = $2", req.avatar, user["id"])
    return {"ok": True, "avatar": req.avatar}

@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest):
    user = await get_user_by_token(req.token)
    if not user: raise HTTPException(401, "Не авторизован")
    if len(req.new_password) < 6: raise HTTPException(400, "Пароль минимум 6 символов")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1", user["id"])
        if not verify_password(req.old_password, row["password_hash"]): raise HTTPException(401, "Неверный старый пароль")
        await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2", hash_password(req.new_password), user["id"])
    return {"ok": True}

@app.post("/api/auth/delete")
async def delete_account(req: TokenRequest):
    user = await get_user_by_token(req.token)
    if not user: raise HTTPException(401, "Не авторизован")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    async with db_pool.acquire() as conn: await conn.execute("DELETE FROM users WHERE id = $1", user["id"])
    return {"ok": True}

@app.post("/api/history/save")
async def history_save(req: HistorySaveRequest):
    user = await get_user_by_token(req.token)
    if not user or db_pool is None: return {"ok": False}
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO history (user_id, subject, numbers) VALUES ($1, $2, $3)", user["id"], req.subject, req.numbers)
        await conn.execute("DELETE FROM history WHERE user_id = $1 AND id NOT IN (SELECT id FROM history WHERE user_id = $1 ORDER BY id DESC LIMIT 20)", user["id"])
    return {"ok": True}

@app.get("/api/history/list")
async def history_list(token: str = ""):
    user = await get_user_by_token(token)
    if not user: raise HTTPException(401, "Не авторизован")
    if db_pool is None: return {"items": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, subject, numbers, to_char(created_at, 'DD.MM HH24:MI') AS created FROM history WHERE user_id = $1 ORDER BY id DESC LIMIT 20", user["id"])
    return {"items": [dict(r) for r in rows]}

@app.post("/api/history/clear")
async def history_clear(req: TokenRequest):
    user = await get_user_by_token(req.token)
    if not user: raise HTTPException(401, "Не авторизован")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM history WHERE user_id = $1", user["id"])
    return {"ok": True}

@app.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    if not req.message.strip(): raise HTTPException(400, "Сообщение пустое")
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("INSERT INTO feedback (date, message, contact, version, image) VALUES ($1, $2, $3, $4, $5)", datetime.now().strftime("%d.%m.%Y %H:%M"), req.message.strip(), req.contact.strip(), VERSION, req.image)
        except Exception as e: print(f"[main] Ошибка feedback: {e}")
    return {"ok": True}

# ═══════ АДМИНКА ═══════
@app.post("/api/admin/check")
async def admin_check(data: dict):
    if await check_admin(data): return {"ok": True, "is_admin": True}
    raise HTTPException(401, "Не админ")

@app.post("/api/admin/messages")
async def admin_messages(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"messages": [], "total": 0}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, date, message, contact, version, image FROM feedback ORDER BY id DESC")
    return {"messages": [dict(r) for r in rows], "total": len(rows)}

@app.post("/api/admin/users")
async def admin_users(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"users": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, email, name, device, is_admin, avatar, to_char(created_at, 'DD.MM.YY HH24:MI') AS created, to_char(last_seen, 'DD.MM.YY HH24:MI') AS last_seen FROM users ORDER BY id DESC")
    return {"users": [dict(r) for r in rows]}

@app.post("/api/admin/online")
async def admin_online(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"users": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, email, name, device, is_admin, avatar, to_char(last_seen, 'HH24:MI:SS') AS last_seen FROM users WHERE last_seen > NOW() - INTERVAL '5 minutes' ORDER BY last_seen DESC")
    return {"users": [dict(r) for r in rows], "total": len(rows)}

@app.post("/api/admin/logs")
async def admin_logs(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"logs": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT l.id, l.action, l.details, l.ip, to_char(l.created_at, 'DD.MM HH24:MI:SS') AS created, u.email, u.name, u.avatar FROM logs l LEFT JOIN users u ON u.id = l.user_id ORDER BY l.id DESC LIMIT 500")
    return {"logs": [dict(r) for r in rows]}

@app.post("/api/admin/logs/clear")
async def admin_logs_clear(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM logs")
    return {"ok": True}

@app.post("/api/admin/delete/{msg_id}")
async def admin_delete(msg_id: int, data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM feedback WHERE id = $1", msg_id)
    return {"ok": True}

@app.post("/api/admin/delete-user/{user_id}")
async def admin_delete_user(user_id: int, data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    await log_action(None, "admin_delete_user", f"id={user_id}")
    return {"ok": True}

@app.post("/api/admin/clear")
async def admin_clear(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM feedback")
    return {"ok": True}

@app.post("/api/admin/export-db")
async def admin_export_db(data: dict):
    token = data.get("token"); password = data.get("password")
    allowed = False
    if password and password == ADMIN_PASSWORD: allowed = True
    elif token:
        user = await get_user_by_token(token)
        if user and user.get("email") == ADMIN_EMAIL: allowed = True
    if not allowed: raise HTTPException(401, "Экспорт доступен только главному админу.")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    sql_dump = f"-- GDZ Parser DB Backup\n-- Дата: {datetime.now().isoformat()}\n\n"
    async with db_pool.acquire() as conn:
        tables = ['users', 'sessions', 'subjects', 'user_subjects', 'user_schedule', 'settings', 'templates', 'history', 'feedback', 'blocked_devices', 'logs']
        for table in tables:
            sql_dump += f"-- Данные таблицы {table}\n"
            try:
                rows = await conn.fetch(f"SELECT * FROM {table}")
                for row in rows:
                    row_dict = dict(row); cols = ", ".join(row_dict.keys()); vals = []
                    for v in row_dict.values():
                        if v is None: vals.append("NULL")
                        elif isinstance(v, bool): vals.append("TRUE" if v else "FALSE")
                        elif isinstance(v, (int, float)): vals.append(str(v))
                        elif isinstance(v, datetime): vals.append(f"'{v.isoformat()}'")
                        else: vals.append(f"'{str(v).replace(chr(39), chr(39)*2)}'")
                    sql_dump += f"INSERT INTO {table} ({cols}) VALUES ({', '.join(vals)});\n"
            except Exception as e: sql_dump += f"-- Ошибка {table}: {e}\n"
            sql_dump += "\n"
    return Response(content=sql_dump.encode('utf-8'), media_type="application/sql", headers={"Content-Disposition": "attachment; filename=gdz_backup.sql"})

@app.post("/api/admin/toggle-admin")
async def admin_toggle_admin(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    user_id = data.get("user_id")
    if not user_id: raise HTTPException(400, "Нет ID")
    async with db_pool.acquire() as conn:
        current = await conn.fetchval("SELECT is_admin FROM users WHERE id = $1", user_id)
        if current is None: raise HTTPException(404, "Не найден")
        await conn.execute("UPDATE users SET is_admin = $1 WHERE id = $2", not current, user_id)
    return {"ok": True, "is_admin": not current}

@app.post("/api/admin/get-user-subjects")
async def admin_get_user_subjects(data: dict):
    if not await check_main_admin(data): raise HTTPException(403, "Только главный админ")
    user_id = data.get("user_id")
    async with db_pool.acquire() as conn:
        assigned_rows = await conn.fetch("SELECT subject_id FROM user_subjects WHERE user_id = $1", user_id)
        assigned_ids = [r["subject_id"] for r in assigned_rows]
        custom_rows = await conn.fetch("SELECT id, name, url, type, group_name, sort_order, max_number, note FROM subjects WHERE user_id = $1 ORDER BY sort_order, id", user_id)
        conf = await conn.fetchval("SELECT subjects_configured FROM users WHERE id = $1", user_id)
    return {"assigned_global_ids": assigned_ids, "custom_subjects": [dict(r) for r in custom_rows], "subjects_configured": bool(conf)}

@app.post("/api/admin/save-user-subjects")
async def admin_save_user_subjects(data: dict):
    if not await check_main_admin(data): raise HTTPException(403, "Только главный админ")
    user_id = data.get("user_id"); subject_ids = data.get("subject_ids", []); configured = data.get("subjects_configured", True)
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM user_subjects WHERE user_id = $1", user_id)
        for sid in subject_ids: await conn.execute("INSERT INTO user_subjects (user_id, subject_id) VALUES ($1, $2)", user_id, sid)
        await conn.execute("UPDATE users SET subjects_configured = $1 WHERE id = $2", configured, user_id)
    return {"ok": True}

@app.post("/api/admin/block-device")
async def admin_block_device(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    device = (data.get("device") or "").strip()
    if not device: raise HTTPException(400, "Нет данных")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("INSERT INTO blocked_devices (device) VALUES ($1) ON CONFLICT DO NOTHING", device)
    return {"ok": True}

@app.post("/api/admin/unblock-device")
async def admin_unblock_device(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    device = (data.get("device") or "").strip()
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM blocked_devices WHERE device = $1", device)
    return {"ok": True}

@app.post("/api/admin/blocked-devices")
async def admin_blocked_devices(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"devices": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT device, to_char(blocked_at, 'DD.MM.YY HH24:MI') AS blocked FROM blocked_devices ORDER BY blocked_at DESC")
    return {"devices": [dict(r) for r in rows]}

@app.post("/api/admin/password/status")
async def admin_password_status(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    stored_hash = await get_admin_password_hash()
    return {"source": "database" if stored_hash else "render_env", "has_custom_password": bool(stored_hash)}

@app.post("/api/admin/password/change")
async def admin_change_password(req: ChangeAdminPasswordRequest):
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    allowed = False
    if req.token:
        user = await get_user_by_token(req.token)
        if user and (user.get("is_admin") or user.get("email") == ADMIN_EMAIL): allowed = True
    if not allowed and req.current_password:
        stored_hash = await get_admin_password_hash()
        if stored_hash and verify_password(req.current_password, stored_hash): allowed = True
        elif not stored_hash and ADMIN_PASSWORD and req.current_password == ADMIN_PASSWORD: allowed = True
    if not allowed: raise HTTPException(401, "Неверный пароль")
    if len(req.new_password) < 8: raise HTTPException(400, "Пароль минимум 8 символов")
    if req.new_password == req.current_password: raise HTTPException(400, "Пароль должен отличаться")
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('admin_password_hash', $1, NOW()) ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()", hash_password(req.new_password))
    return {"ok": True, "message": "Пароль обновлён"}

@app.get("/api/settings/public")
async def public_settings():
    result = dict(DEFAULT_SETTINGS)
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT key, value FROM settings")
                for row in rows:
                    if row["key"] in result: result[row["key"]] = row["value"] or ""
        except Exception: pass
    return result

@app.post("/api/admin/settings/get")
async def admin_settings_get(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    result = dict(DEFAULT_SETTINGS)
    if db_pool is not None:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM settings")
            for row in rows: result[row["key"]] = row["value"] or ""
    return {"settings": result}

@app.post("/api/admin/settings/save")
async def admin_settings_save(req: SettingsSaveRequest):
    if not await check_admin({"password": req.password, "token": req.token}): raise HTTPException(401, "Нет доступа")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    async with db_pool.acquire() as conn:
        for key, value in req.settings.items():
            if key not in DEFAULT_SETTINGS: continue
            await conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, NOW()) ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()", key, str(value or ""))
    return {"ok": True}

@app.post("/api/admin/templates/list")
async def admin_templates_list(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    if db_pool is None: return {"templates": []}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name, length(content) AS size, to_char(updated_at, 'DD.MM.YY HH24:MI') AS updated FROM templates ORDER BY name")
    return {"templates": [dict(r) for r in rows]}

@app.post("/api/admin/templates/get")
async def admin_templates_get(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    name = (data.get("name") or "").strip()
    if name not in ALLOWED_TEMPLATES: raise HTTPException(400, "Недопустимое имя")
    content, source = None, "file"
    if db_pool is not None:
        async with db_pool.acquire() as conn: content = await conn.fetchval("SELECT content FROM templates WHERE name = $1", name)
        if content: source = "db"
    if content is None:
        path = os.path.join("templates", name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f: content = f.read()
        else: raise HTTPException(404, "Не найден")
    return {"name": name, "content": content, "source": source}

@app.post("/api/admin/templates/save")
async def admin_templates_save(req: TemplateSaveRequest):
    if not await check_admin({"password": req.password, "token": req.token}): raise HTTPException(401, "Нет доступа")
    if req.name not in ALLOWED_TEMPLATES: raise HTTPException(400, "Недопустимое имя")
    if not req.content.strip(): raise HTTPException(400, "Пустой шаблон")
    if len(req.content) > MAX_TEMPLATE_SIZE: raise HTTPException(400, "Слишком большой файл")
    if db_pool is None: raise HTTPException(500, "БД недоступна")
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO templates (name, content, updated_at) VALUES ($1, $2, NOW()) ON CONFLICT (name) DO UPDATE SET content = $2, updated_at = NOW()", req.name, req.content)
    return {"ok": True, "size": len(req.content)}

@app.post("/api/admin/templates/reset")
async def admin_templates_reset(data: dict):
    if not await check_admin(data): raise HTTPException(401, "Нет доступа")
    name = (data.get("name") or "").strip()
    if name not in ALLOWED_TEMPLATES: raise HTTPException(400, "Недопустимое имя")
    if db_pool is not None:
        async with db_pool.acquire() as conn: await conn.execute("DELETE FROM templates WHERE name = $1", name)
    return {"ok": True}

@app.post("/api/parse")
async def parse(req: ParseRequest, request: Request):
    jobs = [j for j in req.jobs if (j.numbers.strip() or j.raw_text.strip())]
    if not jobs: raise HTTPException(400, "Введи номера")
    try: results = await parse_tasks([j.dict() for j in jobs])
    except Exception as e: raise HTTPException(500, f"Ошибка парсинга: {e}")
    subjects = ", ".join(j.subject for j in jobs)[:200]
    ip = request.client.host if request.client else ""
    await log_action(None, "parse", subjects, ip)
    return {"results": results, "total": len(results)}

@app.get("/api/image")
async def proxy_image(url: str, w: int = 1000):
    if not url.startswith("https://gdz.ru/"): raise HTTPException(400, "Недопустимый URL")
    w = max(400, min(w, 2000))
    def fetch_and_compress():
        r = cffi_requests.get(url, impersonate="chrome", timeout=8, headers={"Referer": "https://gdz.ru/"})
        if r.status_code != 200: raise Exception(f"HTTP {r.status_code}")
        try:
            img = Image.open(BytesIO(r.content))
            if img.width > w:
                ratio = w / img.width; img = img.resize((w, int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA"): bg.paste(img, mask=img.split()[-1])
                else: bg.paste(img)
                img = bg
            buf = BytesIO(); img.save(buf, format="JPEG", quality=80, optimize=True)
            return buf.getvalue(), "image/jpeg"
        except Exception: return r.content, r.headers.get("content-type", "image/jpeg")
    try: data, content_type = await asyncio.to_thread(fetch_and_compress)
    except Exception: return Response(content=PLACEHOLDER_JPEG, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
    return Response(content=data, media_type=content_type or "image/jpeg", headers={"Cache-Control": "public, max-age=86400"})