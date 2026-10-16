import re
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gdz.ru/",
}


def extract_numbers(text: str) -> list[str]:
    """Вытаскивает номера заданий, отсекая параграфы, даты, страницы."""
    if not text:
        return []
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


def _get_images(url: str) -> list[str]:
    """Возвращает картинку с самым свежим годом издания."""
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
        if r.status_code != 200:
            return []
        html = r.text
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    candidates = []
    seen = set()

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or "/attachments/images/tasks/" not in src:
            continue
        full = urljoin(url, src)
        if full in seen:
            continue
        seen.add(full)

        year = None
        parent = img.find_parent()
        for _ in range(4):
            if parent is None:
                break
            text = parent.get_text(" ", strip=True)
            years = re.findall(r'\b(20\d{2})\b', text)
            if years:
                year = max(int(y) for y in years)
                break
            parent = parent.find_parent()

        if year is None:
            alt = img.get("alt", "") + " " + img.get("title", "")
            years = re.findall(r'\b(20\d{2})\b', alt)
            if years:
                year = max(int(y) for y in years)

        candidates.append((year or 0, full))

    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0])
    return [candidates[-1][1]]


async def parse_tasks(jobs: list[dict]) -> list[dict]:
    """
    jobs = [
        {"subject": "Алгебра", "url": "...", "numbers": "447,449",
         "raw_text": "...", "type": "numbers"},
    ]
    """
    import asyncio

    results = []
    for job in jobs:
        subject = job["subject"]
        base = (job.get("url") or "").strip()
        raw_text = job.get("raw_text", "").strip()
        s_type = job.get("type", "numbers")

        # Текстовые предметы — сразу показываем текст
        if s_type == "text" or not base:
            results.append({
                "subject": subject,
                "number": None,
                "url": None,
                "images": [],
                "found": False,
                "raw_text": raw_text,
                "is_text": True,
            })
            continue

        if not base.endswith("/"):
            base += "/"

        numbers = extract_numbers(job.get("numbers", ""))

        # Нет номеров, но есть текст
        if not numbers:
            results.append({
                "subject": subject,
                "number": None,
                "url": None,
                "images": [],
                "found": False,
                "raw_text": raw_text,
                "is_text": True,
            })
            continue

        # Ищем каждый номер
        for num in numbers:
            candidates = [
                urljoin(base, f"{num}-task/"),
                urljoin(base, f"{num}-s/"),
                urljoin(base, f"{num}-nom/"),
                urljoin(base, f"task-{num}/"),
                urljoin(base, f"1-1-prt-{num}/"),
                urljoin(base, f"1-2-prt-{num}/"),
                urljoin(base, f"2-1-prt-{num}/"),
                urljoin(base, f"2-2-prt-{num}/"),
            ]
            images, used = [], candidates[0]
            for url in candidates:
                images = await asyncio.to_thread(_get_images, url)
                if images:
                    used = url
                    break

            results.append({
                "subject": subject,
                "number": num,
                "url": used,
                "images": images,
                "found": len(images) > 0,
                "raw_text": raw_text,
                "is_text": False,
            })

    return results