import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import OrderedDict

# ============================================================
# CONFIG
# ============================================================

POOL_M3U_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/pool_tv.m3u"

STREAM0_BASE = "https://stream0.cinerama.uz"
STREAM1_BASE = "https://stream1.cinerama.uz"

OUTPUT_M3U = "cinerama_Verified_1.m3u"
REPORT_TXT = "SKALA_DREG_REPORT.txt"
TEST_M3U = "test_cinerama_300.m3u"

MAX_THREADS = 50
TIMEOUT = 8

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
# M3U PARSING
# ============================================================

def extract_stream_id(url):
    """Извлекает ID канала из URL"""
    m = re.search(r'cinerama\.uz/(\d+)/', url)
    if m:
        return int(m.group(1))
    return None


def parse_pool_m3u(text):
    """Парсит pool_tv.m3u и извлекает EXTINF + URL"""
    channels = OrderedDict()
    
    current_extinf = None
    
    for line in text.splitlines():
        line = line.strip()
        
        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue
        
        if current_extinf and line.startswith("http"):
            url = line
            stream_id = extract_stream_id(url)
            
            if stream_id is not None:
                channels[stream_id] = {
                    "id": stream_id,
                    "extinf": current_extinf,
                    "url": url,
                }
            
            current_extinf = None
    
    return channels


# ============================================================
# LOAD POOL M3U
# ============================================================

def load_pool_m3u():
    session = make_session()
    try:
        print()
        print("=" * 70)
        print("LOAD POOL_TV.M3U")
        print("=" * 70)
        print()
        
        response = session.get(POOL_M3U_URL, timeout=15)
        if response.status_code != 200:
            print(f"[ERROR] Status: {response.status_code}")
            return OrderedDict()
        
        channels = parse_pool_m3u(response.text)
        print(f"[SUCCESS] Loaded {len(channels)} channels from pool_tv.m3u")
        print(f"ID range: {min(channels.keys())} - {max(channels.keys())}")
        print()
        
        return channels
    
    except Exception as e:
        print(f"[ERROR] {e}")
        return OrderedDict()
    finally:
        session.close()


# ============================================================
# VERIFY CHANNEL URL + GET EXTINF FROM RESPONSE
# ============================================================

def verify_and_extract_extinf(url):
    """
    Проверяет URL на live и извлекает #EXTINF из ответа сервера
    """
    session = make_session()
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        
        if response.status_code != 200:
            return None
        
        text = response.text
        if not text or "#EXTINF:" not in text:
            return None
        
        # Извлекаем первую EXTINF строку из ответа
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF:"):
                return line
        
        return None
    
    except Exception:
        return None
    finally:
        session.close()


# ============================================================
# VERIFY STREAM1 FROM POOL
# ============================================================

def verify_stream1_from_pool(channels):
    """Проверяет все каналы из пула на Stream1"""
    result = []
    total = len(channels)
    completed = 0
    
    print()
    print("=" * 70)
    print(f"VERIFY STREAM1 FROM POOL ({total} channels)")
    print("=" * 70)
    print()
    
    jobs = []
    for stream_id, channel_data in channels.items():
        url = build_url(STREAM1_BASE, stream_id)
        jobs.append((stream_id, url, channel_data))
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(verify_and_extract_extinf, url): (stream_id, channel_data)
            for stream_id, url, channel_data in jobs
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                extinf_from_server = future.result()
                stream_id, channel_data = futures[future]
                
                if extinf_from_server:
                    url = build_url(STREAM1_BASE, stream_id)
                    
                    result.append({
                        "id": stream_id,
                        "extinf": extinf_from_server,  # Из сервера, не из пула!
                        "url": url,
                        "group": "Stream1",
                    })
                    
                    # Извлекаем имя из EXTINF для вывода
                    m = re.search(r',(.+)$', extinf_from_server)
                    name = m.group(1) if m else f"Channel {stream_id}"
                    print(f"[LIVE] Stream1 ID={stream_id} -> {name}")
            
            except Exception:
                pass
            
            if completed % 100 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    result.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Live Stream1: {len(result)}")
    print()
    
    return result


# ============================================================
# CHECK STREAM0 MIRROR
# ============================================================

def check_stream0_mirror(live_stream1):
    """Проверяет Stream0 для всех живых Stream1"""
    result = []
    total = len(live_stream1)
    completed = 0
    
    print()
    print("=" * 70)
    print(f"CHECK STREAM0 MIRRORS ({total} channels)")
    print("=" * 70)
    print()
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(verify_and_extract_extinf, build_url(STREAM0_BASE, item["id"])): item
            for item in live_stream1
        }
        
        for future in as_completed(futures):
            completed += 1
            try:
                extinf_from_server = future.result()
                item = futures[future]
                
                if extinf_from_server:
                    result.append({
                        "id": item["id"],
                        "extinf": extinf_from_server,
                        "url": build_url(STREAM0_BASE, item["id"]),
                        "group": "Stream0",
                    })
                    
                    m = re.search(r',(.+)$', extinf_from_server)
                    name = m.group(1) if m else f"Channel {item['id']}"
                    print(f"[LIVE] Stream0 ID={item['id']} -> {name}")
            
            except Exception:
                pass
            
            if completed % 100 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    result.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Live Stream0: {len(result)}")
    print()
    
    return result


# ============================================================
# CREATE TEST M3U 300
# ============================================================

def create_test_m3u_300():
    with open(TEST_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Test playlist by {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        for i in range(1, 301):
            if i <= 150:
                group = "Stream0"
                url = build_url(STREAM0_BASE, i)
            else:
                group = "Stream1"
                url = build_url(STREAM1_BASE, i)
            
            name = f"Test Channel {i}"
            extinf = f'#EXTINF:-1 group-title="{group}",{name}'
            
            f.write(extinf + "\n")
            f.write(url + "\n")
    
    print(f"[SUCCESS] Test M3U created: {TEST_M3U}")
    print()


# ============================================================
# WRITE FINAL M3U
# ============================================================

def write_final_m3u(stream0_live, stream1_live):
    """Пишет M3U с реальными EXTINF из потоков"""
    all_items = stream0_live + stream1_live
    all_items = sorted(all_items, key=lambda x: (int(x["id"]), 0 if x["group"] == "Stream0" else 1))
    
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Verified by {COPYRIGHT}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        for idx, item in enumerate(all_items, 1):
            extinf = item["extinf"]
            
            # Добавляем tvg-chno и group-title если их нет
            if 'tvg-chno=' not in extinf:
                extinf = extinf.replace('#EXTINF:-1', f'#EXTINF:-1 tvg-chno="{idx}"', 1)
            
            if f'group-title="{item["group"]}"' not in extinf:
                extinf = extinf.replace('#EXTINF:-1', f'#EXTINF:-1 group-title="{item["group"]}"', 1)
            
            f.write(extinf + "\n")
            f.write(item["url"] + "\n")
    
    print(f"[SUCCESS] Final M3U written: {OUTPUT_M3U}")
    print()


# ============================================================
# WRITE REPORT
# ============================================================

def write_report(pool_count, stream0_live, stream1_live):
    total = len(stream0_live) + len(stream1_live)
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA VERIFIED 1 — REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        
        f.write("SUMMARY\n")
        f.write("-" * 70 + "\n")
        f.write(f"Pool channels: {pool_count}\n")
        f.write(f"Stream0 live: {len(stream0_live)}\n")
        f.write(f"Stream1 live: {len(stream1_live)}\n")
        f.write(f"Total verified: {total}\n")
        f.write("\n")
        
        f.write("STREAM0 LIVE\n")
        f.write("-" * 70 + "\n")
        for item in stream0_live[:50]:
            f.write(f"ID={item['id']} | {item['extinf'][:80]} | {item['url']}\n")
        if len(stream0_live) > 50:
            f.write(f"... and {len(stream0_live) - 50} more\n")
        f.write("\n")
        
        f.write("STREAM1 LIVE\n")
        f.write("-" * 70 + "\n")
        for item in stream1_live[:50]:
            f.write(f"ID={item['id']} | {item['extinf'][:80]} | {item['url']}\n")
        if len(stream1_live) > 50:
            f.write(f"... and {len(stream1_live) - 50} more\n")
        f.write("\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER - FROM pool_tv.m3u")
    print("=" * 70)
    
    # Create test
    create_test_m3u_300()
    
    # Load pool
    pool_channels = load_pool_m3u()
    if not pool_channels:
        print("[ERROR] pool_tv.m3u not loaded")
        return
    
    # Verify Stream1
    stream1_live = verify_stream1_from_pool(pool_channels)
    if not stream1_live:
        print("[ERROR] No live channels in Stream1")
        return
    
    # Check Stream0 mirror
    stream0_live = check_stream0_mirror(stream1_live)
    
    # Write outputs
    write_final_m3u(stream0_live, stream1_live)
    write_report(len(pool_channels), stream0_live, stream1_live)
    
    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print()
    print(f"Pool channels: {len(pool_channels)}")
    print(f"Live Stream0: {len(stream0_live)}")
    print(f"Live Stream1: {len(stream1_live)}")
    print(f"Total: {len(stream0_live) + len(stream1_live)}")
    print()
    print("FILES:")
    print(f"  {OUTPUT_M3U}")
    print(f"  {REPORT_TXT}")
    print(f"  {TEST_M3U}")
    print()


if __name__ == "__main__":
    main()