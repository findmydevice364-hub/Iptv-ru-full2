#!/usr/bin/env python3
"""
Полная пробивка всего плейлиста через ffprobe.

- Берёт unique URL из all_clean.m3u
- ffprobe с таймаутом
- 200 + данные → сохраняем полную инфу
- 403 / timeout / error → пропускаем
- В конце: отчёт + real_multiaudio.m3u (≥2 audio)
- Прогресс сохраняется → можно продолжать
"""

import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict

# ========== НАСТРОЙКИ ==========
INPUT_PLAYLIST = Path("output/all_clean.m3u")
OUTPUT_DIR = Path("output")

PROGRESS_FILE = OUTPUT_DIR / "probe_full_progress.json"
ALL_RESULTS_JSON = OUTPUT_DIR / "probe_all_ok.json"          # все, кто ответил 200
MULTI_RESULTS_JSON = OUTPUT_DIR / "probe_multi.json"         # только ≥2 audio
REPORT_TXT = OUTPUT_DIR / "probe_full_report.txt"
MULTI_M3U = OUTPUT_DIR / "real_multiaudio.m3u"

MAX_WORKERS = 16
TIMEOUT_SEC = 9
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SAVE_EVERY = 40
# ==============================


def parse_m3u_unique(path: Path) -> list[dict]:
    """Возвращает список уникальных каналов по URL (первый встретившийся)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    seen = set()
    channels = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            url = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if url.startswith("http") and url not in seen:
                seen.add(url)
                name = line.split(",")[-1].strip() if "," in line else line
                channels.append({
                    "extinf": line,
                    "url": url,
                    "name": name,
                })
            i += 2
        else:
            i += 1
    return channels


def run_ffprobe(url: str) -> dict:
    """
    Возвращает:
    {
      "ok": bool,          # True только если реально получили стримы
      "error": str|None,
      "http_hint": str,    # "403", "timeout", ...
      "audio": [...],
      "video": [...],
      "elapsed": float
    }
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-user_agent", USER_AGENT,
        "-analyzeduration", "2500000",
        "-probesize", "1500000",
        "-show_entries", "stream=index,codec_type,codec_name,channels,width,height:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
        elapsed = time.monotonic() - start
        stderr = (proc.stderr or "").strip()

        # Быстрые признаки ошибок
        if "403" in stderr or "Forbidden" in stderr:
            return {"ok": False, "error": "403", "http_hint": "403", "audio": [], "video": [], "elapsed": elapsed}
        if "404" in stderr or "Not Found" in stderr:
            return {"ok": False, "error": "404", "http_hint": "404", "audio": [], "video": [], "elapsed": elapsed}
        if "401" in stderr:
            return {"ok": False, "error": "401", "http_hint": "401", "audio": [], "video": [], "elapsed": elapsed}
        if proc.returncode != 0:
            err = stderr[:180] if stderr else "ffprobe_fail"
            return {"ok": False, "error": err, "http_hint": "error", "audio": [], "video": [], "elapsed": elapsed}

        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {"ok": False, "error": "no_streams", "http_hint": "empty", "audio": [], "video": [], "elapsed": elapsed}

        audio, video = [], []
        for s in streams:
            entry = {
                "index": s.get("index"),
                "codec": s.get("codec_name"),
            }
            if s.get("codec_type") == "audio":
                entry["channels"] = s.get("channels")
                tags = s.get("tags") or {}
                if tags.get("language"):
                    entry["lang"] = tags["language"]
                if tags.get("title"):
                    entry["title"] = tags["title"]
                audio.append(entry)
            elif s.get("codec_type") == "video":
                entry["width"] = s.get("width")
                entry["height"] = s.get("height")
                video.append(entry)

        return {
            "ok": True,
            "error": None,
            "http_hint": "200",
            "audio": audio,
            "video": video,
            "elapsed": elapsed,
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "http_hint": "timeout", "audio": [], "video": [], "elapsed": TIMEOUT_SEC}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180], "http_hint": "exception", "audio": [], "video": [], "elapsed": time.monotonic() - start}


def format_audio(audio_list: list) -> str:
    if not audio_list:
        return "—"
    parts = []
    for a in audio_list:
        lang = a.get("lang") or a.get("title") or "?"
        ch = a.get("channels") or "?"
        codec = a.get("codec") or "?"
        parts.append(f"{lang}({codec}/{ch}ch)")
    return " | ".join(parts)


def format_video(video_list: list) -> str:
    if not video_list:
        return "—"
    v = video_list[0]
    w = v.get("width") or "?"
    h = v.get("height") or "?"
    codec = v.get("codec") or "?"
    return f"{w}x{h} {codec}"


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "done": {},          # url → full record
        "stats": {"ok": 0, "fail": 0, "multi": 0, "by_hint": {}},
    }


def save_progress(progress: dict):
    PROGRESS_FILE.write_text(
        json.dumps(progress, ensure_ascii=False),
        encoding="utf-8"
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_PLAYLIST.exists():
        print(f"Нет {INPUT_PLAYLIST}. Сначала запусти iptv_cleaner.py")
        return 1

    print(f"Читаю: {INPUT_PLAYLIST}")
    channels = parse_m3u_unique(INPUT_PLAYLIST)
    print(f"Уникальных URL: {len(channels)}")

    progress = load_progress()
    done = progress["done"]
    stats = progress["stats"]
    if "by_hint" not in stats:
        stats["by_hint"] = {}

    todo = [ch for ch in channels if ch["url"] not in done]
    print(f"Уже сделано : {len(done)}")
    print(f"Осталось    : {len(todo)}")
    print(f"Воркеров    : {MAX_WORKERS}, таймаут {TIMEOUT_SEC}с")
    print("-" * 70)

    if todo:
        start_all = time.monotonic()
        completed = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(run_ffprobe, ch["url"]): ch for ch in todo}

            for fut in as_completed(futures):
                ch = futures[fut]
                result = fut.result()
                completed += 1

                record = {
                    "name": ch["name"],
                    "extinf": ch["extinf"],
                    "url": ch["url"],
                    "domain": urlparse(ch["url"]).netloc,
                    "result": result,
                }
                done[ch["url"]] = record

                hint = result.get("http_hint", "error")
                stats["by_hint"][hint] = stats["by_hint"].get(hint, 0) + 1

                if result["ok"]:
                    stats["ok"] += 1
                    n_audio = len(result["audio"])
                    if n_audio >= 2:
                        stats["multi"] += 1
                        mark = f"★ MULTI({n_audio})"
                    else:
                        mark = "OK"
                    extra = f"{format_video(result['video'])} | {format_audio(result['audio'])}"
                else:
                    stats["fail"] += 1
                    mark = f"SKIP({hint})"
                    extra = result.get("error", "")[:50]

                name_short = ch["name"][:42]
                print(f"[{completed:4}/{len(todo)}] {mark:12} | {name_short:42} | {extra}")

                if completed % SAVE_EVERY == 0:
                    progress["done"] = done
                    progress["stats"] = stats
                    save_progress(progress)
                    print(f"   💾 сохранено  ok={stats['ok']}  multi={stats['multi']}  fail={stats['fail']}")

        progress["done"] = done
        progress["stats"] = stats
        save_progress(progress)
        print("-" * 70)
        print(f"Пробивка закончена за {(time.monotonic()-start_all)/60:.1f} мин")

    # ===== Сбор результатов =====
    ok_list = []
    multi_list = []

    for url, rec in done.items():
        res = rec["result"]
        if not res["ok"]:
            continue
        item = {
            "name": rec["name"],
            "extinf": rec["extinf"],
            "url": url,
            "domain": rec["domain"],
            "audio": res["audio"],
            "video": res["video"],
            "n_audio": len(res["audio"]),
        }
        ok_list.append(item)
        if item["n_audio"] >= 2:
            multi_list.append(item)

    ALL_RESULTS_JSON.write_text(json.dumps(ok_list, ensure_ascii=False, indent=2), encoding="utf-8")
    MULTI_RESULTS_JSON.write_text(json.dumps(multi_list, ensure_ascii=False, indent=2), encoding="utf-8")

    # Отчёт
    lines = []
    lines.append("=" * 90)
    lines.append("  ПОЛНЫЙ ОТЧЁТ ffprobe ПО ВСЕМУ ПЛЕЙЛИСТУ")
    lines.append("=" * 90)
    lines.append(f"Уникальных URL всего     : {len(channels)}")
    lines.append(f"Успешно ответили (200)   : {stats['ok']}")
    lines.append(f"Пропущено (403/timeout/…) : {stats['fail']}")
    lines.append(f"Реальных мульти (≥2 audio): {len(multi_list)}")
    lines.append("")
    lines.append("По причинам пропуска:")
    for h, c in sorted(stats.get("by_hint", {}).items(), key=lambda x: -x[1]):
        if h != "200":
            lines.append(f"  {h:12} : {c}")
    lines.append("")

    by_dom = defaultdict(list)
    for ch in multi_list:
        by_dom[ch["domain"]].append(ch)

    lines.append("=" * 90)
    lines.append("  РЕАЛЬНЫЕ МУЛЬТИАУДИО-КАНАЛЫ (≥ 2 audio streams)")
    lines.append("=" * 90)

    for domain, chs in sorted(by_dom.items(), key=lambda x: -len(x[1])):
        lines.append("")
        lines.append(f"▶ {domain}  ({len(chs)})")
        lines.append("-" * 70)
        for i, ch in enumerate(chs, 1):
            lines.append(f"  [{i}] {ch['name']}")
            lines.append(f"      Видео : {format_video(ch['video'])}")
            lines.append(f"      Аудио : {format_audio(ch['audio'])}")
            lines.append(f"      URL   : {ch['url']}")

    lines.append("")
    lines.append("=" * 90)
    REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")

    # Плейлист только мульти
    m3u = ["#EXTM3U x-title=\"Real multi-audio (ffprobe verified)\""]
    for ch in multi_list:
        audio_info = format_audio(ch["audio"])
        vid = format_video(ch["video"])
        new_name = f"{ch['name']} [{vid}] [{audio_info}]"
        extinf = ch["extinf"]
        if "," in extinf:
            prefix = extinf.rsplit(",", 1)[0]
            extinf = f"{prefix},{new_name}"
        m3u.append(extinf)
        m3u.append(ch["url"])
    MULTI_M3U.write_text("\n".join(m3u) + "\n", encoding="utf-8")

    print()
    print("=" * 60)
    print("ИТОГО:")
    print(f"  Успешно (200)        : {stats['ok']}")
    print(f"  Пропущено            : {stats['fail']}")
    print(f"  Реальных мульти (≥2) : {len(multi_list)}")
    print("=" * 60)
    print("Файлы:")
    print(f"  {ALL_RESULTS_JSON}")
    print(f"  {MULTI_RESULTS_JSON}")
    print(f"  {REPORT_TXT}")
    print(f"  {MULTI_M3U}")
    print(f"  {PROGRESS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())