"""
Пакетная проверка парсера.
Запуск: python check.py <URL> <начало> <конец>
Пример: python check.py https://gdz.ru/class-7/geometria/merzljak/ 1 100
"""
import asyncio
import sys
import time
import json
from pathlib import Path

from parser import _get_images
from urllib.parse import urljoin


async def check_range(base_url: str, start: int, end: int):
    if not base_url.endswith("/"):
        base_url += "/"

    total = end - start + 1
    print(f"\n{'═' * 60}")
    print(f"Проверяем: {base_url}")
    print(f"Номера:    {start}–{end} (всего {total})")
    print(f"{'═' * 60}\n")

    found = []
    not_found = []
    errors = []

    t0 = time.time()

    for i, num in enumerate(range(start, end + 1), 1):
        urls = [
            urljoin(base_url, f"{num}-task/"),
            urljoin(base_url, f"{num}-s/"),
            urljoin(base_url, f"{num}-nom/"),
            urljoin(base_url, f"task-{num}/"),
        ]

        images = []
        used = urls[0]
        for url in urls:
            images = await asyncio.to_thread(_get_images, url)
            if images:
                used = url
                break

        if images:
            found.append({"number": num, "url": used, "image": images[0]})
            status = "✅"
        else:
            not_found.append(num)
            status = "❌"

        # Прогресс каждые 10 номеров
        if i % 10 == 0 or i == total:
            elapsed = time.time() - t0
            speed = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / speed if speed > 0 else 0
            print(f"[{i:>3}/{total}] №{num:<4} {status}  "
                  f"нашли: {len(found):>3}  "
                  f"нет: {len(not_found):>3}  "
                  f"({speed:.1f} зад/сек, осталось ~{int(eta)}с)")

    elapsed = time.time() - t0

    # ── Итоги ─────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"ИТОГИ:")
    print(f"{'═' * 60}")
    print(f"Всего проверено:   {total}")
    print(f"Найдено решений:   {len(found)} ({len(found)*100//total}%)")
    print(f"Не найдено:        {len(not_found)} ({len(not_found)*100//total}%)")
    print(f"Время:             {int(elapsed)} сек")
    print(f"Скорость:          {total/elapsed:.1f} зад/сек")
    print(f"{'═' * 60}\n")

    if not_found:
        # Компактный список "пропусков"
        ranges = []
        start_r = not_found[0]
        prev = not_found[0]
        for n in not_found[1:]:
            if n == prev + 1:
                prev = n
            else:
                ranges.append((start_r, prev))
                start_r = n
                prev = n
        ranges.append((start_r, prev))

        print("НЕ НАЙДЕНЫ (номера):")
        for a, b in ranges:
            if a == b:
                print(f"  • {a}")
            else:
                print(f"  • {a}–{b} ({b - a + 1} шт.)")
        print()

    # ── Сохраняем в файл ─────────────────────────────────
    result_file = Path("check_result.json")
    result_file.write_text(
        json.dumps({
            "base_url": base_url,
            "range": [start, end],
            "total": total,
            "found_count": len(found),
            "not_found_count": len(not_found),
            "not_found": not_found,
            "found": found,
            "elapsed_sec": round(elapsed, 1),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📄 Подробный результат: {result_file.resolve()}")


def main():
    if len(sys.argv) < 4:
        print("Использование: python check.py <URL> <начало> <конец>")
        print("Пример:        python check.py https://gdz.ru/class-7/geometria/merzljak/ 1 50")
        sys.exit(1)

    base_url = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])

    if start > end:
        start, end = end, start

    asyncio.run(check_range(base_url, start, end))


if __name__ == "__main__":
    main()