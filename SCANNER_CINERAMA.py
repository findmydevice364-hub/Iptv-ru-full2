import os
import re
import requests
from datetime import datetime
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================

MEGA_PLAYLIST_URL = (
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/"
    "refs/heads/main/IPTV_MEGA_PLAYLIST.m3u"
)

STREAM8_BASE = "https://stream8.cinerama.uz"
STREAM0_BASE = "https://stream0.cinerama.uz"
STREAM1_BASE = "https://stream1.cinerama.uz"

OUTPUT_M3U = "cinerama_Verified_1.m3u"
REPORT_TXT = "SKALA_DREG_REPORT.txt"

MAX_THREADS = 50
TIMEOUT = 5

START_ID = 0
END_ID = 6000

COPYRIGHT = "© Phoenix 89S - Verify 1"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

# ============================================================
# HTTP
# ============================================================

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def build_url(base, stream_id):
    return f"{base}/{stream_id}/tracks-v1a1/mono.m3u8"


# ============================================================
# EXTRACT EXTINF
# ============================================================

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
    line = extract_extinf_line(text)
    if not line or "," not in line:
        return None
    name = line.split(",", 1)[1].strip()
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_tvg_id(extinf_text):
    if not extinf_text:
        return None
    m = re.search(r'tvg-id=["\']?([^"\',\s]+)', extinf_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


# ============================================================
# LOAD MEGA PLAYLIST AND EXTRACT CINERAMA IDS
# ============================================================

def load_mega_playlist():
    """Загружает мега плейлист и извлекает все Cinerama ID"""
    print("[INFO] Загружаем МЕГА плейлист...")
    
    session = make_session()
    try:
        response = session.get(MEGA_PLAYLIST_URL, timeout=10)
        if response.status_code != 200:
            print(f"[ERROR] Не удалось загрузить мега плейлист: {response.status_code}")
            return set()
        
        text = response.text
        ids = set()
        
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("http"):
                continue
            
            # Ищем cinerama.uz/ID/
            match = re.search(r'cinerama\.uz/(\d+)/', line)
            if match:
                cid = int(match.group(1))
                ids.add(cid)
        
        print(f"[INFO] Извлечено {len(ids)} уникальных ID из мега плейлиста")
        return ids
    
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки мега: {e}")
        return set()
    finally:
        session.close()


# ============================================================
# SCAN STREAM8
# ============================================================

def scan_stream8_url(item):
    session = make_session()
    stream_id = item["id"]
    url = item["url"]
    
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
            "group": "Stream8",
            "status": 200,
        }
    
    except Exception:
        return None
    finally:
        session.close()


def scan_stream8(mega_ids):
    """Сканирует все ID из мега плейлиста в Stream8"""
    
    if not mega_ids:
        mega_ids = set(range(START_ID, END_ID + 1))
    
    pool = [
        {"id": cid, "url": build_url(STREAM8_BASE, cid)}
        for cid in sorted(mega_ids)
    ]
    
    found = []
    total = len(pool)
    completed = 0
    
    print()
    print("=" * 70)
    print(f"STREAM8: СКАНИРОВАНИЕ {total} ID")
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
                    print(f"[FOUND] STREAM8 ID={result['id']} -> {result['name']}")
            except Exception:
                pass
            
            print(f"Прогресс: {completed}/{total}", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[STREAM8] Найдено живых: {len(found)}")
    return found


# ============================================================
# CHECK MIRRORS (Stream0, Stream1)
# ============================================================

def check_mirror(base_url, stream8_item, group_name):
    session = make_session()
    stream_id = stream8_item["id"]
    url = build_url(base_url, stream_id)
    
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
            "id": stream_id,
            "name": stream8_item["name"],
            "tvg_id": stream8_item.get("tvg_id"),
            "url": url,
            "group": group_name,
            "status": 200,
        }
    
    except Exception:
        return None
    finally:
        session.close()


def check_mirrors(stream8_channels):
    """Проверяет Stream0 и Stream1 для всех найденных Stream8"""
    
    jobs = []
    for item in stream8_channels:
        jobs.append((STREAM0_BASE, "Stream0", item))
        jobs.append((STREAM1_BASE, "Stream1", item))
    
    result = []
    total = len(jobs)
    completed = 0
    
    print()
    print("=" * 70)
    print(f"STREAM0/STREAM1: ПРОВЕРКА ЗЕРКАЛ ({total} проверок)")
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
                    print(f"[LIVE] {item['group']} ID={item['id']} -> {item['name']}")
            except Exception:
                pass
            
            print(f"Прогресс: {completed}/{total}", flush=True)
    
    result.sort(key=lambda x: (int(x["id"]), 0 if x["group"] == "Stream0" else 1))
    print()
    print(f"[MIRRORS] Живых потоков: {len(result)}")
    return result


# ============================================================
# MAKE M3U
# ============================================================

def make_extinf(item, channel_number):
    tvg_id = item.get("tvg_id") or f"cinerama_{item['id']}"
    return (
        f'#EXTINF:-1 '
        f'tvg-chno="{channel_number}" '
        f'tvg-id="{tvg_id}" '
        f'group-title="{item["group"]}",'
        f'{item["name"]}'
    )


def write_m3u(stream8_items, stream0_items, stream1_items):
    """Пишет все три потока в один M3U"""
    
    all_items = stream8_items + stream0_items + stream1_items
    ranked = sorted(all_items, key=lambda x: (
        int(x["id"]),
        {"Stream8": 0, "Stream0": 1, "Stream1": 2}.get(x["group"], 99)
    ))
    
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Playlist verified by {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        channel_number = 1
        for item in ranked:
            f.write(make_extinf(item, channel_number) + "\n")
            f.write(item["url"] + "\n")
            channel_number += 1


# ============================================================
# WRITE REPORT
# ============================================================

def write_report(stream8_count, stream0_count, stream1_count, all_items):
    """Пишет полный отчет со статистикой"""
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — FULL REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("STATISTICS\n")
        f.write("-" * 70 + "\n")
        f.write(f"Stream8 found: {stream8_count}\n")
        f.write(f"Stream0 live: {stream0_count}\n")
        f.write(f"Stream1 live: {stream1_count}\n")
        f.write(f"Total live: {len(all_items)}\n")
        f.write("\n")
        
        f.write("DETAILS\n")
        f.write("-" * 70 + "\n")
        
        for item in sorted(all_items, key=lambda x: (int(x["id"]), x["group"])):
            f.write(f"{item['group']} | ID={item['id']} | {item['name']} | {item['url']}\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER")
    print("=" * 70)
    print()
    
    # 1. Load mega playlist
    mega_ids = load_mega_playlist()
    if not mega_ids:
        print("[ERROR] Не удалось загрузить мега плейлист")
        return
    
    # 2. Scan Stream8
    stream8_items = scan_stream8(mega_ids)
    if not stream8_items:
        print("[ERROR] Stream8 не найдено")
        return
    
    # 3. Check Stream0 and Stream1
    mirrors = check_mirrors(stream8_items)
    if not mirrors:
        print("[ERROR] Зеркала не найдены")
        return
    
    # 4. Separate Stream0 and Stream1
    stream0_items = [item for item in mirrors if item["group"] == "Stream0"]
    stream1_items = [item for item in mirrors if item["group"] == "Stream1"]
    
    # 5. Write M3U
    write_m3u(stream8_items, stream0_items, stream1_items)
    
    # 6. Write report
    all_items = stream8_items + stream0_items + stream1_items
    write_report(len(stream8_items), len(stream0_items), len(stream1_items), all_items)
    
    # 7. Statistics
    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print()
    print(f"Stream8 found: {len(stream8_items)}")
    print(f"Stream0 live: {len(stream0_items)}")
    print(f"Stream1 live: {len(stream1_items)}")
    print(f"Total: {len(all_items)}")
    print()
    print(f"M3U: {OUTPUT_M3U}")
    print(f"TXT: {REPORT_TXT}")
    print()


if __name__ == "__main__":
    main()