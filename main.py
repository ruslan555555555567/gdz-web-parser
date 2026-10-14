from io import BytesIO
from typing import List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import httpx

from parser import parse_tasks

app = FastAPI(title="GDZ Парсер")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class Job(BaseModel):
    subject: str
    url: str
    numbers: str = ""


class ParseRequest(BaseModel):
    jobs: List[Job]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


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


@app.get("/api/image")
async def proxy_image(url: str):
    if not url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "Недопустимый URL")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://gdz.ru/",
        })
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Не удалось загрузить картинку")
    return StreamingResponse(
        BytesIO(r.content),
        media_type=r.headers.get("content-type", "image/jpeg")
    )