from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin


async def get_solution_images(page_url: str) -> list[str]:
    """Открывает страницу задания и возвращает ссылки на картинки с решением."""
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
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            html = await page.content()
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "lxml")
    solutions = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "/attachments/images/tasks/" in src:
            full = urljoin(page_url, src)
            if full not in seen:
                seen.add(full)
                solutions.append(full)
    return solutions