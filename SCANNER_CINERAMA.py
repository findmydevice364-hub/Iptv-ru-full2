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


# ============================================================
# STEP 1: LOAD MEGA PLAYLIST (reference only)
# ============================================================

def load_mega_playlist():
    """
    Mега используется как справочника / ссылки / сравнения.
    Главный опрос идёт только к реальным серверам.
    """
    print()
    print("=" * 70)
    print("STEP 1: LOAD MEGA PLAYLIST (REFERENCE ONLY)")
    print("=" * 70)
    print()
    
    session = make_session()
    mega_items = OrderedDict()
    
    try:
        response = session.get(MEGA_PLAYLIST_URL, timeout=10)
        if response.status_code != 200:
            print(f"[ERROR] MEGA: {response.status_code}")
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
                if not match:
                    current_extinf = None
                    continue
                
                cid = int(match.group(1))
                name = extract_extinf_name(current_extinf)
                
                if name:
                    mega_items[cid] = {
                        "id": cid,
                        "name": name,
                        "url": url,
                    }
                
                current_extinf = None
        
        print(f"[SUCCESS] Mega PL loaded: {len(mega_items)} known IDs")
        return mega_items
    
    except Exception as e:
        print(f"[ERROR] Mega load: {e}")
        return mega_items
    finally:
        session.close()


# ============================================================
# STEP 2: CREATE MINI POOL + FULL POOL
# ============================================================

def create_mini_pool(mega_items):
    """Создаёт мини-пул из меги; это именно справка, а не источник истины"""
    mini = OrderedDict()
    for cid, item in mega_items.items():
        mini[cid] = {
            "id": cid,
            "name": item["name"],
            "url": item["url"]
        }
    return mini


def create_full_pool():
    """Генерирует полный пул 0-6000 для Stream8"""
    full = OrderedDict()
    for cid in range(START_ID, END_ID + 1):
        full[cid] = {
            "id": cid,
            "url": build_url(STREAM8_BASE, cid)
        }
    return full


def compare_mini_vs_full(mini_pool, full_pool):
    """Сравнивает мини-пул с полным пулом"""
    mini_ids = set(mini_pool.keys())
    full_ids = set(full_pool.keys())
    
    in_both = mini_ids & full_ids
    only_in_mini = mini_ids - full_ids
    only_in_full = full_ids - mini_ids
    
    return {
        "mini_ids": mini_ids,
        "full_ids": full_ids,
        "in_both": in_both,
        "only_in_mini": only_in_mini,
        "only_in_full": only_in_full,
    }


def write_pool_analysis(mini_pool, compare_result):
    """Записывает отчет по мини-пулу и сравнению"""
    with open(POOL_ANALYSIS_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA POOL ANALYSIS\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("MEGA / MINI POOL\n")
        f.write("-" * 70 + "\n")
        f.write(f"Mini pool count: {len(mini_pool)}\n")
        f.write(f"Mini pool first 20:\n")
        
        for idx, (cid, item) in enumerate(list(mini_pool.items())[:20]):
            f.write(f"  {idx+1:2}. ID={cid:<5} | {item['name']}\n")
        
        f.write("\n")
        f.write("FULL POOL\n")
        f.write("-" * 70 + "\n")
        f.write(f"Full pool count: {len(compare_result['full_ids'])}\n")
        f.write(f"Range: {START_ID}-{END_ID}\n")
        f.write("\n")
        
        f.write("COMPARISON\n")
        f.write("-" * 70 + "\n")
        f.write(f"In both: {len(compare_result['in_both'])}\n")
        f.write(f"Only in mini: {len(compare_result['only_in_mini'])}\n")
        f.write(f"Only in full (new): {len(compare_result['only_in_full'])}\n")
        f.write("\n")
        
        if compare_result["only_in_full"]:
            f.write("First 50 IDs only in full pool:\n")
            for cid in sorted(compare_result["only_in_full"])[:50]:
                f.write(f"  ID={cid}\n")
            f.write("\n")
        
    print(f"[SUCCESS] Pool analysis written: {POOL_ANALYSIS_TXT}")
    print()


# ============================================================
# STEP 3: QUERY STREAM8 FULL POOL (REAL SOURCE)
# ============================================================

def query_stream8_url(item):
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
        }
    
    except Exception:
        return None
    finally:
        session.close()


def step_3_scan_stream8(full_pool):
    """Главный реальный процесс: опрос Stream8 по полному пулу"""
    print()
    print("=" * 70)
    print("STEP 3: QUERY STREAM8 FULL POOL (REAL SOURCE)")
    print("=" * 70)
    print()
    
    found = []
    total = len(full_pool)
    completed = 0
    
    print(f"[INFO] Checking {total} Stream8 URLs...")
    print()
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(query_stream8_url, item): item
            for item in full_pool.values()
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    tvg_info = f" [TVG-ID: {result['tvg_id']}]" if result.get("tvg_id") else ""
                    print(f"[FOUND] Stream8 ID={result['id']} -> {result['name']}{tvg_info}")
            except Exception:
                pass
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream8 live: {len(found)}")
    print()
    
    return found


# ============================================================
# STEP 4 / 5: CHECK STREAM0 + STREAM1
# ============================================================

def query_mirror(base_url, stream8_item, group_name):
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
        }
    
    except Exception:
        return None
    finally:
        session.close()


def step_4_5_check_streams(stream8_items):
    """Проверяет Stream0 и Stream1 для всех найденных Stream8"""
    print()
    print("=" * 70)
    print("STEP 4/5: CHECK STREAM0 + STREAM1 MIRRORS")
    print("=" * 70)
    print()
    
    jobs = []
    for item in stream8_items:
        jobs.append((STREAM0_BASE, "Stream0", item))
        jobs.append((STREAM1_BASE, "Stream1", item))
    
    result = []
    total = len(jobs)
    completed = 0
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(query_mirror, base_url, item, group_name): (group_name, item["id"])
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
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    result.sort(key=lambda x: (int(x["id"]), 0 if x["group"] == "Stream0" else 1))
    print()
    print(f"[SUCCESS] Stream0/Stream1 live: {len(result)}")
    print()
    
    return result


# ============================================================
# WRITE M3U AND REPORT
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


def write_report(mega_items, compare_result, stream8_items, stream0_items, stream1_items):
    all_items = stream8_items + stream0_items + stream1_items
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — FINAL REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("MEGA vs FULL POOL\\n")
        f.write("-" * 70 + "\n")
        f.write(f"MEGA IDs: {len(mega_items)}\\n")
        f.write(f"FULL POOL IDs: {len(compare_result['full_ids'])}\\n")
        f.write(f"In both: {len(compare_result['in_both'])}\\n")
        f.write(f"Only in mega: {len(compare_result['only_in_mini'])}\\n")
        f.write(f"Only in full: {len(compare_result['only_in_full'])}\\n")
        f.write("\n")
        
        f.write("LIVE SERVER STATISTICS\\n")
        f.write("-" * 70 + "\n")
        f.write(f"Stream8 live: {len(stream8_items)}\\n")
        f.write(f"Stream0 live: {len(stream0_items)}\\n")
        f.write(f"Stream1 live: {len(stream1_items)}\\n")
        f.write(f"TOTAL LIVE: {len(all_items)}\\n")
        f.write("\n")
        
        f.write("ALL ITEMS\\n")
        f.write("-" * 70 + "\n")
        for item in sorted(all_items, key=lambda x: (int(x["id"]), x["group"])):
            f.write(f"{item['group']:8} | ID={item['id']:5} | {item['name'][:50]:50} | {item['url']}\\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER")
    print("=" * 70)
    print()
    
    # 1. Load MEGA as reference
    mega_items = load_mega_playlist()
    if not mega_items:
        print("[WARN] MEGA not loaded, but direct server query will continue")
    
    mini_pool = create_mini_pool(mega_items)
    full_pool = create_full_pool()
    compare_result = compare_mini_vs_full(mini_pool, full_pool)
    write_pool_analysis(mini_pool, compare_result)
    
    # 2. Real source: demand direct scan
    stream8_items = step_3_scan_stream8(full_pool)
    if not stream8_items:
        print("[ERROR] No live channels found in Stream8")
        return
    
    # 3. Mirrors
    all_mirrors = step_4_5_check_streams(stream8_items)
    stream0_items = [item for item in all_mirrors if item["group"] == "Stream0"]
    stream1_items = [item for item in all_mirrors if item["group"] == "Stream1"]
    
    # 4. Output
    write_m3u(stream8_items, stream0_items, stream1_items)
    write_report(mega_items, compare_result, stream8_items, stream0_items, stream1_items)
    
    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print()
    print(f"MEGA IDs: {len(mega_items)}")
    print(f"STREAM8 live: {len(stream8_items)}")
    print(f"STREAM0 live: {len(stream0_items)}")
    print(f"STREAM1 live: {len(stream1_items)}")
    print(f"TOTAL M3U entries: {len(stream8_items) + len(stream0_items) + len(stream1_items)}")
    print()
    print(f"M3U: {OUTPUT_M3U}")
    print(f"REPORT: {REPORT_TXT}")
    print(f"POOL ANALYSIS: {POOL_ANALYSIS_TXT}")
    print()


if __name__ == "__main__":
    main()