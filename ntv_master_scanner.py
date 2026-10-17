
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NTV CDN Master Playlist Auditor
--------------------------------
Проверяет известные пути на CDN NTV, рекурсивно разбирает HLS,
сохраняет фактические ответы и собирает единый M3U.

Выходные файлы:
  ntv_report.csv          Все HTTP-проверки
  ntv_found.m3u           Найденные HLS-потоки
  ntv_masters.txt         Содержимое обнаруженных master playlists
  ntv_target_channels.txt Отчёт по «Следствие вели…» и «Своя игра»
  ntv_streams.csv         Отдельный список вариантов потоков

Примечание:
  Перебор известных путей не означает полный перебор CDN.
  В скрипте нет обхода авторизации или ограничений доступа.
"""

import asyncio
import csv
import re
from collections import deque
from urllib.parse import urljoin, urlparse

import aiohttp


HOSTS = [
    "https://cdn.ntv.ru",
    "https://cdn2.ntv.ru",
    "https://cdn3.ntv.ru",
    "https://cdn-dvr.ntv.ru",
]

PATHS = [
    "ntv0/playlist.m3u8",
    "ntv0/index.m3u8",
    "ntv0/master.m3u8",
    "ntv0_hd/playlist.m3u8",
    "ntv1/playlist.m3u8",
    "ntv2/playlist.m3u8",
    "ntv4/playlist.m3u8",
    "ntv7/playlist.m3u8",
    "vitrina/playlist.m3u8",
    "vitrina/index.m3u8",
    "ntv-spb/playlist.m3u8",
    "ntv-spb/index.m3u8",
    "th_hit/playlist.m3u8",
    "th_hit/index.m3u8",
    "th_serial/playlist.m3u8",
    "th_serial/index.m3u8",
    "th_style/playlist.m3u8",
    "th_style/index.m3u8",
    "th_pravo/playlist.m3u8",
    "th_pravo/index.m3u8",
    "unknown_russia/playlist.m3u8",
    "unknown_russia/index.m3u8",
    "sledstvie_veli/playlist.m3u8",
    "svoya_igra/playlist.m3u8",
    "playlist.m3u8",
    "index.m3u8",
    "master.m3u8",
]

CHANNEL_LABELS = {
    "ntv0": "НТВ",
    "ntv0_hd": "НТВ HD",
    "ntv1": "НТВ — вариант 1",
    "ntv2": "НТВ — вариант 2",
    "ntv4": "НТВ — вариант 4",
    "ntv7": "НТВ — вариант 7",
    "vitrina": "НТВ Витрина",
    "ntv-spb": "НТВ — Санкт-Петербург",
    "th_hit": "НТВ-Хит",
    "th_serial": "НТВ Сериал",
    "th_style": "НТВ Стиль жизни",
    "th_pravo": "НТВ Право",
    "unknown_russia": "Неизвестная Россия",
    "sledstvie_veli": "НТВ Следствие вели…",
    "sledstvie": "НТВ Следствие вели…",
    "sledstvieveli": "НТВ Следствие вели…",
    "svoya_igra": "НТВ Своя игра",
    "svoyaigra": "НТВ Своя игра",
    "svoya-igra": "НТВ Своя игра",
}

TARGETS = {
    "НТВ Следствие вели…": (
        "следствие вели",
        "sledstvie_veli",
        "sledstvieveli",
    ),
    "НТВ Своя игра": (
        "своя игра",
        "svoya_igra",
        "svoyaigra",
        "svoya-igra",
    ),
}

TIMEOUT = aiohttp.ClientTimeout(total=18, connect=7)
MAX_BODY = 1_500_000
CONCURRENCY = 12
MAX_DEPTH = 4
MAX_URLS = 1500
UA = "Mozilla/5.0 NTV-CDN-Auditor/3.0"


def normalize_text(value):
    return str(value or "").casefold().replace("ё", "е")


def is_ntv_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "ntv.ru" or host.endswith(".ntv.ru")


def channel_from_url(url):
    """Название только по известному идентификатору в пути URL."""
    parts = [
        p.lower()
        for p in urlparse(url).path.strip("/").split("/")
        if p
    ]

    for part in reversed(parts):
        if part in CHANNEL_LABELS:
            return CHANNEL_LABELS[part]

    return ""


def detect_target(*values):
    text = normalize_text(" ".join(str(v or "") for v in values))

    for label, aliases in TARGETS.items():
        for alias in aliases:
            if normalize_text(alias) in text:
                return label

    return ""


def parse_attributes(line):
    """Разбирает атрибуты HLS, включая значения в кавычках."""
    result = {}
    for match in re.finditer(
        r'([A-Z0-9-]+)=("[^"]*"|[^,]*)',
        line,
    ):
        value = match.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[match.group(1)] = value
    return result


def parse_playlist(text, base_url):
    """
    Возвращает:
      is_master, children, variants, extinf_entries

    variants содержит URI вариантов качества и атрибуты
    предшествующего EXT-X-STREAM-INF.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    is_master = any(
        line.startswith((
            "#EXT-X-STREAM-INF:",
            "#EXT-X-I-FRAME-STREAM-INF:",
            "#EXT-X-MEDIA:",
        ))
        for line in lines
    )

    children = []
    variants = []
    extinf_entries = []
    pending_stream = None
    pending_extinf = None

    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_stream = parse_attributes(line)
            continue

        if line.startswith("#EXT-X-I-FRAME-STREAM-INF:"):
            attrs = parse_attributes(line)
            uri = attrs.get("URI")
            if uri:
                variants.append({
                    "url": urljoin(base_url, uri),
                    "attrs": attrs,
                })
                children.append(urljoin(base_url, uri))
            continue

        if line.startswith("#EXTINF:"):
            pending_extinf = line
            continue

        if line.startswith("#"):
            attrs = parse_attributes(line)
            uri = attrs.get("URI")
            if uri and line.startswith("#EXT-X-MEDIA:"):
                child = urljoin(base_url, uri)
                children.append(child)
            continue

        child = urljoin(base_url, line)
        if urlparse(child).scheme not in ("http", "https"):
            pending_stream = None
            pending_extinf = None
            continue

        # URI после EXT-X-STREAM-INF — вариант качества.
        if pending_stream is not None:
            variants.append({
                "url": child,
                "attrs": pending_stream,
            })
            pending_stream = None

        # URI после EXTINF — запись обычного M3U.
        if pending_extinf is not None:
            extinf_entries.append({
                "extinf": pending_extinf,
                "url": child,
            })
            pending_extinf = None

        if child.lower().split("?", 1)[0].endswith(".m3u8"):
            children.append(child)

    return (
        is_master,
        list(dict.fromkeys(children)),
        variants,
        extinf_entries,
    )


async def fetch(session, sem, url):
    async with sem:
        try:
            async with session.get(
                url,
                allow_redirects=True,
                headers={"User-Agent": UA},
            ) as response:
                body = await response.content.read(MAX_BODY)
                text = body.decode("utf-8", errors="replace")
                hls = "#EXTM3U" in text[:4096]

                if hls:
                    master, children, variants, entries = parse_playlist(
                        text, str(response.url)
                    )
                else:
                    master, children, variants, entries = (
                        False, [], [], []
                    )

                return {
                    "url": url,
                    "final_url": str(response.url),
                    "status": response.status,
                    "content_type": response.headers.get(
                        "Content-Type", ""
                    ),
                    "bytes": len(body),
                    "hls": hls,
                    "master": master,
                    "children": children,
                    "variants": variants,
                    "entries": entries,
                    "body": text if hls else "",
                    "error": "",
                }

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return {
                "url": url,
                "final_url": "",
                "status": 0,
                "content_type": "",
                "bytes": 0,
                "hls": False,
                "master": False,
                "children": [],
                "variants": [],
                "entries": [],
                "body": "",
                "error": f"{type(exc).__name__}: {exc}",
            }


def quality_label(attrs):
    resolution = attrs.get("RESOLUTION", "")
    bandwidth = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH", "")
    codecs = attrs.get("CODECS", "")

    parts = []
    if resolution:
        parts.append(resolution)
    if bandwidth.isdigit():
        parts.append(f"{int(bandwidth) // 1000} kbps")
    if codecs:
        parts.append(codecs)

    return " / ".join(parts)


def make_extinf(name, attrs=None, group="NTV"):
    attrs = attrs or {}
    details = quality_label(attrs)

    if not name:
        name = "NTV поток — имя не определено"

    if details:
        name = f"{name} [{details}]"

    safe_group = group.replace('"', "'")
    safe_name = name.replace("\r", " ").replace("\n", " ")

    return f'#EXTINF:-1 group-title="{safe_group}",{safe_name}'


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    timeout = TIMEOUT

    candidates = {
        f"{host.rstrip('/')}/{path.lstrip('/')}"
        for host in HOSTS
        for path in PATHS
    }

    results = []
    by_url = {}
    queue = deque((url, 0, "") for url in sorted(candidates))
    queued = set(candidates)

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={"User-Agent": UA},
    ) as session:
        while queue and len(by_url) < MAX_URLS:
            current_batch = []

            while queue and len(current_batch) < CONCURRENCY:
                url, depth, inherited_name = queue.popleft()
                if url in by_url:
                    continue
                current_batch.append((url, depth, inherited_name))

            if not current_batch:
                break

            print(
                f"Проверка: {len(by_url) + len(current_batch)} "
                f"/ лимит {MAX_URLS}"
            )

            fetched = await asyncio.gather(
                *(fetch(session, sem, item[0]) for item in current_batch)
            )

            for (url, depth, inherited_name), result in zip(
                current_batch, fetched
            ):
                result["depth"] = depth
                result["inherited_name"] = inherited_name
                by_url[url] = result
                results.append(result)

                if depth >= MAX_DEPTH:
                    continue

                parent_name = (
                    channel_from_url(url)
                    or channel_from_url(result["final_url"])
                    or inherited_name
                )

                for child in result["children"]:
                    if (
                        is_ntv_url(child)
                        and child not in queued
                        and child not in by_url
                        and len(queued) < MAX_URLS
                    ):
                        queued.add(child)
                        queue.append((child, depth + 1, parent_name))

    # Собираем потоки и варианты качества.
    streams = {}

    for result in results:
        parent_name = (
            channel_from_url(result["url"])
            or channel_from_url(result["final_url"])
            or result["inherited_name"]
        )

        for variant in result["variants"]:
            url = variant["url"]
            if not is_ntv_url(url):
                continue

            name = (
                channel_from_url(url)
                or parent_name
                or "NTV поток — имя не определено"
            )
            attrs = variant["attrs"]
            streams[url] = {
                "url": url,
                "name": name,
                "group": "НТВ",
                "resolution": attrs.get("RESOLUTION", ""),
                "bandwidth": attrs.get("AVERAGE-BANDWIDTH")
                or attrs.get("BANDWIDTH", ""),
                "codecs": attrs.get("CODECS", ""),
                "source_url": result["url"],
                "target": detect_target(name, url),
                "extinf": make_extinf(name, attrs, "НТВ"),
            }

        for entry in result["entries"]:
            url = entry["url"]
            if not is_ntv_url(url):
                continue

            extinf = entry["extinf"]
            name = extinf.rsplit(",", 1)[-1].strip() if "," in extinf else ""
            name = name or parent_name or "NTV поток — имя не определено"

            streams.setdefault(url, {
                "url": url,
                "name": name,
                "group": "НТВ",
                "resolution": "",
                "bandwidth": "",
                "codecs": "",
                "source_url": result["url"],
                "target": detect_target(name, url, extinf),
                "extinf": extinf,
            })

        # Медиа-плейлист тоже может быть конечным URL потока.
        # Не добавляем master как отдельный канал, если он содержит варианты.
        if result["hls"] and not result["master"]:
            url = result["final_url"] or result["url"]
            if is_ntv_url(url):
                name = (
                    channel_from_url(url)
                    or parent_name
                    or "NTV поток — имя не определено"
                )
                streams.setdefault(url, {
                    "url": url,
                    "name": name,
                    "group": "НТВ",
                    "resolution": "",
                    "bandwidth": "",
                    "codecs": "",
                    "source_url": result["url"],
                    "target": detect_target(name, url, result["body"]),
                    "extinf": make_extinf(name, group="НТВ"),
                })

    # Отчёт всех HTTP-проверок.
    report_fields = [
        "url", "final_url", "status", "content_type",
        "bytes", "hls", "master", "depth", "error",
    ]
    with open("ntv_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=report_fields)
        writer.writeheader()
        for result in results:
            writer.writerow({
                key: result.get(key, "")
                for key in report_fields
            })

    # Единый M3U.
    with open("ntv_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for stream in sorted(streams.values(), key=lambda x: (
            x["name"], x["url"]
        )):
            f.write(stream["extinf"] + "\n")
            f.write(stream["url"] + "\n")

    # Все найденные мастер-плейлисты.
    with open("ntv_masters.txt", "w", encoding="utf-8") as f:
        for result in results:
            if result["master"]:
                f.write(f"URL: {result['url']}\n")
                f.write(result["body"])
                f.write("\n\n" + "=" * 70 + "\n\n")

    # Список вариантов качества и источников.
    stream_fields = [
        "name", "url", "resolution", "bandwidth", "codecs",
        "source_url", "target",
    ]
    with open("ntv_streams.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=stream_fields)
        writer.writeheader()
        for stream in sorted(streams.values(), key=lambda x: (
            x["name"], x["url"]
        )):
            writer.writerow({
                key: stream.get(key, "")
                for key in stream_fields
            })

    # Отдельный отчёт по двум искомым каналам.
    with open("ntv_target_channels.txt", "w", encoding="utf-8") as f:
        for label, aliases in TARGETS.items():
            hits = []

            for result in results:
                haystack = normalize_text(
                    result["url"] + " "
                    + result["final_url"] + " "
                    + result["body"]
                )
                if any(normalize_text(alias) in haystack for alias in aliases):
                    hits.append((
                        result["status"],
                        result["hls"],
                        result["url"],
                        result["final_url"],
                    ))

            for stream in streams.values():
                if stream["target"] == label:
                    hits.append((
                        "stream",
                        True,
                        stream["source_url"],
                        stream["url"],
                    ))

            # Убираем дубли записей отчёта.
            hits = list(dict.fromkeys(hits))
            f.write(label + "\n")

            if not hits:
                f.write(
                    "Совпадений в проверенных ответах не найдено. "
                    "Это не доказывает отсутствие канала на CDN.\n"
                )
            else:
                for status, hls, source, url in hits:
                    f.write(
                        f"STATUS={status} HLS={hls}\n"
                        f"Источник: {source}\n"
                        f"URL: {url}\n\n"
                    )

            f.write("\n")

    # Сводка.
    print("\nСканирование завершено.")
    print(f"Проверено URL: {len(results)}")
    print(f"HTTP 200: {sum(r['status'] == 200 for r in results)}")
    print(f"HLS-ответов: {sum(r['hls'] for r in results)}")
    print(f"Master playlists: {sum(r['master'] for r in results)}")
    print(f"Уникальных потоков: {len(streams)}")
    print("Созданы:")
    print("  ntv_report.csv")
    print("  ntv_found.m3u")
    print("  ntv_masters.txt")
    print("  ntv_target_channels.txt")
    print("  ntv_streams.csv")


if __name__ == "__main__":
    asyncio.run(main())

