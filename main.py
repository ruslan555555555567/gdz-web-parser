from io import BytesIO
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import httpx

from parser import get_solution_images

app = FastAPI(title="GDZ Parser")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class ParseRequest(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/parse")
async def parse(req: ParseRequest):
    if not req.url.startswith("https://gdz.ru/"):
        raise HTTPException(400, "URL должен начинаться с https://gdz.ru/")
    try:
        images = await get_solution_images(req.url)
    except Exception as e:
        raise HTTPException(500, f"Ошибка парсинга: {e}")
    return {"images": images, "count": len(images)}


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