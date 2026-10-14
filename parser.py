import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_numbers(text: str) -> list[str]:
    """Вытаскивает все числа из строки, убирает дубли."""
    if not text:
        return []
    numbers = re.findall(r'\d{1,4}', text)
    seen, result = set(), []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


async def _get_images(page, url: str) -> list[str]:
    """Открывает страницу и достаёт картинки с решением."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        html = await page.content()
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "/attachments/images/tasks/" in src:
            full = urljoin(url, src)
            if full not in seen:
                seen.add(full)
                out.append(full)
    return out


async def parse_tasks(jobs: list[dict]) -> list[dict]:
    """jobs = [{"subject": "Алгебра", "url": "...", "numbers": "447,449"}]"""
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='ru-RU',
        )
        page = await context.new_page()

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
                    images = await _get_images(page, url)
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

        await browser.close()

    return results