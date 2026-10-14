import re
import time
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gdz.ru/",
}

MIN_IMAGE_SIZE = 15000   # минимальный размер картинки-решения в байтах
MAX_ATTEMPTS = 3         # сколько раз пробовать страницу


def extract_numbers(text: str) -> list[str]:
    """Вытаскивает номера заданий, пропуская параграфы, даты и годы."""
    if not text:
        return []

    # Убираем параграфы: п.20, пункт 5, §7
    text = re.sub(r'(?:\bп\.|\bпункт|\b§)\s*\d+', ' ', text, flags=re.IGNORECASE)
    # Убираем даты: 12.10, 12.10.2026, 12/10, 12-10
    text = re.sub(r'\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?', ' ', text)
    # Убираем годы: 2020, 2015 и т.п.
    text = re.sub(r'\b(19|20)\d{2}\b', ' ', text)

    numbers = re.findall(r'\d{1,4}', text)
    seen, result = set(), []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _download_size(url: str) -> int:
    """Скачивает картинку и возвращает её размер в байтах (0 при ошибке)."""
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
        if r.status_code == 200:
            return len(r.content)
    except Exception as e:
        print(f"[parser] Ошибка скачивания {url}: {e}")
    return 0


def _get_images(url: str) -> list[str]:
    """
    Пробует до MAX_ATTEMPTS раз достать страницу задания.
    Возвращает ссылку на первую картинку-решение (не заглушку).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                impersonate="chrome",
                timeout=30,
            )
            if r.status_code != 200:
                print(f"[parser] {url} -> HTTP {r.status_code} (попытка {attempt})")
                time.sleep(2)
                continue
            html = r.text
        except Exception as e:
            print(f"[parser] Ошибка {url} (попытка {attempt}): {e}")
            time.sleep(2)
            continue

        soup = BeautifulSoup(html, "lxml")
        candidates, seen = [], set()
        for img in soup.find_all("img"):
            src = img.get("src")
            if src and "/attachments/images/tasks/" in src:
                full = urljoin(url, src)
                if full not in seen:
                    seen.add(full)
                    candidates.append(full)

        if not candidates:
            print(f"[parser] {url}: картинок не найдено (попытка {attempt})")
            time.sleep(2)
            continue

        # Ищем первую картинку с реальным содержимым
        for img_url in candidates:
            size = _download_size(img_url)
            print(f"[parser] {img_url} -> {size} байт")
            if size >= MIN_IMAGE_SIZE:
                return [img_url]

        # Все картинки оказались заглушками — пробуем ещё раз
        print(f"[parser] Все картинки мелкие, повтор через 2 сек... (попытка {attempt})")
        time.sleep(2)

    return []


async def parse_tasks(jobs: list[dict]) -> list[dict]:
    """
    jobs = [
        {"subject": "Алгебра", "url": "https://gdz.ru/.../", "numbers": "447,449"},
    ]
    """
    import asyncio

    results = []
    for job in jobs:
        subject = job["subject"]
        base = job["url"]
        if not base.endswith("/"):
            base += "/"

        numbers = extract_numbers(job.get("numbers", ""))
        for num in numbers:
            candidates = [
                urljoin(base, f"{num}-task/"),
                urljoin(base, f"{num}-s/"),
                urljoin(base, f"{num}-nom/"),
                urljoin(base, f"task-{num}/"),
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
            })

    return results