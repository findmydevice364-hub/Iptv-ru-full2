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
POOL_ANALYSIS_TXT = "POOL_ANALYSIS.txt"

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
# EXTINF EXTRACT
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


def extract_tvg_logo(extinf_text):
    if not extinf_text:
        return None
    m = re.search(r'tvg-logo=["\']?([^"\']+)["\']?', extinf_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


# ============================================================
# STEP 1: LOAD MEGA PLAYLIST (reference)
# ============================================================

def load_mega_playlist():
    print()
    print("=" * 70)
    print("STEP 1: LOAD MEGA PLAYLIST (REFERENCE)")
    print("=" * 70)
    print()
    
    session = make_session()
    mega_items = OrderedDict()
    
    try:
        response = session.get(MEGA_PLAYLIST_URL, timeout=10)
        if response.status_code != 200:
            print(f"[WARN] MEGA: {response.status_code}")
            return mega_items
        
        current_extinf = None
        
        for line in response.text.splitlines():
            line = line.strip()
            
            if line.startswith("#EXTINF:"):
                current_extinf = line
                continue
            
            if current_extinf and line.startswith("http"):
                url = line
                match = re.search(r'cinerama\.uz/(\d+)/', url)
                if match:
                    cid = int(match.group(1))
                    name = extract_extinf_name(current_extinf)
                    if name:
                        mega_items[cid] = {"id": cid, "name": name, "url": url}
                current_extinf = None
        
        print(f"[SUCCESS] MEGA loaded: {len(mega_items)} IDs")
        print()
        return mega_items
    
    except Exception as e:
        print(f"[ERROR] {e}")
        return mega_items
    finally:
        session.close()


# ============================================================
# STEP 2: GENERATE FULL URL POOL (0-6000)
# ============================================================

def generate_full_url_pool():
    """
    Генерирует ПОЛНЫЙ пул всех ссылок для Stream8 (0-6000).
    Каждая ссылка будет проверена на наличие #EXTINF
    """
    print()
    print("=" * 70)
    print("STEP 2: GENERATE FULL URL POOL (0-6000)")
    print("=" * 70)
    print()
    
    pool = []
    for stream_id in range(START_ID, END_ID + 1):
        pool.append({
            "id": stream_id,
            "url": build_url(STREAM8_BASE, stream_id)
        })
    
    print(f"[SUCCESS] Generated {len(pool)} URLs for Stream8")
    print(f"Example: {pool[0]['url']}")
    print(f"Example: {pool[100]['url']}")
    print()
    
    return pool


# ============================================================
# STEP 3: SCAN ALL URLS FOR EXTINF (Stream8)
# ============================================================

def scan_stream8_url(item):
    """Проверяет одну ссылку Stream8 на наличие #EXTINF"""
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
        tvg_logo = extract_tvg_logo(extinf_line)
        
        return {
            "id": stream_id,
            "name": name,
            "tvg_id": tvg_id,
            "tvg_logo": tvg_logo,
            "url": url,
            "group": "Stream8",
            "extinf_line": extinf_line,
        }
    
    except Exception:
        return None
    finally:
        session.close()


def step_3_scan_stream8(full_pool):
    """
    Сканирует ВСЕ 6001 ссылок Stream8.
    Ищет #EXTINF, извлекает названия, logos, tvg-id
    """
    print()
    print("=" * 70)
    print("STEP 3: SCAN ALL STREAM8 URLs FOR EXTINF (0-6000)")
    print("=" * 70)
    print()
    
    found = []
    total = len(full_pool)
    completed = 0
    
    print(f"[INFO] Scanning {total} Stream8 URLs...")
    print()
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(scan_stream8_url, item): item
            for item in full_pool
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    logo_info = f" [LOGO: {result['tvg_logo'][:40]}...]" if result.get("tvg_logo") else ""
                    print(f"[FOUND] ID={result['id']:<5} -> {result['name']}{logo_info}")
            except Exception:
                pass
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream8 live with EXTINF: {len(found)}")
    print()
    
    return found


# ============================================================
# STEP 4: CHECK STREAM0 MIRRORS
# ============================================================

def check_mirror_url(base_url, stream_id, channel_name, group_name):
    """Проверяет одну ссылку зеркала (Stream0 или Stream1)"""
    session = make_session()
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
            "name": channel_name,
            "url": url,
            "group": group_name,
            "extinf_line": extinf_line,
        }
    
    except Exception:
        return None
    finally:
        session.close()


def step_4_check_stream0(stream8_items):
    """
    Проверяет Stream0 зеркала.
    Берём ID из Stream8, конвертим домен, проверяем живость
    """
    print()
    print("=" * 70)
    print("STEP 4: CHECK STREAM0 MIRRORS")
    print("=" * 70)
    print()
    
    found = []
    total = len(stream8_items)
    completed = 0
    
    print(f"[INFO] Checking {total} Stream0 URLs...")
    print()
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(check_mirror_url, STREAM0_BASE, item["id"], item["name"], "Stream0"): item["id"]
            for item in stream8_items
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    print(f"[LIVE] Stream0 ID={result['id']:<5} -> {result['name']}")
            except Exception:
                pass
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream0 live: {len(found)}")
    print()
    
    return found


# ============================================================
# STEP 5: CHECK STREAM1 MIRRORS
# ============================================================

def step_5_check_stream1(stream8_items):
    """
    Проверяет Stream1 зеркала.
    Берём ID из Stream8, конвертим домен, проверяем живость
    """
    print()
    print("=" * 70)
    print("STEP 5: CHECK STREAM1 MIRRORS")
    print("=" * 70)
    print()
    
    found = []
    total = len(stream8_items)
    completed = 0
    
    print(f"[INFO] Checking {total} Stream1 URLs...")
    print()
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(check_mirror_url, STREAM1_BASE, item["id"], item["name"], "Stream1"): item["id"]
            for item in stream8_items
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    print(f"[LIVE] Stream1 ID={result['id']:<5} -> {result['name']}")
            except Exception:
                pass
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream1 live: {len(found)}")
    print()
    
    return found


# ============================================================
# WRITE M3U
# ============================================================

def make_extinf_line(item, channel_number):
    """Формирует строку #EXTINF для M3U"""
    tvg_id = item.get("tvg_id") or f"cinerama_{item['id']}"
    tvg_logo = item.get("tvg_logo") or ""
    
    if tvg_logo:
        return (
            f'#EXTINF:-1 tvg-chno="{channel_number}" '
            f'tvg-id="{tvg_id}" tvg-logo="{tvg_logo}" '
            f'group-title="{item["group"]}",'
            f'{item["name"]}'
        )
    else:
        return (
            f'#EXTINF:-1 tvg-chno="{channel_number}" '
            f'tvg-id="{tvg_id}" '
            f'group-title="{item["group"]}",'
            f'{item["name"]}'
        )


def write_m3u(stream8_items, stream0_items, stream1_items):
    """Пишет M3U со всеми тремя потоками"""
    
    all_items = stream8_items + stream0_items + stream1_items
    ranked = sorted(all_items, key=lambda x: (
        int(x["id"]),
        {"Stream8": 0, "Stream0": 1, "Stream1": 2}.get(x["group"], 99)
    ))
    
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Verified by {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total: Stream8={len(stream8_items)} | Stream0={len(stream0_items)} | Stream1={len(stream1_items)}\n")
        f.write("\n")
        
        channel_number = 1
        for item in ranked:
            f.write(make_extinf_line(item, channel_number) + "\n")
            f.write(item["url"] + "\n")
            channel_number += 1


# ============================================================
# WRITE REPORTS
# ============================================================

def write_pool_analysis(mega_items, full_pool_count):
    """Записывает анализ пула"""
    with open(POOL_ANALYSIS_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA POOL ANALYSIS\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("MEGA PLAYLIST\n")
        f.write("-" * 70 + "\n")
        f.write(f"Known IDs in MEGA: {len(mega_items)}\n")
        f.write("\n")
        
        f.write("FULL URL POOL\n")
        f.write("-" * 70 + "\n")
        f.write(f"Generated URLs: {full_pool_count} (0-{END_ID})\n")
        f.write("\n")


def write_report(mega_items, stream8_items, stream0_items, stream1_items):
    """Пишет финальный отчёт"""
    
    all_items = stream8_items + stream0_items + stream1_items
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — FINAL REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("SUMMARY\n")
        f.write("-" * 70 + "\n")
        f.write(f"MEGA known: {len(mega_items)}\n")
        f.write(f"Stream8 live: {len(stream8_items)}\n")
        f.write(f"Stream0 live: {len(stream0_items)}\n")
        f.write(f"Stream1 live: {len(stream1_items)}\n")
        f.write(f"TOTAL M3U ENTRIES: {len(all_items)}\n")
        f.write("\n")
        
        f.write("ALL CHANNELS\n")
        f.write("=" * 70 + "\n")
        
        for item in sorted(all_items, key=lambda x: (int(x["id"]), x["group"])):
            logo_str = item.get("tvg_logo", "")[:30] if item.get("tvg_logo") else "none"
            f.write(
                f"{item['group']:8} | ID={item['id']:5} | "
                f"{item['name'][:40]:40} | Logo: {logo_str:30} | "
                f"{item['url']}\n"
            )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER - FULL POOL + ALL STREAMS")
    print("=" * 70)
    
    # Step 1
    mega_items = load_mega_playlist()
    
    # Step 2
    full_pool = generate_full_url_pool()
    
    # Step 3
    stream8_items = step_3_scan_stream8(full_pool)
    if not stream8_items:
        print("[ERROR] No Stream8 channels found!")
        return
    
    # Step 4
    stream0_items = step_4_check_stream0(stream8_items)
    
    # Step 5
    stream1_items = step_5_check_stream1(stream8_items)
    
    # Write outputs
    write_pool_analysis(mega_items, len(full_pool))
    write_m3u(stream8_items, stream0_items, stream1_items)
    write_report(mega_items, stream8_items, stream0_items, stream1_items)
    
    # Summary
    print()
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print()
    print(f"MEGA: {len(mega_items)} IDs")
    print(f"Stream8: {len(stream8_items)} live")
    print(f"Stream0: {len(stream0_items)} live")
    print(f"Stream1: {len(stream1_items)} live")
    print(f"TOTAL M3U: {len(stream8_items) + len(stream0_items) + len(stream1_items)} entries")
    print()
    print(f"Files:")
    print(f"  {OUTPUT_M3U}")
    print(f"  {REPORT_TXT}")
    print(f"  {POOL_ANALYSIS_TXT}")
    print()


if __name__ == "__main__":
    main()