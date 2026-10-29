#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Полное техническое сканирование IPTV-плейлиста.

Вход:
    output/all_clean.m3u

Выход:
    output/probe_multi.json
    output/probe_multi.txt
    output/real_multiaudio.m3u
    output/probe_all_ok.json
    output/probe_full_report.txt
    output/probe_full_progress.json

Требования:
    Python 3.10+
    ffprobe (поставляется вместе с FFmpeg)

Успешный ffprobe означает, что поток удалось разобрать.
Это само по себе не гарантирует HTTP-статус 200.
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


# ============================================================
# НАСТРОЙКИ
# ============================================================

INPUT_M3U = Path("output/all_clean.m3u")
OUTPUT_DIR = Path("output")

MULTI_JSON = OUTPUT_DIR / "probe_multi.json"
MULTI_TXT = OUTPUT_DIR / "probe_multi.txt"
MULTI_M3U = OUTPUT_DIR / "real_multiaudio.m3u"

ALL_OK_JSON = OUTPUT_DIR / "probe_all_ok.json"
FULL_REPORT_TXT = OUTPUT_DIR / "probe_full_report.txt"
PROGRESS_JSON = OUTPUT_DIR / "probe_full_progress.json"

MAX_WORKERS = 8
TIMEOUT_SEC = 30
SAVE_EVERY = 20

ANALYZE_DURATION = "10000000"
PROBE_SIZE = "10000000"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
)

FFPROBE_BINARY = os.environ.get("FFPROBE", "ffprobe")


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_fraction(value):
    """Преобразует дробь вида 25/1 в число FPS."""
    if not value or value == "0/0":
        return None

    try:
        numerator, denominator = value.split("/", 1)
        denominator = float(denominator)

        if denominator == 0:
            return None

        return round(float(numerator) / denominator, 3)
    except (ValueError, ZeroDivisionError):
        return None


def atomic_write_text(path, content):
    """Атомарная запись текстового файла."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)

        os.replace(temp_name, path)

    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path, data):
    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_text(path, content + "\n")


def normalize_url(url):
    return (url or "").strip()


def get_domain(url):
    try:
        return urlparse(url).netloc or ""
    except Exception:
        return ""


def get_source(url):
    try:
        parsed = urlparse(url)
        return {
            "scheme": parsed.scheme,
            "domain": parsed.netloc,
            "path": parsed.path,
            "query": parsed.query,
        }
    except Exception:
        return {
            "scheme": "",
            "domain": "",
            "path": "",
            "query": "",
        }


def json_load_or_default(path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
    except (OSError, json.JSONDecodeError):
        print(
            f"[WARN] Не удалось прочитать {path}; "
            "используется пустое состояние.",
            flush=True,
        )

    return default


# ============================================================
# ЧТЕНИЕ M3U
# ============================================================

def parse_m3u(path):
    """
    Читает M3U и связывает EXTINF с URL.
    Допускает промежуточные #EXTVLCOPT, #KODIPROP и другие теги.
    """

    if not path.exists():
        raise FileNotFoundError(
            f"Входной плейлист не найден: {path}"
        )

    entries = []
    current = None

    with path.open("r", encoding="utf-8-sig", errors="replace") as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("#EXTINF:"):
                current = {
                    "extinf": line,
                    "name": extract_name(line),
                    "group": extract_attribute(line, "group-title"),
                    "tvg_id": extract_attribute(line, "tvg-id"),
                    "tvg_name": extract_attribute(line, "tvg-name"),
                    "tvg_logo": extract_attribute(line, "tvg-logo"),
                    "tags": [],
                    "url": None,
                }
                continue

            if line.startswith("#"):
                if current is not None:
                    current["tags"].append(line)
                continue

            if line.startswith(("http://", "https://")):
                if current is None:
                    current = {
                        "extinf": "",
                        "name": line,
                        "group": "",
                        "tvg_id": "",
                        "tvg_name": "",
                        "tvg_logo": "",
                        "tags": [],
                        "url": None,
                    }

                current["url"] = normalize_url(line)
                entries.append(current)
                current = None

    # Оставляем только записи с HTTP(S)-адресами.
    return [
        entry for entry in entries
        if entry.get("url", "").startswith(("http://", "https://"))
    ]


def extract_attribute(line, attribute):
    pattern = (
        r'(?:^|\s)'
        + re.escape(attribute)
        + r'="([^"]*)"'
    )

    match = re.search(pattern, line)
    return match.group(1) if match else ""


def extract_name(extinf):
    if not extinf:
        return ""

    # Название находится после последней запятой.
    # Обычно это корректно для стандартного формата EXTINF.
    if "," in extinf:
        return extinf.rsplit(",", 1)[1].strip()

    return ""


def unique_entries(entries):
    """
    Сохраняет первое появление каждого URL.
    Если URL повторяется, использует первую запись метаданных.
    """

    result = []
    seen = set()

    for entry in entries:
        url = normalize_url(entry.get("url"))

        if not url or url in seen:
            continue

        seen.add(url)

        item = dict(entry)
        item["url"] = url
        result.append(item)

    return result


# ============================================================
# ЗАПУСК FFPROBE
# ============================================================

def probe_url(entry):
    url = entry["url"]

    command = [
        FFPROBE_BINARY,
        "-v", "error",
        "-hide_banner",
        "-show_error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        "-user_agent", USER_AGENT,
        "-rw_timeout", str(TIMEOUT_SEC * 1_000_000),
        "-analyzeduration", ANALYZE_DURATION,
        "-probesize", PROBE_SIZE,
        url,
    ]

    started = time.time()

    result = {
        "url": url,
        "name": entry.get("name", ""),
        "group": entry.get("group", ""),
        "tvg_id": entry.get("tvg_id", ""),
        "tvg_name": entry.get("tvg_name", ""),
        "tvg_logo": entry.get("tvg_logo", ""),
        "extinf": entry.get("extinf", ""),
        "playlist_tags": entry.get("tags", []),
        "source": get_source(url),
        "checked_at": utc_now(),
        "probe_ok": False,
        "http_hint": None,
        "duration_seconds": None,
        "format": {},
        "streams": [],
        "stream_counts": {},
        "audio_track_count": 0,
        "video_track_count": 0,
        "subtitle_track_count": 0,
        "is_multi_audio": False,
        "probe_error": None,
        "probe_stderr": None,
        "probe_elapsed_seconds": None,
    }

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            check=False,
        )

        result["probe_elapsed_seconds"] = round(
            time.time() - started, 3
        )

        if process.returncode != 0:
            result["probe_error"] = (
                f"ffprobe завершился с кодом {process.returncode}"
            )

            if process.stderr:
                result["probe_stderr"] = process.stderr[-5000:]

            return result

        try:
            data = json.loads(process.stdout or "{}")
        except json.JSONDecodeError as exc:
            result["probe_error"] = (
                f"Не удалось разобрать JSON ffprobe: {exc}"
            )
            result["probe_stderr"] = (process.stderr or "")[-5000:]
            return result

        format_info = data.get("format") or {}
        streams = data.get("streams") or []

        result["format"] = format_info
        result["duration_seconds"] = safe_float(
            format_info.get("duration")
        )

        enriched_streams = []

        counts = Counter()

        for stream in streams:
            item = dict(stream)

            codec_type = item.get("codec_type", "unknown")
            counts[codec_type] += 1

            tags = item.get("tags") or {}
            dispositions = item.get("disposition") or {}

            item["language"] = (
                tags.get("language")
                or tags.get("LANGUAGE")
                or ""
            )

            item["title"] = (
                tags.get("title")
                or tags.get("handler_name")
                or ""
            )

            item["is_default"] = bool(
                safe_int(dispositions.get("default"))
            )

            item["is_forced"] = bool(
                safe_int(dispositions.get("forced"))
            )

            item["fps_avg"] = parse_fraction(
                item.get("avg_frame_rate")
            )

            item["fps_r"] = parse_fraction(
                item.get("r_frame_rate")
            )

            # Основные технические поля сохраняются вместе
            # со всеми исходными полями ffprobe.
            enriched_streams.append(item)

        result["streams"] = enriched_streams
        result["stream_counts"] = dict(counts)
        result["audio_track_count"] = counts.get("audio", 0)
        result["video_track_count"] = counts.get("video", 0)
        result["subtitle_track_count"] = counts.get("subtitle", 0)

        result["is_multi_audio"] = (
            result["audio_track_count"] >= 2
        )

        result["probe_ok"] = True

        # Успешный разбор не является доказательством HTTP 200.
        result["http_hint"] = "OK"

        return result

    except subprocess.TimeoutExpired:
        result["probe_elapsed_seconds"] = round(
            time.time() - started, 3
        )
        result["probe_error"] = (
            f"Тайм-аут ffprobe: более {TIMEOUT_SEC} секунд"
        )
        return result

    except FileNotFoundError:
        result["probe_error"] = (
            f"Не найден исполняемый файл {FFPROBE_BINARY}. "
            "Установите FFmpeg/ffprobe."
        )
        return result

    except Exception as exc:
        result["probe_elapsed_seconds"] = round(
            time.time() - started, 3
        )
        result["probe_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        return result


# ============================================================
# ЧЕЛОВЕКОЧИТАЕМЫЙ ТЕХНИЧЕСКИЙ ОТЧЁТ
# ============================================================

def format_bitrate(value):
    number = safe_int(value, -1)

    if number < 0:
        return "неизвестно"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f} Мбит/с"

    if number >= 1_000:
        return f"{number / 1_000:.1f} кбит/с"

    return f"{number} бит/с"


def format_size(value):
    number = safe_int(value, -1)

    if number < 0:
        return "неизвестно"

    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(number)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.2f} {units[unit_index]}"


def value_or_unknown(value):
    if value is None or value == "":
        return "неизвестно"

    return str(value)


def describe_stream(stream):
    codec_type = stream.get("codec_type", "unknown")
    codec_name = value_or_unknown(stream.get("codec_name"))
    codec_long = value_or_unknown(stream.get("codec_long_name"))

    lines = [
        (
            f"    Поток #{stream.get('index', '?')} "
            f"[{codec_type}]"
        ),
        f"      Кодек: {codec_name} ({codec_long})",
    ]

    if codec_type == "video":
        width = stream.get("width")
        height = stream.get("height")

        resolution = (
            f"{width}x{height}"
            if width and height
            else "неизвестно"
        )

        lines.append(f"      Разрешение: {resolution}")
        lines.append(
            f"      FPS avg: {value_or_unknown(stream.get('fps_avg'))}"
        )
        lines.append(
            f"      FPS r: {value_or_unknown(stream.get('fps_r'))}"
        )
        lines.append(
            f"      Пиксельный формат: "
            f"{value_or_unknown(stream.get('pix_fmt'))}"
        )
        lines.append(
            f"      Профиль: {value_or_unknown(stream.get('profile'))}"
        )
        lines.append(
            f"      Уровень: {value_or_unknown(stream.get('level'))}"
        )
        lines.append(
            f"      Соотношение сторон: "
            f"{value_or_unknown(stream.get('display_aspect_ratio'))}"
        )
        lines.append(
            f"      Битрейт: {format_bitrate(stream.get('bit_rate'))}"
        )

    elif codec_type == "audio":
        lines.append(
            f"      Язык: {value_or_unknown(stream.get('language'))}"
        )
        lines.append(
            f"      Название: {value_or_unknown(stream.get('title'))}"
        )
        lines.append(
            f"      Частота дискретизации: "
            f"{value_or_unknown(stream.get('sample_rate'))} Гц"
        )
        lines.append(
            f"      Каналы: {value_or_unknown(stream.get('channels'))}"
        )
        lines.append(
            f"      Раскладка каналов: "
            f"{value_or_unknown(stream.get('channel_layout'))}"
        )
        lines.append(
            f"      Битрейт: {format_bitrate(stream.get('bit_rate'))}"
        )
        lines.append(
            f"      Профиль: {value_or_unknown(stream.get('profile'))}"
        )

    elif codec_type == "subtitle":
        lines.append(
            f"      Язык: {value_or_unknown(stream.get('language'))}"
        )
        lines.append(
            f"      Название: {value_or_unknown(stream.get('title'))}"
        )

    lines.append(
        f"      По умолчанию: "
        f"{'да' if stream.get('is_default') else 'нет'}"
    )
    lines.append(
        f"      Принудительный: "
        f"{'да' if stream.get('is_forced') else 'нет'}"
    )

    tags = stream.get("tags") or {}

    if tags:
        lines.append("      Теги:")
        for key, value in sorted(tags.items()):
            lines.append(f"        {key}: {value}")

    return lines


def describe_channel(result):
    lines = [
        "=" * 100,
        f"КАНАЛ: {value_or_unknown(result.get('name'))}",
        "=" * 100,
        f"URL: {result.get('url', '')}",
        f"Домен: {result.get('source', {}).get('domain', '')}",
        f"Путь: {result.get('source', {}).get('path', '')}",
        f"Группа: {value_or_unknown(result.get('group'))}",
        f"tvg-id: {value_or_unknown(result.get('tvg_id'))}",
        f"tvg-name: {value_or_unknown(result.get('tvg_name'))}",
        f"tvg-logo: {value_or_unknown(result.get('tvg_logo'))}",
        f"Проверен: {result.get('checked_at', '')}",
        (
            "Время анализа: "
            f"{value_or_unknown(result.get('probe_elapsed_seconds'))} сек."
        ),
        (
            "Длительность: "
            f"{value_or_unknown(result.get('duration_seconds'))} сек."
        ),
        (
            "Формат: "
            f"{value_or_unknown(result.get('format', {}).get('format_name'))}"
        ),
        (
            "Общий битрейт: "
            f"{format_bitrate(result.get('format', {}).get('bit_rate'))}"
        ),
        (
            "Количество потоков: "
            f"{len(result.get('streams', []))}"
        ),
        f"Видеодорожек: {result.get('video_track_count', 0)}",
        f"Аудиодорожек: {result.get('audio_track_count', 0)}",
        f"Субтитров: {result.get('subtitle_track_count', 0)}",
        "",
        "ТЕХНИЧЕСКИЕ ПОТОКИ:",
    ]

    for stream in result.get("streams", []):
        lines.extend(describe_stream(stream))
        lines.append("")

    format_info = result.get("format") or {}
    format_tags = format_info.get("tags") or {}

    if format_tags:
        lines.append("ТЕГИ ФОРМАТА:")

        for key, value in sorted(format_tags.items()):
            lines.append(f"  {key}: {value}")

    playlist_tags = result.get("playlist_tags") or []

    if playlist_tags:
        lines.append("")
        lines.append("ТЕГИ ИЗ M3U:")

        for tag in playlist_tags:
            lines.append(f"  {tag}")

    lines.append("")
    return lines


# ============================================================
# ФОРМИРОВАНИЕ M3U
# ============================================================

def m3u_escape(value):
    return str(value or "").replace("\r", " ").replace("\n", " ")


def make_multi_m3u(results):
    lines = ["#EXTM3U"]

    for result in results:
        name = m3u_escape(result.get("name") or result.get("url"))
        group = m3u_escape(result.get("group"))
        tvg_id = m3u_escape(result.get("tvg_id"))
        tvg_name = m3u_escape(result.get("tvg_name"))
        tvg_logo = m3u_escape(result.get("tvg_logo"))

        audio_count = result.get("audio_track_count", 0)
        video_count = result.get("video_track_count", 0)

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{tvg_id}" '
            f'tvg-name="{tvg_name}" '
            f'tvg-logo="{tvg_logo}" '
            f'group-title="{group}",'
            f'{name} [AUDIO:{audio_count} VIDEO:{video_count}]'
        )

        # Сохраняем дополнительные M3U-теги, если они есть.
        for tag in result.get("playlist_tags", []):
            if tag.startswith(
                (
                    "#EXTVLCOPT:",
                    "#KODIPROP:",
                    "#EXTGRP:",
                )
            ):
                lines.append(tag)

        lines.append(result["url"])

    return "\n".join(lines) + "\n"


# ============================================================
# ОТЧЁТ ПО ВСЕМ РЕЗУЛЬТАТАМ
# ============================================================

def build_full_report(results, failures, total_urls, elapsed):
    successful = len(results)
    multi = [item for item in results if item.get("is_multi_audio")]

    domains = Counter(
        item.get("source", {}).get("domain", "")
        for item in results
        if item.get("source", {}).get("domain", "")
    )

    lines = [
        "ПОЛНЫЙ ОТЧЁТ СКАНИРОВАНИЯ IPTV",
        "=" * 80,
        f"Дата создания: {utc_now()}",
        f"Входной файл: {INPUT_M3U}",
        f"Всего уникальных URL: {total_urls}",
        f"Успешно обработано: {successful}",
        f"Ошибок: {len(failures)}",
        f"Каналов с двумя и более аудиодорожками: {len(multi)}",
        f"Общее время выполнения: {elapsed:.1f} сек.",
        "",
        "ДОМЕНЫ УСПЕШНО ОБРАБОТАННЫХ ПОТОКОВ:",
    ]

    for domain, count in domains.most_common():
        lines.append(f"  {domain}: {count}")

    lines.extend([
        "",
        "КАНАЛЫ С НЕСКОЛЬКИМИ АУДИОДОРОЖКАМИ:",
        "",
    ])

    for item in multi:
        lines.extend(describe_channel(item))

    lines.extend([
        "",
        "=" * 80,
        "ОШИБКИ ОБРАБОТКИ",
        "=" * 80,
        "",
    ])

    for item in failures:
        lines.append(f"Канал: {item.get('name', '')}")
        lines.append(f"URL: {item.get('url', '')}")
        lines.append(f"Ошибка: {item.get('probe_error', 'неизвестно')}")

        if item.get("probe_stderr"):
            lines.append(f"stderr: {item['probe_stderr']}")

        lines.append("")

    lines.extend([
        "",
        "ПРИМЕЧАНИЕ:",
        "Успешный разбор ffprobe не гарантирует HTTP-статус 200.",
        "Некоторые потоки могут требовать заголовки, cookies, токены",
        "или определённые параметры доступа.",
        "",
    ])

    return "\n".join(lines)


# ============================================================
# СОХРАНЕНИЕ РЕЗУЛЬТАТОВ И ПРОГРЕССА
# ============================================================

def save_progress(completed):
    atomic_write_json(
        PROGRESS_JSON,
        {
            "updated_at": utc_now(),
            "completed": completed,
        },
    )


def save_outputs(results, failures, total_urls, elapsed):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    successful = sorted(
        results,
        key=lambda item: (
            item.get("name", "").casefold(),
            item.get("url", ""),
        ),
    )

    multi = [
        item for item in successful
        if item.get("audio_track_count", 0) >= 2
    ]

    atomic_write_json(ALL_OK_JSON, {
        "generated_at": utc_now(),
        "input": str(INPUT_M3U),
        "total_unique_urls": total_urls,
        "successful_count": len(successful),
        "channels": successful,
    })

    atomic_write_json(MULTI_JSON, {
        "generated_at": utc_now(),
        "input": str(INPUT_M3U),
        "multi_audio_count": len(multi),
        "channels": multi,
    })

    multi_lines = [
        "ТЕХНИЧЕСКИЙ ОТЧЁТ: КАНАЛЫ С НЕСКОЛЬКИМИ АУДИОДОРОЖКАМИ",
        f"Дата создания: {utc_now()}",
        f"Количество каналов: {len(multi)}",
        "",
    ]

    for item in multi:
        multi_lines.extend(describe_channel(item))

    atomic_write_text(
        MULTI_TXT,
        "\n".join(multi_lines),
    )

    atomic_write_text(
        MULTI_M3U,
        make_multi_m3u(multi),
    )

    atomic_write_text(
        FULL_REPORT_TXT,
        build_full_report(
            successful,
            failures,
            total_urls,
            elapsed,
        ),
    )

    print(
        "[SAVE] "
        f"успешно={len(successful)}, "
        f"многодорожечных={len(multi)}, "
        f"ошибок={len(failures)}",
        flush=True,
    )


# ============================================================
# ОСНОВНАЯ ПРОГРАММА
# ============================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if shutil.which(FFPROBE_BINARY) is None:
        print(
            f"[FATAL] Не найден ffprobe: {FFPROBE_BINARY}",
            file=sys.stderr,
        )
        print(
            "Установите FFmpeg и проверьте команду ffprobe -version.",
            file=sys.stderr,
        )
        return 2

    if not INPUT_M3U.exists():
        print(
            f"[FATAL] Не найден входной плейлист: {INPUT_M3U}",
            file=sys.stderr,
        )
        return 2

    print(f"[INFO] Читаю {INPUT_M3U}", flush=True)

    entries = unique_entries(parse_m3u(INPUT_M3U))
    total_urls = len(entries)

    if not entries:
        print("[FATAL] В плейлисте не найдено HTTP(S)-потоков.", flush=True)
        return 2

    print(f"[INFO] Уникальных URL: {total_urls}", flush=True)
    print(f"[INFO] Параллельных задач: {MAX_WORKERS}", flush=True)

    progress = json_load_or_default(
        PROGRESS_JSON,
        {"completed": {}},
    )

    completed = progress.get("completed", {})

    if not isinstance(completed, dict):
        completed = {}

    # Повторно используем результаты по URL.
    # Все текущие URL будут обработаны или восстановлены из прогресса.
    pending = []

    for entry in entries:
        old = completed.get(entry["url"])

        if isinstance(old, dict) and old.get("probe_ok"):
            continue

        # Неудачные результаты тоже перепроверяются при новом запуске.
        pending.append(entry)

    print(
        f"[INFO] Уже успешно проверено: "
        f"{total_urls - len(pending)}",
        flush=True,
    )
    print(f"[INFO] Осталось проверить: {len(pending)}", flush=True)

    start_time = time.time()
    newly_completed = 0

    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            future_to_entry = {
                executor.submit(probe_url, entry): entry
                for entry in pending
            }

            for future in concurrent.futures.as_completed(future_to_entry):
                entry = future_to_entry[future]
                url = entry["url"]

                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "url": url,
                        "name": entry.get("name", ""),
                        "source": get_source(url),
                        "checked_at": utc_now(),
                        "probe_ok": False,
                        "is_multi_audio": False,
                        "audio_track_count": 0,
                        "video_track_count": 0,
                        "subtitle_track_count": 0,
                        "streams": [],
                        "format": {},
                        "probe_error": f"{type(exc).__name__}: {exc}",
                    }

                completed[url] = result
                newly_completed += 1

                if result.get("probe_ok"):
                    status = "MULTI-AUDIO" if result.get(
                        "is_multi_audio"
                    ) else "OK"

                    print(
                        f"[{status}] "
                        f"{result.get('name', '')} | "
                        f"audio={result.get('audio_track_count', 0)} | "
                        f"{url}",
                        flush=True,
                    )
                else:
                    print(
                        f"[ERROR] {result.get('name', '')} | "
                        f"{result.get('probe_error', '')} | {url}",
                        flush=True,
                    )

                if newly_completed % SAVE_EVERY == 0:
                    save_progress(completed)

                    results = [
                        item for item in completed.values()
                        if item.get("probe_ok")
                    ]

                    failures = [
                        item for item in completed.values()
                        if not item.get("probe_ok")
                    ]

                    save_outputs(
                        results,
                        failures,
                        total_urls,
                        time.time() - start_time,
                    )

    except KeyboardInterrupt:
        print(
            "\n[WARN] Прервано пользователем. Сохраняю прогресс...",
            flush=True,
        )

        save_progress(completed)

        results = [
            item for item in completed.values()
            if item.get("probe_ok")
        ]

        failures = [
            item for item in completed.values()
            if not item.get("probe_ok")
        ]

        save_outputs(
            results,
            failures,
            total_urls,
            time.time() - start_time,
        )

        return 130

    # Финальное сохранение.
    save_progress(completed)

    # В отчёты попадают только URL из текущего плейлиста.
    current_urls = {entry["url"] for entry in entries}

    current_results = [
        completed[url]
        for url in current_urls
        if url in completed and completed[url].get("probe_ok")
    ]

    current_failures = [
        completed[url]
        for url in current_urls
        if url in completed and not completed[url].get("probe_ok")
    ]

    elapsed = time.time() - start_time

    save_outputs(
        current_results,
        current_failures,
        total_urls,
        elapsed,
    )

    multi_count = sum(
        1 for item in current_results
        if item.get("audio_track_count", 0) >= 2
    )

    print("\n[DONE] Сканирование завершено.", flush=True)
    print(f"[DONE] Всего URL: {total_urls}", flush=True)
    print(f"[DONE] Успешно: {len(current_results)}", flush=True)
    print(f"[DONE] Ошибки: {len(current_failures)}", flush=True)
    print(f"[DONE] Многодорожечные: {multi_count}", flush=True)
    print(f"[DONE] Отчёты: {OUTPUT_DIR.resolve()}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())