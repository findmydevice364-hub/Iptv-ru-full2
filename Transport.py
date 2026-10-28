
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

URLS = [
    "http://stream.mcquack.net/412/index.m3u8",
    "http://stream0.mcquack.net/412/index.m3u8",
    "http://stream1.mcquack.net/412/index.m3u8",
    "http://stream2.mcquack.net/412/index.m3u8",
    "http://stream3.mcquack.net/412/index.m3u8",
    "http://stream4.mcquack.net/412/index.m3u8",
    "http://stream5.mcquack.net/412/index.m3u8",
    "http://stream6.mcquack.net/412/index.m3u8",
    "http://stream7.mcquack.net/412/index.m3u8",
    "http://stream8.mcquack.net/412/index.m3u8",
    "http://stream9.mcquack.net/412/index.m3u8",
    "http://stream.mcquack.net/412/tracks-v1a1/mono.ts.m3u8?ads=1&filter.tracks=v1a1",
    "http://stream.mcquack.net/412/tracks-v1a1/mono.ts.m3u8",
    "http://stream.mcquack.net/412/tracks-v1a1/mono.ts.m3u8?ads=0&filter.tracks=v1a1",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, */*",
}

HTTP_TIMEOUT = (5, 10)
FFPROBE_TIMEOUT = 12
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
MAX_VARIANTS_TO_PROBE = 3

stats = {
    "total": 0,
    "http_ok": 0,
    "valid_playlists": 0,
    "master_playlists": 0,
    "media_playlists": 0,
    "failed": 0,
    "ffprobe_ok": 0,
    "ffprobe_failed": 0,
}


def log(message=""):
    print(message, flush=True)


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


def shorten(value, limit=300):
    value = str(value)
    return value if len(value) <= limit else value[:limit] + "..."


def format_duration(value):
    try:
        seconds = int(float(value))
        if seconds < 0:
            return "неизвестна"
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return (
            f"{hours:02}:{minutes:02}:{seconds:02}"
            if hours
            else f"{minutes:02}:{seconds:02}"
        )
    except (TypeError, ValueError, OverflowError):
        return "не указана"


def format_bitrate(value):
    try:
        return f"{int(value) / 1_000_000:.2f} Мбит/с"
    except (TypeError, ValueError, OverflowError):
        return "не указан"


def parse_attributes(text):
    """Разбор атрибутов HLS с учётом кавычек."""
    parts = []
    current = []
    quoted = False

    for char in text:
        if char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    if current:
        parts.append("".join(current).strip())

    result = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip().strip('"')

    return result


def is_valid_playlist(content):
    lines = [
        line.strip()
        for line in content.lstrip("\ufeff").splitlines()
        if line.strip()
    ]
    return bool(lines and lines[0] == "#EXTM3U")


def analyze_playlist(content, base_url):
    lines = [
        line.strip()
        for line in content.lstrip("\ufeff").splitlines()
        if line.strip()
    ]

    is_master = any(
        line.startswith("#EXT-X-STREAM-INF:")
        for line in lines
    )

    result = {
        "is_master": is_master,
        "variants": [],
        "segments": 0,
        "duration": 0.0,
        "endlist": "#EXT-X-ENDLIST" in lines,
    }

    if is_master:
        log("[Плейлист] Тип: мастер-плейлист")

        for index, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue

            attributes = parse_attributes(line.split(":", 1)[1])

            variant_path = None
            for next_line in lines[index + 1:]:
                if not next_line.startswith("#"):
                    variant_path = next_line
                    break

            if not variant_path:
                log("[!] Не удалось найти URL варианта")
                continue

            variant_url = urljoin(base_url, variant_path)
            variant = {
                "url": variant_url,
                "bandwidth": attributes.get("BANDWIDTH"),
                "resolution": attributes.get("RESOLUTION", "не указано"),
                "codecs": attributes.get("CODECS", "не указаны"),
                "fps": attributes.get("FRAME-RATE", "не указан"),
            }
            result["variants"].append(variant)

            log(f"  URL: {variant_url}")
            log(f"  Разрешение: {variant['resolution']}")
            log(f"  Битрейт: {format_bitrate(variant['bandwidth'])}")
            log(f"  Кодеки: {variant['codecs']}")
            log(f"  FPS: {variant['fps']}")

        stats["master_playlists"] += 1

    else:
        log("[Плейлист] Тип: медиаплейлист")

        pending_duration = None

        for line in lines:
            if line.startswith("#EXTINF:"):
                duration_text = line.split(":", 1)[1].split(",", 1)[0]
                try:
                    pending_duration = float(duration_text)
                except ValueError:
                    pending_duration = 0.0

            elif not line.startswith("#"):
                result["segments"] += 1

                if pending_duration is not None:
                    result["duration"] += pending_duration
                    pending_duration = None

        stats["media_playlists"] += 1

        log(f"  Сегментов: {result['segments']}")
        log(f"  Суммарная длительность: {format_duration(result['duration'])}")
        log(
            "  ENDLIST: "
            + ("присутствует" if result["endlist"] else "отсутствует")
        )

    return result


def analyze_ffprobe(url):
    executable = shutil.which("ffprobe")

    if not executable:
        log("[!] ffprobe не найден; пропускаем анализ кодеков.")
        return False

    command = [
        executable,
        "-hide_banner",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-rw_timeout", "8000000",
        "-user_agent", HEADERS["User-Agent"],
        url,
    ]

    log("[ffprobe] Анализ потока...")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
            check=False,
        )

        if result.returncode != 0:
            log(f"[X] ffprobe завершился с кодом {result.returncode}")
            if result.stderr.strip():
                log(f"    {shorten(result.stderr.strip())}")
            stats["ffprobe_failed"] += 1
            return False

        data = json.loads(result.stdout)
        fmt = data.get("format", {})

        log(f"  Формат: {fmt.get('format_name', 'не указан')}")
        log(f"  Длительность: {format_duration(fmt.get('duration'))}")
        log(f"  Битрейт: {format_bitrate(fmt.get('bit_rate'))}")

        tags = fmt.get("tags", {})
        if tags:
            log("  Метаданные:")
            for key, value in tags.items():
                log(f"    {key}: {shorten(value, 200)}")
        else:
            log("  Метаданные формата отсутствуют.")

        streams = data.get("streams", [])
        if not streams:
            log("  [!] Аудио- и видеопотоки не обнаружены.")

        for stream in streams:
            kind = stream.get("codec_type", "unknown")
            codec = stream.get("codec_name", "не указан")

            log(f"  Поток #{stream.get('index', '?')}: {kind}")

            if kind == "video":
                log(f"    Кодек: {codec}")
                log(
                    f"    Разрешение: {stream.get('width', '?')}x"
                    f"{stream.get('height', '?')}"
                )
                log(
                    f"    FPS: {stream.get('avg_frame_rate') or stream.get('r_frame_rate', '?')}"
                )
                log(f"    Pixel format: {stream.get('pix_fmt', 'не указан')}")

            elif kind == "audio":
                log(f"    Кодек: {codec}")
                log(f"    Каналы: {stream.get('channels', '?')}")
                log(f"    Частота: {stream.get('sample_rate', '?')} Hz")

            for key, value in stream.get("tags", {}).items():
                log(f"    Тег {key}: {shorten(value, 200)}")

        stats["ffprobe_ok"] += 1
        return True

    except subprocess.TimeoutExpired:
        log(f"[X] ffprobe: превышено время ожидания {FFPROBE_TIMEOUT} с.")
    except json.JSONDecodeError as exc:
        log(f"[X] Ошибка разбора JSON ffprobe: {exc}")
    except OSError as exc:
        log(f"[X] Не удалось запустить ffprobe: {exc}")
    except Exception as exc:
        log(f"[X] Ошибка ffprobe: {type(exc).__name__}: {exc}")

    stats["ffprobe_failed"] += 1
    return False


def check_node(session, url):
    section(f"ПРОВЕРКА: {url}")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        log("[X] Некорректный URL")
        stats["failed"] += 1
        return False

    started = time.monotonic()

    try:
        with session.get(
            url,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            stream=True,
        ) as response:

            log(f"HTTP: {response.status_code}")
            log(f"Время ответа: {time.monotonic() - started:.2f} с.")
            log(f"Конечный URL: {response.url}")
            log(f"Content-Type: {response.headers.get('Content-Type', 'не указан')}")

            if response.status_code != 200:
                log("[X] Сервер вернул HTTP-ошибку.")
                stats["failed"] += 1
                return False

            stats["http_ok"] += 1

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_PLAYLIST_BYTES:
                        log("[X] Плейлист превышает лимит размера.")
                        stats["failed"] += 1
                        return False
                except ValueError:
                    pass

            chunks = []
            size = 0

            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue

                size += len(chunk)
                if size > MAX_PLAYLIST_BYTES:
                    log("[X] Ответ превышает лимит размера.")
                    stats["failed"] += 1
                    return False

                chunks.append(chunk)

            raw = b"".join(chunks)
            content = raw.decode(
                response.encoding or "utf-8",
                errors="replace",
            ).strip()

            if not is_valid_playlist(content):
                log("[X] Ответ не является валидным M3U8: отсутствует #EXTM3U.")
                log(f"Начало ответа: {shorten(content[:250])}")
                stats["failed"] += 1
                return False

            stats["valid_playlists"] += 1
            log("[OK] Получен валидный HLS-плейлист.")

            analysis = analyze_playlist(content, response.url)

    except requests.exceptions.Timeout:
        log("[X] Тайм-аут HTTP-запроса.")
        stats["failed"] += 1
        return False
    except requests.exceptions.SSLError as exc:
        log(f"[X] Ошибка TLS/SSL: {shorten(exc)}")
        stats["failed"] += 1
        return False
    except requests.exceptions.ConnectionError as exc:
        log(f"[X] Ошибка подключения: {shorten(exc)}")
        stats["failed"] += 1
        return False
    except requests.exceptions.RequestException as exc:
        log(f"[X] HTTP-ошибка: {shorten(exc)}")
        stats["failed"] += 1
        return False
    except Exception as exc:
        log(f"[X] Ошибка: {type(exc).__name__}: {exc}")
        stats["failed"] += 1
        return False

    if analysis["is_master"]:
        if not analysis["variants"]:
            log("[!] Варианты качества не найдены.")

        for variant in analysis["variants"][:MAX_VARIANTS_TO_PROBE]:
            log()
            log(f"[Вариант] {variant['url']}")
            analyze_ffprobe(variant["url"])

        skipped = len(analysis["variants"]) - MAX_VARIANTS_TO_PROBE
        if skipped > 0:
            log(f"Остальные варианты пропущены: {skipped}")
    else:
        analyze_ffprobe(url)

    return True


def main():
    section("КОМПЛЕКСНАЯ ДИАГНОСТИКА HLS / IPTV")
    log(f"Время UTC: {datetime.now(timezone.utc).isoformat()}")
    log(f"Python: {sys.version.split()[0]}")
    log(f"ffprobe: {shutil.which('ffprobe') or 'не найден'}")

    stats["total"] = len(URLS)

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for index, url in enumerate(URLS, 1):
            log()
            log(f"Узел {index}/{len(URLS)}")
            check_node(session, url)

    section("ИТОГОВАЯ СТАТИСТИКА")
    log(f"Всего узлов: {stats['total']}")
    log(f"HTTP 200: {stats['http_ok']}")
    log(f"Валидных плейлистов: {stats['valid_playlists']}")
    log(f"Мастер-плейлистов: {stats['master_playlists']}")
    log(f"Медиа-плейлистов: {stats['media_playlists']}")
    log(f"Ошибок узлов: {stats['failed']}")
    log(f"Успешных анализов ffprobe: {stats['ffprobe_ok']}")
    log(f"Ошибок ffprobe: {stats['ffprobe_failed']}")
    log(f"Завершено UTC: {datetime.now(timezone.utc).isoformat()}")

    if stats["valid_playlists"] == 0:
        log("[X] Ни одного валидного HLS-плейлиста.")
        return 1

    log("[OK] Проверка завершена.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[!] Выполнение прервано.")
        sys.exit(130)
    except Exception as exc:
        log(f"[FATAL] {type(exc).__name__}: {exc}")
        sys.exit(1)
