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
# EXTINF EXTRACTION
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
# STEP 2.0: LOAD MEGA & CREATE MINI POOL
# ============================================================

def step_2_0_load_mega():
    """Загружает мега плейлист и создаёт mini_pool"""
    print()
    print("=" * 70)
    print("STEP 2.0: LOAD MEGA PLAYLIST & CREATE MINI POOL")
    print("=" * 70)
    print()
    
    session = make_session()
    mini_pool = OrderedDict()
    
    try:
        print(f"[INFO] Загружаем мега плейлист...")
        response = session.get(MEGA_PLAYLIST_URL, timeout=10)
        
        if response.status_code != 200:
            print(f"[ERROR] Статус: {response.status_code}")
            return mini_pool
        
        text = response.text
        current_extinf = None
        
        for line in text.splitlines():
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
                    mini_pool[cid] = {
                        "id": cid,
                        "name": name,
                        "url": url,
                    }
                
                current_extinf = None
        
        print(f"[SUCCESS] Извлечено из МЕГА: {len(mini_pool)} уникальных ID")
        print(f"Диапазон ID: {min(mini_pool.keys())} - {max(mini_pool.keys())}")
        print()
        
        return mini_pool
    
    except Exception as e:
        print(f"[ERROR] {e}")
        return mini_pool
    finally:
        session.close()


# ============================================================
# STEP 2.1: GENERATE FULL POOL (0-6000)
# ============================================================

def step_2_1_generate_full_pool():
    """Генерирует полный пул всех ID от 0 до 6000"""
    print()
    print("=" * 70)
    print("STEP 2.1: GENERATE FULL POOL (0-6000)")
    print("=" * 70)
    print()
    
    full_pool = OrderedDict()
    
    for cid in range(START_ID, END_ID + 1):
        full_pool[cid] = {
            "id": cid,
            "url": build_url(STREAM8_BASE, cid)
        }
    
    print(f"[SUCCESS] Сгенерирован полный пул: {len(full_pool)} ID (0-{END_ID})")
    print()
    
    return full_pool


# ============================================================
# STEP 2.2: COMPARE MEGA vs FULL POOL
# ============================================================

def step_2_2_compare(mini_pool, full_pool):
    """Сравнивает мега (mini_pool) с полным пулом"""
    print()
    print("=" * 70)
    print("STEP 2.2: COMPARE MEGA vs FULL POOL")
    print("=" * 70)
    print()
    
    mega_ids = set(mini_pool.keys())
    pool_ids = set(full_pool.keys())
    
    in_both = mega_ids & pool_ids
    only_in_mega = mega_ids - pool_ids
    only_in_pool = pool_ids - mega_ids
    
    print(f"МЕГА: {len(mega_ids)} ID")
    print(f"ПОЛНЫЙ пул: {len(pool_ids)} ID")
    print(f"В обоих: {len(in_both)} ID")
    print(f"Только в МЕГА (вне 0-6000): {len(only_in_mega)} ID")
    print(f"Только в ПУЛЕ (новые): {len(only_in_pool)} ID")
    print()
    
    return {
        "mega_ids": mega_ids,
        "pool_ids": pool_ids,
        "in_both": in_both,
        "only_in_mega": only_in_mega,
        "only_in_pool": only_in_pool,
    }


# ============================================================
# STEP 2.3: CREATE POOL ANALYSIS REPORT & SAVE
# ============================================================

def step_2_3_create_pool_report(mini_pool, compare_result):
    """Создаёт отчёт по шагам 2.0-2.2"""
    print()
    print("=" * 70)
    print("STEP 2.3: CREATE POOL ANALYSIS REPORT & SAVE")
    print("=" * 70)
    print()
    
    report_file = "POOL_ANALYSIS.txt"
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("POOL ANALYSIS REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("STEP 2.0: MEGA PLAYLIST\n")
        f.write("-" * 70 + "\n")
        f.write(f"Total IDs in MEGA: {len(compare_result['mega_ids'])}\n")
        f.write(f"ID range: {min(compare_result['mega_ids'])} - {max(compare_result['mega_ids'])}\n")
        f.write("\n")
        
        f.write("First 30 IDs from MEGA:\n")
        for idx, (cid, item) in enumerate(list(mini_pool.items())[:30]):
            f.write(f"  {idx+1:2}. ID={cid:<5} | {item['name']}\n")
        f.write("\n")
        
        f.write("STEP 2.1: FULL POOL\n")
        f.write("-" * 70 + "\n")
        f.write(f"Total IDs in POOL: {len(compare_result['pool_ids'])} (0-{END_ID})\n")
        f.write("\n")
        
        f.write("STEP 2.2: COMPARISON RESULTS\n")
        f.write("-" * 70 + "\n")
        f.write(f"In both MEGA and POOL: {len(compare_result['in_both'])}\n")
        f.write(f"Only in MEGA (out of 0-6000 range): {len(compare_result['only_in_mega'])}\n")
        f.write(f"Only in POOL (NEW IDs): {len(compare_result['only_in_pool'])}\n")
        f.write("\n")
        
        if compare_result['only_in_mega']:
            f.write("IDs only in MEGA (outside 0-6000):\n")
            for cid in sorted(compare_result['only_in_mega'])[:30]:
                if cid in mini_pool:
                    f.write(f"  ID={cid} | {mini_pool[cid]['name']}\n")
            if len(compare_result['only_in_mega']) > 30:
                f.write(f"  ... and {len(compare_result['only_in_mega']) - 30} more\n")
            f.write("\n")
        
        if compare_result['only_in_pool']:
            f.write(f"NEW IDs in POOL (not in MEGA): {len(compare_result['only_in_pool'])} IDs\n")
            sorted_new = sorted(compare_result['only_in_pool'])
            f.write("Range: {} - {}\n".format(sorted_new[0], sorted_new[-1]))
            f.write("\n")
    
    print(f"[SUCCESS] Отчёт сохранён: {report_file}")
    print(f"Результаты запомнены в переменной compare_result")
    print()
    
    return report_file


# ============================================================
# STEP 4: SCAN FULL POOL (all 0-6000)
# ============================================================

def scan_stream8_url(item):
    """Сканирует один URL Stream8"""
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


def step_4_scan_full_pool(full_pool):
    """Сканирует весь пул 0-6000"""
    print()
    print("=" * 70)
    print("STEP 4: SCAN FULL POOL STREAM8 (0-6000)")
    print("=" * 70)
    print()
    
    found = []
    total = len(full_pool)
    completed = 0
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(scan_stream8_url, item): item for item in full_pool.values()}
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                if result:
                    found.append(result)
                    print(f"[FOUND] Stream8 ID={result['id']} -> {result['name']}")
            except Exception:
                pass
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream8 найдено: {len(found)}")
    print()
    
    return found


# ============================================================
# STEP 5: EXTINF ALREADY EXTRACTED
# (Already done in step 4, during scan)
# ============================================================

# ============================================================
# STEP 6: CONVERT & CHECK STREAM0 + STREAM1
# ============================================================

def check_mirror(base_url, stream8_item, group_name):
    """Проверяет один URL зеркала"""
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


def step_6_check_mirrors(stream8_items):
    """Конвертирует в Stream0 и Stream1, проверяет"""
    print()
    print("=" * 70)
    print("STEP 6: CONVERT & CHECK STREAM0 + STREAM1")
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
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    result.sort(key=lambda x: (int(x["id"]), 0 if x["group"] == "Stream0" else 1))
    print()
    print(f"[SUCCESS] Mirrors найдено: {len(result)}")
    print()
    
    return result


# ============================================================
# WRITE M3U
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
        f.write(f"# {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        channel_number = 1
        for item in ranked:
            f.write(make_extinf(item, channel_number) + "\n")
            f.write(item["url"] + "\n")
            channel_number += 1


# ============================================================
# STEP 7: FINAL REPORT
# ============================================================

def step_7_final_report(mini_pool, compare_result, stream8_items, mirrors):
    """Создаёт финальный отчёт со сравнением и статистикой"""
    print()
    print("=" * 70)
    print("STEP 7: FINAL REPORT (MEGA vs POOL + STATISTICS)")
    print("=" * 70)
    print()
    
    stream0_items = [item for item in mirrors if item["group"] == "Stream0"]
    stream1_items = [item for item in mirrors if item["group"] == "Stream1"]
    all_items = stream8_items + stream0_items + stream1_items
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — FINAL REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("SECTION 1: MEGA vs FULL POOL COMPARISON\n")
        f.write("=" * 70 + "\n")
        f.write(f"МЕГА плейлист IDs: {len(compare_result['mega_ids'])}\n")
        f.write(f"ПОЛНЫЙ пул IDs (0-{END_ID}): {len(compare_result['pool_ids'])}\n")
        f.write(f"В обоих: {len(compare_result['in_both'])}\n")
        f.write(f"Только в МЕГА (вне диапазона): {len(compare_result['only_in_mega'])}\n")
        f.write(f"Только в ПУЛЕ (новые): {len(compare_result['only_in_pool'])}\n")
        f.write("\n")
        
        f.write("SECTION 2: SCANNING STATISTICS\n")
        f.write("=" * 70 + "\n")
        f.write(f"Stream8 FOUND: {len(stream8_items)}\n")
        f.write(f"Stream0 LIVE: {len(stream0_items)}\n")
        f.write(f"Stream1 LIVE: {len(stream1_items)}\n")
        f.write(f"TOTAL STREAMS: {len(all_items)}\n")
        f.write("\n")
        
        f.write("SECTION 3: FULL CHANNEL LIST\n")
        f.write("=" * 70 + "\n")
        
        for item in sorted(all_items, key=lambda x: (int(x["id"]), x["group"])):
            f.write(f"{item['group']:8} | ID={item['id']:5} | {item['name'][:45]:45} | {item['url']}\n")
    
    print(f"[SUCCESS] Финальный отчёт: {REPORT_TXT}")
    print()


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER - COMPLETE PIPELINE")
    print("=" * 70)
    
    # Step 2.0
    mini_pool = step_2_0_load_mega()
    if not mini_pool:
        print("[ERROR] mini_pool пуст")
        return
    
    # Step 2.1
    full_pool = step_2_1_generate_full_pool()
    
    # Step 2.2
    compare_result = step_2_2_compare(mini_pool, full_pool)
    
    # Step 2.3
    step_2_3_create_pool_report(mini_pool, compare_result)
    
    # Step 4
    stream8_items = step_4_scan_full_pool(full_pool)
    if not stream8_items:
        print("[ERROR] Stream8 пуст")
        return
    
    # Step 5 (Already done in step 4)
    
    # Step 6
    mirrors = step_6_check_mirrors(stream8_items)
    if not mirrors:
        print("[ERROR] Mirrors пусты")
        return
    
    # Separate
    stream0_items = [item for item in mirrors if item["group"] == "Stream0"]
    stream1_items = [item for item in mirrors if item["group"] == "Stream1"]
    
    # Write M3U
    write_m3u(stream8_items, stream0_items, stream1_items)
    
    # Step 7
    step_7_final_report(mini_pool, compare_result, stream8_items, mirrors)
    
    # Summary
    print()
    print("=" * 70)
    print("ALL STEPS COMPLETED")
    print("=" * 70)
    print()
    print(f"Files generated:")
    print(f"  - {OUTPUT_M3U}")
    print(f"  - {REPORT_TXT}")
    print(f"  - POOL_ANALYSIS.txt")
    print()


if __name__ == "__main__":
    main()