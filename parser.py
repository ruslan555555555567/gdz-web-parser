import re
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gdz.ru/",
}

# Предметы, где основной контент — ТЕКСТ (не ищем номера)
TEXT_SUBJECTS = {
    "Литература", "История", "Музыка", "Труд (технология)",
    "Изобразительное искусство",
}

# Алиасы предметов (для "Разнообразие бактерий" → Биология)
SUBJECT_ALIASES = {
    "Биология": ["биология", "разнообразие бактерий", "бактерии", "грибы", "лишайники"],
    "Алгебра": ["алгебра"],
    "Геометрия": ["геометрия"],
    "Вероятность и статистика": ["вероятность", "статистика"],
    "Физика": ["физика"],
    "Информатика": ["информатика"],
    "География": ["география"],
    "Русский язык": ["русский"],
    "Английский язык": ["английский", "english", "иностранный"],
    "Литература": ["литература"],
    "История": ["история"],
    "Музыка": ["музыка"],
    "Труд (технология)": ["труд", "технология"],
    "Изобразительное искусство": ["изо", "изобразительное"],
}


def extract_numbers(text: str) -> list[str]:
    """Вытаскивает номера заданий, отсекая параграфы, даты, страницы."""
    if not text:
        return []

    # Параграфы: п.20, пункт 5, §7, "параграф 7"
    text = re.sub(r'(?:\bп\.|\bпункт|\b§|\bпараграф)\s*\d+', ' ', text, flags=re.IGNORECASE)
    # Даты: 12.10, 12.10.2026, 12/10, 12-10
    text = re.sub(r'\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?', ' ', text)
    # Годы: 2020, 2015
    text = re.sub(r'\b(19|20)\d{2}\b', ' ', text)
    # Страницы: "стр. 16-17", "страница 20"
    text = re.sub(r'(?:\bстр\.|\bстраниц[аы]?)\s*\d+(-\d+)?', ' ', text, flags=re.IGNORECASE)
    # Листы/объём: "1,5 листа", "2 листа"
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
        r = requests.get(
            url,
            headers=HEADERS,
            impersonate="chrome",
            timeout=20,
        )
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
    _, best_url = candidates[-1]
    return [best_url]


async def parse_tasks(jobs: list[dict]) -> list[dict]:
    """
    jobs = [
        {"subject": "Алгебра", "url": "...", "numbers": "447,449", "raw_text": "текст задания"},
    ]
    """
    import asyncio

    results = []
    for job in jobs:
        subject = job["subject"]
        base = job["url"]
        if not base.endswith("/"):
            base += "/"

        raw_text = job.get("raw_text", "").strip()
        is_text_subject = subject in TEXT_SUBJECTS

        # Для текстовых предметов — сразу возвращаем текст
        if is_text_subject:
            results.append({
                "subject": subject,
                "number": None,
                "url": None,
                "images": [],
                "found": False,
                "raw_text": raw_text,
            })
            continue

        numbers = extract_numbers(job.get("numbers", ""))

        # Если номеров нет, но есть текст — показываем текст
        if not numbers and raw_text:
            results.append({
                "subject": subject,
                "number": None,
                "url": None,
                "images": [],
                "found": False,
                "raw_text": raw_text,
            })
            continue

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
            })

    return results