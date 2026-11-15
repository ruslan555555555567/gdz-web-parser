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


def _get_page_data(url: str) -> dict:
    """
    Возвращает dict: {"solutions": [...], "condition": "текст условия"}
    solutions — список решений (ИИ + картинки)
    condition — текст условия задачи с gdz.ru (нужно для режима Экзамен)
    """
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
        if r.status_code != 200:
            return {"solutions": [], "condition": ""}
        html = r.text
    except Exception:
        return {"solutions": [], "condition": ""}

    soup = BeautifulSoup(html, "lxml")
    solutions = []

    # 1. Условие задачи (для режима Экзамен)
    condition = ""
    cond_tag = soup.find("p", class_="task__condition")
    if cond_tag:
        # Заменяем <br> на \n, чтобы сохранить переносы строк
        for br in cond_tag.find_all("br"):
            br.replace_with("\n")
        condition = cond_tag.get_text().strip()
        # Убираем номер задачи в начале ("41. " → "")
        condition = re.sub(r'^\s*\d+\.\s*', '', condition).strip()

    # 2. ИИ-решебник
    ai_card = soup.find("div", class_="ai-card")
    if ai_card:
        ai_content = ai_card.find("div", class_="ai-card__content")
        if ai_content:
            solutions.append({
                "edition": "ИИ-решебник",
                "text": ai_content.get_text(separator="\n").strip()
            })

    # 3. Картинки решений (внутри figure с классом task-container)
    for fig in soup.find_all("figure", class_="task-container"):
        caption = fig.find("figcaption", class_="task-crumbs")
        raw_title = caption.get_text(" ", strip=True) if caption else "Решение"
        edition_title = re.sub(r'/\s*(задание|страница)\s*/\s*\d+', '', raw_title, flags=re.IGNORECASE).strip()

        images = []
        for img in fig.find_all("img"):
            src = img.get("src", "")
            if "/attachments/" in src and (".jpg" in src or ".jpeg" in src or ".webp" in src):
                full = urljoin("https://gdz.ru", src)
                if full not in images:
                    images.append(full)

        if images:
            solutions.append({
                "edition": edition_title if edition_title else "Решение",
                "images": images
            })

    return {"solutions": solutions, "condition": condition}


async def parse_tasks(jobs: list[dict]) -> list[dict]:
    results = []
    for job in jobs:
        subject = job["subject"]
        base = (job.get("url") or "").strip()
        raw_text = job.get("raw_text", "").strip()
        s_type = job.get("type", "numbers")

        if s_type == "text" or not base:
            results.append({
                "subject": subject, "number": None, "url": None, "solutions": [],
                "found": False, "raw_text": raw_text, "condition": "", "is_text": True
            })
            continue

        if not base.endswith("/"): base += "/"
        numbers = extract_numbers(job.get("numbers", ""))

        if not numbers:
            results.append({
                "subject": subject, "number": None, "url": None, "solutions": [],
                "found": False, "raw_text": raw_text, "condition": "", "is_text": True
            })
            continue

        for num in numbers:
            candidates = [
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
            ]

            solutions, used, condition = [], candidates[0], ""
            for url in candidates:
                data = await asyncio.to_thread(_get_page_data, url)
                # Если нашли хотя бы условие — запоминаем URL
                if data["solutions"] or data["condition"]:
                    solutions = data["solutions"]
                    condition = data["condition"]
                    used = url
                    # Если нашли и решения — выходим, дальше искать смысла нет
                    if data["solutions"]:
                        break

            results.append({
                "subject": subject,
                "number": num,
                "url": used,
                "solutions": solutions,
                "found": len(solutions) > 0,
                "raw_text": raw_text,
                "condition": condition,
                "is_text": False
            })

    return results