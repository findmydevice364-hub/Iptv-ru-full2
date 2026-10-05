import os
import re
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict

# ============================================================
# CONFIG
# ============================================================

MAX_THREADS = 50
TIMEOUT = 5

START_ID = 0
END_ID = 6000

STREAM8_BASE = "https://stream8.cinerama.uz"
STREAM0_BASE = "https://stream0.cinerama.uz"
STREAM1_BASE = "https://stream1.cinerama.uz"

OUTPUT_FILE = "cinerama_Verified_1.m3u"
REPORT_FILE = "SKALA_DREG_REPORT.txt"

# Сравниваем только с одним "мега" файлом,
# если он существует. Прошлые проходы не искать.
COMPARE_FILE = "cinerama_Verified_1.m3u"

COPYRIGHT = "© Phoenix 89S - Verify 1"

# ============================================================
# HTTP
# ============================================================

def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 CineramaScanner/1.0",
        "Accept": "*/*",
        "Connection": "keep-alive",
    })
    return session


def build_url(base, stream_id):
    return f"{base}/{stream_id}/tracks-v1a1/mono.m3u8"


def build_pool(base, start_id=START_ID, end_id=END_ID):
    pool = []
    for stream_id in range(start_id, end_id + 1):
        pool.append({
            "id": stream_id,
            "url": build_url(base, stream_id),
        })
    return pool


# ============================================================
# EXTINF PARSING
# ============================================================

def clean_name(name):
    if not name:
        return ""
    name = str(name).strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,")


def extract_extinf_line(text):
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            return line
    return None


def extract_extinf_name(text):
    if not text:
        return None

    extinf_line = extract_extinf_line(text)
    if not extinf_line or "," not in extinf_line:
        return None

    name = extinf_line.split(",", 1)[1].strip()
    if not name:
        return None

    return clean_name(name)


def extract_tvg_id(extinf_text):
    if not extinf_text:
        return None

    match = re.search(r'tvg-id=["\']?([^"\'\s,]+)', extinf_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


# ============================================================
# STREAM8: generate full pool -> scan it
# ============================================================

def scan_stream8_url(item):
    session = make_session()
    url = item["url"]
    stream_id = item["id"]

    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return None

        text = response.text
        if not text or "#EXTINF:" not in text:
            return None

        extinf_line = extract_extinf_line(text)
        if not extinf_line:
            return None

        name = extract_extinf_name(text)
        if not name:
            return None

        tvg_id = extract_tvg_id(extinf_line)

        return {
            "id": stream_id,
            "name": name,
            "tvg_id": tvg_id,
            "url": url,
            "group": "STREAM8",
        }

    except Exception:
        return None
    finally:
        session.close()


def scan_stream8_pool():
    pool = build_pool(STREAM8_BASE)
    found = []
    total = len(pool)
    completed = 0

    print()
    print("=" * 70)
    print("ГЕНЕРАЦИЯ ПУЛА И СКАНИРОВАНИЕ STREAM8")
    print(f"Всего URL: {total}")
    print("=" * 70)
    print()

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(scan_stream8_url, item): item for item in pool}

        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    tvg_info = f" [TVG-ID: {result['tvg_id']}]" if result.get("tvg_id") else ""
                    print(f"[FOUND] STREAM8 ID={result['id']} -> {result['name']}{tvg_info}")
            except Exception:
                pass

            print(f"Прогресс: {completed}/{total}", flush=True)

    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[STREAM8] Найдено: {len(found)}")
    return found


# ============================================================
# STREAM0 / STREAM1 CHECK
# ============================================================

def check_mirror(base_url, stream8_item, group_name):
    session = make_session()
    url = build_url(base_url, stream8_item["id"])

    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return None

        text = response.text
        if not text or "#EXTINF:" not in text:
            return None

        extinf_line = extract_extinf_line(text)
        if not extinf_line:
            return None

        return {
            "id": stream8_item["id"],
            "name": stream8_item["name"],
            "tvg_id": stream8_item.get("tvg_id"),
            "group": group_name,
            "url": url,
        }

    except Exception:
        return None
    finally:
        session.close()


def build_mirror_pool(stream8_channels):
    jobs = []
    for item in stream8_channels:
        jobs.append((STREAM0_BASE, "STREAM0", item))
        jobs.append((STREAM1_BASE, "STREAM1", item))

    result = []
    total = len(jobs)
    completed = 0

    print()
    print("=" * 70)
    print("ПРОВЕРКА STREAM0 / STREAM1")
    print(f"Всего проверок: {total}")
    print("=" * 70)
    print()

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(check_mirror, base_url, item, group_name): (group_name, item["id"])
            for base_url, group_name, item in jobs
        }

        for future in as_completed(futures):
            completed += 1
            try:
                item = future.result()
                if item:
                    result.append(item)
                    tvg_info = f" [TVG-ID: {item['tvg_id']}]" if item.get("tvg_id") else ""
                    print(f"[LIVE] {item['group']} ID={item['id']} -> {item['name']}{tvg_info}")
            except Exception:
                pass

            print(f"Прогресс: {completed}/{total}", flush=True)

    result.sort(key=lambda x: (int(x["id"]), 0 if x["group"] == "STREAM0" else 1))
    print()
    print(f"[MIRRORS] Живых потоков: {len(result)}")
    return result


# ============================================================
# WRITE M3U
# ============================================================

def make_extinf(item, channel_number):
    tvg_id = item.get("tvg_id") or f"cinerama_{item['id']}"
    group = item["group"]
    name = item["name"]

    return (
        f'#EXTINF:-1 '
        f'tvg-chno="{channel_number}" '
        f'tvg-id="{tvg_id}" '
        f'group-title="{group}",'
        f'{name}'
    )


def write_playlist(items):
    ranked = sorted(items, key=lambda x: (int(x["id"]), 0 if x["group"] == "STREAM0" else 1))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Playlist verified by {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        channel_number = 1
        for item in ranked:
            f.write(make_extinf(item, channel_number) + "\n")
            f.write(item["url"] + "\n")
            channel_number += 1


# ============================================================
# REPORT
# ============================================================

def write_report(stream8_count, total_live, stream0_count, stream1_count, items):
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Stream8 найдено: {stream8_count}\n")
        f.write(f"Stream0 живых: {stream0_count}\n")
        f.write(f"Stream1 живых: {stream1_count}\n")
        f.write(f"ИТОГО: {total_live}\n")
        f.write("\n")

        for item in sorted(items, key=lambda x: (int(x["id"]), 0 if x["group"] == "STREAM0" else 1)):
            f.write(f"{item['group']} | ID={item['id']} | {item['name']} | URL={item['url']}\n")


# ============================================================
# COMPARE ONLY WITH MEGA FILE
# ============================================================

def parse_m3u(filename):
    result = OrderedDict()

    if not os.path.exists(filename):
        return result

    try:
        with open(filename, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.rstrip("\n") for line in f]
    except Exception:
        return result

    current_extinf = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue

        if line.startswith("http") and current_extinf:
            url = line

            if "stream0.cinerama.uz" in url:
                group = "STREAM0"
            elif "stream1.cinerama.uz" in url:
                group = "STREAM1"
            elif "stream8.cinerama.uz" in url:
                group = "STREAM8"
            else:
                current_extinf = None
                continue

            match = re.search(r"cinerama\.uz/(\d+)/", url)
            if not match:
                current_extinf = None
                continue

            stream_id = int(match.group(1))
            name = extract_extinf_name(current_extinf)

            key = (group, stream_id)
            result[key] = {
                "id": stream_id,
                "name": name or "",
                "group": group,
                "url": url,
            }

            current_extinf = None

    return result


def make_key(item):
    return (item["group"], int(item["id"]))


def build_current_map(current):
    result = OrderedDict()
    for item in current:
        result[make_key(item)] = item
    return result


def compare_with_mega(current):
    if not os.path.exists(COMPARE_FILE):
        return {
            "file": COMPARE_FILE,
            "old_count": 0,
            "added": [],
            "removed": [],
            "changed_name": [],
            "unchanged": [],
        }

    old_map = parse_m3u(COMPARE_FILE)
    current_map = build_current_map(current)

    report = {
        "file": COMPARE_FILE,
        "old_count": len(old_map),
        "added": [],
        "removed": [],
        "changed_name": [],
        "unchanged": [],
    }

    old_keys = set(old_map.keys())
    new_keys = set(current_map.keys())

    for key in sorted(new_keys - old_keys):
        report["added"].append(current_map[key])

    for key in sorted(old_keys - new_keys):
        report["removed"].append(old_map[key])

    for key in sorted(old_keys & new_keys):
        old = old_map[key]
        new = current_map[key]

        old_name = clean_name(old.get("name", ""))
        new_name = clean_name(new.get("name", ""))

        if old_name != new_name:
            report["changed_name"].append({"old": old, "new": new})
        else:
            report["unchanged"].append(new)

    return report


def append_compare_report(report):
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write("\n\n")
        f.write("=" * 70 + "\n")
        f.write(f"СВЕРКА С: {report['file']}\n")
        f.write("=" * 70 + "\n")
        f.write(f"Было: {report['old_count']}\n")
        f.write(f"Новых: {len(report['added'])}\n")
        f.write(f"Исчезло: {len(report['removed'])}\n")
        f.write(f"Изменено название: {len(report['changed_name'])}\n")
        f.write(f"Без изменений: {len(report['unchanged'])}\n")
        f.write("\n")

        if report["added"]:
            f.write("ДОБАВЛЕНЫ:\n")
            for item in report["added"]:
                f.write(f"+ {item['group']} ID={item['id']} {item['name']}\n")
            f.write("\n")

        if report["removed"]:
            f.write("ИСЧЕЗЛИ:\n")
            for item in report["removed"]:
                f.write(f"- {item['group']} ID={item['id']} {item['name']}\n")
            f.write("\n")

        if report["changed_name"]:
            f.write("ИЗМЕНИЛОСЬ НАЗВАНИЕ:\n")
            for item in report["changed_name"]:
                old = item["old"]
                new = item["new"]
                f.write(f"* {new['group']} ID={new['id']}\n")
                f.write(f"  БЫЛО: {old['name']}\n")
                f.write(f"  СТАЛО: {new['name']}\n")
            f.write("\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER")
    print("=" * 70)
    print()
    print("Логика: весь пул формируется -> потом сканируется")
    print("Прошлые проходы НЕ ищем")
    print()

    stream8_channels = scan_stream8_pool()
    if not stream8_channels:
        print("[ERROR] Stream8 ничего не найдено.")
        return

    live = build_mirror_pool(stream8_channels)
    if not live:
        print("[ERROR] Stream0 / Stream1 ничего не найдено.")
        return

    stream0_count = sum(1 for item in live if item["group"] == "STREAM0")
    stream1_count = sum(1 for item in live if item["group"] == "STREAM1")

    write_playlist(live)
    write_report(len(stream8_channels), len(live), stream0_count, stream1_count, live)

    compare_report = compare_with_mega(live)
    append_compare_report(compare_report)

    print()
    print("=" * 70)
    print("ГОТОВО")
    print("=" * 70)
    print()
    print(f"Stream8 найдено: {len(stream8_channels)}")
    print(f"Stream0 живых:  {stream0_count}")
    print(f"Stream1 живых:  {stream1_count}")
    print(f"ИТОГО:          {len(live)}")
    print(f"M3U: {OUTPUT_FILE}")
    print(f"TXT: {REPORT_FILE}")
    print()
    print("Сравнение идет только с одним mega-файлом.")
    print("Прошлые проходы не сканируются.")
    print()


if __name__ == "__main__":
    main()