
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import csv
import re
from urllib.parse import urljoin, urlparse

import aiohttp

HOSTS = [
    "https://cdn.ntv.ru",
    "https://cdn2.ntv.ru",
    "https://cdn3.ntv.ru",
    "https://cdn-dvr.ntv.ru",
]

# Известные и опубликованные пути для первичной проверки.
# Это список кандидатов, а не подтверждение их доступности.
PATHS = [
    "ntv0/playlist.m3u8",
    "ntv0_hd/playlist.m3u8",
    "ntv1/playlist.m3u8",
    "ntv2/playlist.m3u8",
    "ntv4/playlist.m3u8",
    "ntv7/playlist.m3u8",
    "vitrina/index.m3u8",
    "ntv-spb/index.m3u8",
    "th_hit/playlist.m3u8",
    "th_serial/playlist.m3u8",
    "th_style/playlist.m3u8",
    "th_pravo/playlist.m3u8",
    "unknown_russia/playlist.m3u8",
    "sledstvie_veli/playlist.m3u8",
    "svoya_igra/playlist.m3u8",
    "playlist.m3u8",
    "index.m3u8",
    "master.m3u8",
]

TARGETS = {
    "НТВ Следствие вели…": (
        "следствие вели", "sledstvie", "sledstvie_veli"
    ),
    "НТВ Своя игра": (
        "своя игра", "svoya_igra", "svoya-igra"
    ),
}

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6)
MAX_BODY = 1_000_000
CONCURRENCY = 12
UA = "Mozilla/5.0 NTVPlaylistAudit/2.0"


def is_ntv_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "ntv.ru" or host.endswith(".ntv.ru")


def parse_playlist(text, base_url):
    """Разбирает HLS master/media playlist и сохраняет EXTINF."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    is_master = any(
        x.startswith("#EXT-X-STREAM-INF")
        or x.startswith("#EXT-X-I-FRAME-STREAM-INF")
        for x in lines
    )

    children = []
    entries = []
    pending_extinf = None
    pending_stream = False

    for line in lines:
        if line.startswith("#EXTINF:"):
            pending_extinf = line
            continue

        if line.startswith("#EXT-X-STREAM-INF"):
            pending_stream = True
            continue

        if line.startswith("#"):
            # URI="..." иногда содержится прямо в атрибуте тега.
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                children.append(urljoin(base_url, match.group(1)))
            continue

        url = urljoin(base_url, line)
        if not url.lower().split("?", 1)[0].endswith(".m3u8"):
            continue

        children.append(url)
        if pending_extinf:
            entries.append((pending_extinf, url))
            pending_extinf = None

        pending_stream = False

    return is_master, list(dict.fromkeys(children)), entries


def target_name(*values):
    text = " ".join(str(x or "") for x in values).casefold()
    text = text.replace("ё", "е")

    for label, aliases in TARGETS.items():
        if any(a.replace("ё", "е") in text for a in aliases):
            return label
    return None


async def fetch(session, sem, url):
    async with sem:
        try:
            async with session.get(
                url, allow_redirects=True,
                headers={"User-Agent": UA},
            ) as r:
                body = await r.content.read(MAX_BODY)
                text = body.decode("utf-8", errors="replace")
                hls = "#EXTM3U" in text[:4096]
                master, children, entries = (
                    parse_playlist(text, str(r.url))
                    if hls else (False, [], [])
                )

                return {
                    "url": url,
                    "final_url": str(r.url),
                    "status": r.status,
                    "content_type": r.headers.get("Content-Type", ""),
                    "bytes": len(body),
                    "hls": hls,
                    "master": master,
                    "children": children,
                    "entries": entries,
                    "body": text if hls else "",
                    "error": "",
                }
        except Exception as e:
            return {
                "url": url, "final_url": "", "status": 0,
                "content_type": "", "bytes": 0,
                "hls": False, "master": False,
                "children": [], "entries": [], "body": "",
                "error": f"{type(e).__name__}: {e}",
            }


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    headers = {"User-Agent": UA}

    candidates = {
        f"{host}/{path}"
        for host in HOSTS for path in PATHS
    }

    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=headers,
    ) as session:
        print(f"Первичная проверка: {len(candidates)} URL")
        results = await asyncio.gather(
            *(fetch(session, sem, u) for u in sorted(candidates))
        )

        # Обход дочерних HLS URL только на доменах NTV.
        seen = {x["url"] for x in results}
        for depth in range(2):
            next_urls = set()
            for item in results:
                for child in item["children"]:
                    if is_ntv_url(child) and child not in seen:
                        next_urls.add(child)

            if not next_urls:
                break

            seen.update(next_urls)
            print(f"Уровень {depth + 1}: найдено ссылок {len(next_urls)}")
            batch = await asyncio.gather(
                *(fetch(session, sem, u) for u in sorted(next_urls))
            )
            results.extend(batch)

    # CSV: каждый проверенный URL и результат ответа.
    with open("ntv_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        fields = [
            "url", "final_url", "status", "content_type",
            "bytes", "hls", "master", "error",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in fields})

    # M3U: сохраняем реальные EXTINF, найденные в ответах.
    # Для потоков без EXTINF используем нейтральную подпись,
    # а не угадываем название канала по идентификатору.
    streams = {}
    for r in results:
        for extinf, url in r["entries"]:
            streams[url] = extinf

    for r in results:
        if r["hls"] and not r["master"]:
            url = r["final_url"] or r["url"]
            streams.setdefault(
                url,
                "#EXTINF:-1 group-title=\"NTV audit\",NTV поток (название не указано)"
            )

    with open("ntv_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for url, extinf in sorted(streams.items()):
            f.write(extinf + "\n" + url + "\n")

    # Сохраняем содержимое master-плейлистов для диагностики.
    with open("ntv_masters.txt", "w", encoding="utf-8") as f:
        for r in results:
            if r["master"]:
                f.write(f"URL: {r['url']}\n")
                f.write(r["body"] + "\n\n" + "=" * 70 + "\n\n")

    # Отдельная сводка по двум искомым каналам.
    with open("ntv_target_channels.txt", "w", encoding="utf-8") as f:
        for label, aliases in TARGETS.items():
            hits = []
            for r in results:
                haystack = (
                    r["url"] + " " + r["final_url"] + " " + r["body"]
                ).casefold().replace("ё", "е")
                if any(a.replace("ё", "е") in haystack for a in aliases):
                    hits.append(r)

            f.write(label + "\n")
            if not hits:
                f.write("Совпадений в проверенных ответах не найдено.\n")
            else:
                for r in hits:
                    f.write(
                        f"HTTP={r['status']} HLS={r['hls']} "
                        f"URL={r['final_url'] or r['url']}\n"
                    )
            f.write("\n")

    print("\nСканирование завершено.")
    print(f"Проверено URL: {len(results)}")
    print(f"HTTP 200: {sum(r['status'] == 200 for r in results)}")
    print(f"HLS-ответов: {sum(r['hls'] for r in results)}")
    print(f"Master playlist: {sum(r['master'] for r in results)}")
    print("Созданы ntv_report.csv, ntv_found.m3u,")
    print("ntv_masters.txt и ntv_target_channels.txt")


if __name__ == "__main__":
    asyncio.run(main())
