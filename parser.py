import re
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import asyncio

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gdz.ru/",
}

def extract_numbers(text: str) -> list[str]:
    if not text: return []
    text = re.sub(r'(?:\bп\.|\bпункт|\b§|\bпараграф)\s*\d+', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?', ' ', text)
    text = re.sub(r'\b(19|20)\d{2}\b', ' ', text)
    text = re.sub(r'(?:\bстр\.|\bстраниц[аы]?)\s*\d+(-\d+)?', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+[,.]?\d*\s*лист[аов]?', ' ', text, flags=re.IGNORECASE)

    numbers = re.findall(r'\d{1,4}', text)
    seen, result = set(), []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result

def _get_solutions(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
        if r.status_code != 200: return []
        html = r.text
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    solutions = []

    ai_card = soup.find("div", class_="ai-card")
    if ai_card:
        ai_content = ai_card.find("div", class_="ai-card__content")
        if ai_content:
            solutions.append({
                "edition": "ИИ-решебник",
                "text": ai_content.get_text(separator="\n").strip()
            })

    for fig in soup.find_all("figure", class_="task-container"):
        caption = fig.find("figcaption", class_="task-crumbs")
        raw_title = caption.get_text(" ", strip=True) if caption else "Решение"
        edition_title = re.sub(r'/\s*(задание|страница)\s*/\s*\d+', '', raw_title, flags=re.IGNORECASE).strip()

        images = []
        for img in fig.find_all("img"):
            src = img.get("src", "")
            if ("/attachments/" in src or ".jpg" in src or ".png" in src) and "logo" not in src.lower() and "icon" not in src.lower():
                full = urljoin("https://gdz.ru", src)
                if full not in images:
                    images.append(full)

        if images:
            solutions.append({
                "edition": edition_title if edition_title else "Решение",
                "images": images
            })

    return solutions

async def parse_tasks(jobs: list[dict]) -> list[dict]:
    results = []
    for job in jobs:
        subject = job["subject"]
        base = (job.get("url") or "").strip()
        raw_text = job.get("raw_text", "").strip()
        s_type = job.get("type", "numbers")

        if s_type == "text" or not base:
            results.append({"subject": subject, "number": None, "url": None, "solutions": [], "found": False, "raw_text": raw_text, "is_text": True})
            continue

        if not base.endswith("/"): base += "/"

        numbers = extract_numbers(job.get("numbers", ""))

        if not numbers:
            results.append({"subject": subject, "number": None, "url": None, "solutions": [], "found": False, "raw_text": raw_text, "is_text": True})
            continue

        for num in numbers:
            candidates = []
            # 1. Специальная логика для физики (формат "параграф.задание")
            if subject.lower().strip() == "физика" and "." in job.get("numbers", ""):
                parts = job.get("numbers", "").split(".")
                if len(parts) == 2:
                    paragraf, zadanie = parts[0].strip(), parts[1].strip()
                    # Пробуем все типы: 1 - вопросы, 2 - задания, 3 - обсуждения
                    for t in [1, 2, 3]:
                        candidates.append(urljoin(base, f"{paragraf}-{t}-item-{zadanie}/"))
            # 2. Стандартные варианты для остальных предметов и обычных номеров
            candidates.extend([
                urljoin(base, f"{num}-task/"),
                urljoin(base, f"{num}-s/"),
                urljoin(base, f"{num}-nom/"),
                urljoin(base, f"1-nom-{num}/"),
                urljoin(base, f"2-nom-{num}/"),
                urljoin(base, f"nom-{num}/"),
                urljoin(base, f"task-{num}/"),
                urljoin(base, f"1-1-prt-{num}/"),
                urljoin(base, f"1-2-prt-{num}/"),
                urljoin(base, f"2-1-prt-{num}/"),
                urljoin(base, f"2-2-prt-{num}/"),
                urljoin(base, f"page-{num}/"),
                urljoin(base, f"{num}-page/"),
                urljoin(base, f"page-{num}-task/"),
                urljoin(base, f"{num}/"),
                urljoin(base, f"paragraf-{num}/"),
                urljoin(base, f"{num}-zadanie/"),
                urljoin(base, f"zadanie-{num}/"),
            ])

            solutions, used = [], candidates[0] if candidates else ""
            for url in candidates:
                solutions = await asyncio.to_thread(_get_solutions, url)
                if solutions:
                    used = url
                    break

            results.append({
                "subject": subject,
                "number": num,
                "url": used,
                "solutions": solutions,
                "found": len(solutions) > 0,
                "raw_text": raw_text,
                "is_text": False
            })

    return results