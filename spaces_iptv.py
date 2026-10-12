
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IPTV Playlist Collector PRO
Python 3.10+

Запуск:
    python process_playlists.py

Вход:
    sources.txt                 (необязательно)

Результаты:
    processed/combined.m3u
    processed/combined_by_name.m3u
    processed/report.json
    processed/failed_sources.txt
    processed/sources_discovered.txt
"""

import concurrent.futures
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------- НАСТРОЙКИ ----------------

SOURCES_FILE = Path("sources.txt")
OUTPUT_DIR = Path("processed")

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT = 20
MAX_WORKERS = 6
PAUSE_SECONDS = 0.25

HEADERS = {
    "User-Agent": "IPTV-Playlist-Collector/2.0",
    "Accept": "*/*",
}

# Открытые репозитории и источники из предоставленного списка.
# Доступность проверяется во время запуска.
BUILTIN_SOURCES = [
    # Российские и общие
    "https://iptv.axenov.dev/so",
    "https://smolnp.github.io/IPTVru/IPTVru.m3u",
    "https://raw.githubusercontent.com/RomanTheQuant/IPtv/refs/heads/main/IPTV_SHARED.m3u",
    "https://pastebin.com/raw/d3mDM8Tv",
    "https://iptv.axenov.dev/mult",

    # Открытые публичные репозитории
    "https://iptv-org.github.io/iptv/index.m3u",
    "https://iptv-org.github.io/iptv/index.category.m3u",
    "https://iptv-org.github.io/iptv/index.language.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/categories/news.m3u",
    "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "https://iptv-org.github.io/iptv/categories/animation.m3u",
    "https://iptv-org.github.io/iptv/categories/documentary.m3u",

    # Российские подборки
    "https://ngrch.github.io/iptv/ru.m3u",
    "https://ngrch.github.io/iptv/music.m3u",
    "https://ngrch.github.io/iptv/cartoons.m3u",
    "https://raw.githubusercontent.com/substanc1/iptv-russia/main/streams/ru.m3u",
    "https://substanc1.github.io/iptv-russia/streams/ru.m3u",
    "https://raw.githubusercontent.com/denxvofficial/IPTV/refs/heads/main/iptv.m3u",
    "https://raw.githubusercontent.com/denxvofficial/IPTV/refs/heads/main/iptv-top.m3u",

    # Другие предоставленные источники
    "https://raw.githubusercontent.com/user797/Playlists/refs/heads/main/Cartoons.m3u8",
    "https://telekarta-tv.ru/wp-content/uploads/kids-all.m3u",
    "https://smart-iptv.ru/radio.m3u",
    "https://loganet.vercel.app/LoganetXAll.m3u",
    "https://tfrthuikijuyg.online/IPTV.m3u",
    "https://satexpresstv.ru/apk/setv.m3u",
    "https://shamilbro.ucoz.net/TV.m3u8",
    "https://shamilbro.ucoz.net/Wink.m3u8",
    "https://goo.su/KIp3",
    "https://raw.githubusercontent.com/Dimonovich/TV/Dimonovich/FREE/TV",
    "https://polzafre.short.gy/IPTV",
    "https://raw.githubusercontent.com/Dimonovich/TV/Dimonovich/FREE/FILMS",
    "https://github.com/suzukantv/IPTV/raw/refs/heads/main/SuzuSCloudFreeGedemTvNET.m3u",
    "https://raw.githubusercontent.com/IPTVSHARED/iptv/refs/heads/main/MEDIASHARED.m3u",
    "https://raw.githubusercontent.com/suzukantv/IPTV-FILM/refs/heads/main/mediateka.m3u",
    "https://raw.githubusercontent.com/suzukantv/IPTV/refs/heads/main/KolobokHatNet2.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
]

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_url(url):
    return url.strip().strip('"').strip("'")


def load_sources():
    """Объединяет встроенный список и пользовательский sources.txt."""
    candidates = list(BUILTIN_SOURCES)

    if SOURCES_FILE.exists():
        content = SOURCES_FILE.read_text(encoding="utf-8-sig")

        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            candidates.append(normalize_url(line))

    result = []
    seen = set()

    for url in candidates:
        if not url.startswith(("http://", "https://")):
            continue

        if url not in seen:
            result.append(url)
            seen.add(url)

    return result


def download_playlist(url):
    """Скачивает текст с ограничением размера и времени."""
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        status = response.status
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")

        data = response.read(MAX_BYTES + 1)

        if len(data) > MAX_BYTES:
            raise ValueError(
                f"Ответ превышает лимит {MAX_BYTES // 1024 // 1024} МБ"
            )

    text = data.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not text:
        raise ValueError("Пустой ответ")

    if re.search(r"<(?:!doctype\s+html|html)(?:\s|>)",
                 text[:4096], re.IGNORECASE):
        raise ValueError("Получена HTML-страница, а не M3U")

    if "#EXTINF" not in text[:65536]:
        raise ValueError(
            f"Не найдены записи EXTINF; Content-Type={content_type}"
        )

    return status, final_url, text


def parse_m3u(text):
    """
    Разбирает M3U на блоки.
    Сохраняет директивы между EXTINF и URL.
    """
    entries = []
    current = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            if current:
                entry = finish_entry(current)
                if entry:
                    entries.append(entry)

            current = [line]
            continue

        if current:
            current.append(line)

            if not line.startswith("#"):
                entry = finish_entry(current)

                if entry:
                    entries.append(entry)

                current = []

    if current:
        entry = finish_entry(current)
        if entry:
            entries.append(entry)

    return entries


def finish_entry(lines):
    """Проверяет блок и извлекает имя, URL и метаданные."""
    stream_url = next(
        (
            line for line in reversed(lines)
            if line.startswith(
                ("http://", "https://", "rtsp://", "rtmp://")
            )
        ),
        None,
    )

    if not stream_url:
        return None

    extinf = next(
        (line for line in lines if line.startswith("#EXTINF:")),
        "",
    )

    name = (
        extinf.rsplit(",", 1)[-1].strip()
        if "," in extinf else "Unknown"
    )

    return {
        "name": name or "Unknown",
        "url": stream_url,
        "lines": lines,
    }


def process_source(url):
    """Обрабатывает один источник. Возвращает данные для отчёта."""
    result = {
        "source": url,
        "status": None,
        "final_url": None,
        "channels": 0,
        "error": None,
        "entries": [],
        "elapsed_seconds": 0,
    }

    started = time.monotonic()

    try:
        status, final_url, text = download_playlist(url)
        entries = parse_m3u(text)

        result.update({
            "status": status,
            "final_url": final_url,
            "channels": len(entries),
            "entries": entries,
        })

        if not entries:
            result["error"] = "M3U распознан, но записи не найдены"

    except (HTTPError, URLError, TimeoutError, OSError,
            ValueError) as exc:
        result["error"] = str(exc)

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_seconds"] = round(
        time.monotonic() - started, 3
    )

    return result


def write_m3u(path, entries):
    """Сохраняет корректный M3U."""
    output = ["#EXTM3U"]

    for entry in entries:
        output.extend(entry["lines"])

    path.write_text(
        "\n".join(output) + "\n",
        encoding="utf-8",
    )


def main():
    sources = load_sources()

    if not sources:
        print("Нет источников для обработки.")
        return

    print(f"Всего источников: {len(sources)}")
    print(f"Параллельных загрузок: {MAX_WORKERS}")
    print("Начинаем проверку...\n")

    results = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        future_to_url = {
            executor.submit(process_source, url): url
            for url in sources
        }

        for number, future in enumerate(
            concurrent.futures.as_completed(future_to_url), 1
        ):
            result = future.result()
            results.append(result)

            if result["error"]:
                print(
                    f"[{number}/{len(sources)}] ОШИБКА "
                    f"{result['source']}: {result['error']}"
                )
            else:
                print(
                    f"[{number}/{len(sources)}] OK "
                    f"{result['channels']} записей; "
                    f"{result['elapsed_seconds']} с — "
                    f"{result['source']}"
                )

            time.sleep(PAUSE_SECONDS)

    # Стабильный порядок источников в отчёте.
    order = {url: i for i, url in enumerate(sources)}
    results.sort(key=lambda item: order[item["source"]])

    unique_entries = []
    seen_urls = set()
    seen_names = set()

    for result in results:
        for entry in result["entries"]:
            url_key = entry["url"].strip()

            if url_key in seen_urls:
                continue

            seen_urls.add(url_key)
            unique_entries.append(entry)

            # Отдельный индекс названий для диагностического отчёта.
            name_key = entry["name"].casefold().strip()
            if name_key:
                seen_names.add(name_key)

    # Основной плейлист: дублирующиеся URL удалены.
    write_m3u(
        OUTPUT_DIR / "combined.m3u",
        unique_entries,
    )

    # Дополнительный список: первый экземпляр каждого имени.
    by_name = []
    seen = set()

    for entry in unique_entries:
        key = entry["name"].casefold().strip()

        if key in seen:
            continue

        seen.add(key)
        by_name.append(entry)

    write_m3u(
        OUTPUT_DIR / "combined_by_name.m3u",
        by_name,
    )

    # Полный отчёт без объёмных блоков M3U.
    report = []

    for result in results:
        report.append({
            key: value
            for key, value in result.items()
            if key != "entries"
        })

    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    failed = [
        item for item in results
        if item["error"] or item["channels"] == 0
    ]

    (OUTPUT_DIR / "failed_sources.txt").write_text(
        "\n".join(item["source"] for item in failed) + "\n",
        encoding="utf-8",
    )

    (OUTPUT_DIR / "sources_discovered.txt").write_text(
        "\n".join(sources) + "\n",
        encoding="utf-8",
    )

    total_entries = sum(item["channels"] for item in results)
    success_count = len(results) - len(failed)

    print("\n========== ИТОГ ==========")
    print(f"Источников: {len(sources)}")
    print(f"Успешно разобрано: {success_count}")
    print(f"Ошибки/пустые результаты: {len(failed)}")
    print(f"Всего записей до дедупликации: {total_entries}")
    print(f"Уникальных URL: {len(unique_entries)}")
    print(f"Уникальных названий: {len(seen_names)}")
    print(f"По уникальным названиям: {len(by_name)}")
    print(f"Результаты: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
