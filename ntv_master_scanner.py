
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NTV CDN playlist auditor
Проверяет известные пути на cdn.ntv.ru, cdn2.ntv.ru, cdn3.ntv.ru,
ищет HLS master/media playlists и сохраняет отчёты.

Файлы:
  ntv_report.csv       — результаты HTTP-проверок
  ntv_found.m3u        — найденные HLS-ссылки
  ntv_masters.txt      — ответы, похожие на master playlist
"""

import asyncio
import csv
import re
import sys
from urllib.parse import urljoin, urlparse

import aiohttp


HOSTS = [
    "https://cdn.ntv.ru",
    "https://cdn2.ntv.ru",
    "https://cdn3.ntv.ru",
]

# Известные идентификаторы каналов — стартовый список,
# а не утверждение, что каждый путь существует на каждом узле.
CHANNELS = [
    "ntv0", "ntv0_hd", "ntv1", "ntv2", "ntv4", "ntv7",
    "th_hit", "th_serial", "th_style", "th_pravo",
    "unknown_russia", "sledstvie_veli", "svoya_igra",
]

PLAYLIST_NAMES = [
    "playlist.m3u8",
    "index.m3u8",
    "master.m3u8",
]

TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5)
CONCURRENCY = 12
MAX_BODY = 1_000_000
USER_AGENT = "Mozilla/5.0 NTV-Playlist-Auditor/1.0"

# Дополнительные типовые пути. Не перебираем случайные имена
# и не пытаемся обходить авторизацию или ограничения сервера.
EXTRA_PATHS = [
    "/playlist.m3u8",
    "/index.m3u8",
    "/master.m3u8",
    "/ntv/playlist.m3u8",
]


def build_candidates():
    urls = set(EXTRA_PATHS)

    for channel in CHANNELS:
        for name in PLAYLIST_NAMES:
            urls.add(f"/{channel}/{name}")

    result = []
    for host in HOSTS:
        for path in sorted(urls):
            result.append(host + path)

    return result


def parse_hls(text, base_url):
    """Возвращает дочерние HLS-ссылки и признак master playlist."""
    children = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    is_master = any(
        line.startswith("#EXT-X-STREAM-INF")
        or line.startswith("#EXT-X-I-FRAME-STREAM-INF")
        for line in lines
    )

    for line in lines:
        if line.startswith("#"):
            # URI может быть внутри атрибута URI="..."
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                children.append(urljoin(base_url, match.group(1)))
            continue

        if line.lower().startswith(("http://", "https://", "/", "./", "../")):
            children.append(urljoin(base_url, line))

    return is_master, list(dict.fromkeys(children))


async def fetch(session, sem, url):
    async with sem:
        try:
            async with session.get(
                url,
                allow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as response:
                body = await response.content.read(MAX_BODY)
                text = body.decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
                looks_hls = (
                    "#EXTM3U" in text[:4096]
                    or "mpegurl" in content_type.lower()
                )
                is_master, children = parse_hls(text, str(response.url)) \
                    if looks_hls else (False, [])

                return {
                    "url": url,
                    "final_url": str(response.url),
                    "status": response.status,
                    "content_type": content_type,
                    "bytes_read": len(body),
                    "looks_hls": looks_hls,
                    "is_master": is_master,
                    "children": children,
                    "error": "",
                    "body": text if looks_hls else "",
                }

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return {
                "url": url,
                "final_url": "",
                "status": 0,
                "content_type": "",
                "bytes_read": 0,
                "looks_hls": False,
                "is_master": False,
                "children": [],
                "error": type(exc).__name__ + ": " + str(exc),
                "body": "",
            }


async def main():
    candidates = build_candidates()
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)

    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=headers,
    ) as session:
        print(f"Проверяю кандидатов: {len(candidates)}")
        results = await asyncio.gather(
            *(fetch(session, sem, url) for url in candidates)
        )

        # Разбираем найденные master-плейлисты на один уровень вниз.
        child_urls = set()
        for item in results:
            if item["is_master"]:
                child_urls.update(item["children"])

        child_urls = {
            u for u in child_urls
            if urlparse(u).scheme in ("http", "https")
            and urlparse(u).hostname
            and urlparse(u).hostname.endswith("ntv.ru")
        }
        child_urls -= {r["url"] for r in results}

        print(f"Найдено дочерних ссылок NTV: {len(child_urls)}")
        if child_urls:
            child_results = await asyncio.gather(
                *(fetch(session, sem, u) for u in sorted(child_urls))
            )
            results.extend(child_results)

    # CSV-отчёт
    columns = [
        "url", "final_url", "status", "content_type", "bytes_read",
        "looks_hls", "is_master", "child_count", "error",
    ]
    with open("ntv_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in results:
            row = {key: item.get(key, "") for key in columns}
            row["child_count"] = len(item["children"])
            writer.writerow(row)

    # Все обнаруженные HLS-URL
    found = {}
    for item in results:
        if item["looks_hls"]:
            found[item["final_url"] or item["url"]] = item

    with open("ntv_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for url, item in sorted(found.items()):
            name = urlparse(url).path.strip("/").replace("/", " / ") or url
            f.write(f'#EXTINF:-1 group-title="NTV audit",{name}\n{url}\n')

    with open("ntv_masters.txt", "w", encoding="utf-8") as f:
        for item in results:
            if item["is_master"]:
                f.write(f'URL: {item["url"]}\n')
                f.write(item["body"])
                f.write("\n\n" + "-" * 72 + "\n\n")

    good = sum(1 for x in results if x["status"] == 200)
    hls = sum(1 for x in results if x["looks_hls"])
    masters = sum(1 for x in results if x["is_master"])

    print("\nГотово.")
    print(f"Проверено URL: {len(results)}")
    print(f"HTTP 200: {good}")
    print(f"Похоже на HLS: {hls}")
    print(f"Master-плейлистов: {masters}")
    print("Отчёты: ntv_report.csv, ntv_found.m3u, ntv_masters.txt")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        sys.exit(130)
