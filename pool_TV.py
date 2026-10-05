import re
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

POOL_M3U_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/playlist.m3u"
OUTPUT_M3U = "pull_correct.m3u"
REPORT_TXT = "pull_correct_report.txt"

MAX_THREADS = 50
TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# ============================================================
# SESSION
# ============================================================

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ============================================================
# PARSE M3U
# ============================================================

def parse_m3u(text):
    """Парсит M3U и возвращает список каналов"""
    channels = OrderedDict()
    current_extinf = None
    
    for line in text.splitlines():
        line = line.strip()
        
        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue
        
        if current_extinf and line.startswith("http"):
            url = line
            channels[url] = {
                "url": url,
                "extinf_original": current_extinf,
                "extinf_live": None,
                "is_live": False
            }
            current_extinf = None
    
    return channels


# ============================================================
# LOAD PLAYLIST
# ============================================================

def load_pool_m3u():
    session = make_session()
    try:
        print("\n" + "=" * 70)
        print("LOADING PLAYLIST.M3U FROM REPO")
        print("=" * 70 + "\n")
        
        response = session.get(POOL_M3U_URL, timeout=15)
        if response.status_code != 200:
            print(f"[ERROR] Status: {response.status_code}")
            return OrderedDict()
        
        channels = parse_m3u(response.text)
        print(f"[OK] Loaded {len(channels)} channels\n")
        
        return channels
    
    except Exception as e:
        print(f"[ERROR] {e}\n")
        return OrderedDict()
    finally:
        session.close()


# ============================================================
# GET EXTINF FROM LIVE STREAM
# ============================================================

def get_extinf_from_stream(url):
    """
    Проверяет доступность потока и извлекает #EXTINF
    Возвращает extinf или None
    """
    session = make_session()
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
        
        if response.status_code != 200:
            return None
        
        # Читаем первые 10KB для поиска #EXTINF
        text = response.text[:10000]
        
        if not text or "#EXTINF:" not in text:
            return None
        
        # Ищем первую EXTINF строку
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
# VERIFY ALL STREAMS
# ============================================================

def verify_all_streams(channels):
    """Проверяет все потоки и получает актуальные EXTINF"""
    total = len(channels)
    completed = 0
    
    print("=" * 70)
    print(f"VERIFYING STREAMS ({total} total)")
    print("=" * 70 + "\n")
    
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(get_extinf_from_stream, url): url
            for url in channels
        }
        
        for future in as_completed(futures):
            completed += 1
            url = futures[future]
            
            try:
                live_extinf = future.result()
                if live_extinf:
                    channels[url]["extinf_live"] = live_extinf
                    channels[url]["is_live"] = True
                    
                    # Вытаскиваем имя канала
                    m = re.search(r',(.+)$', live_extinf)
                    name = m.group(1) if m else "Unknown"
                    print(f"[LIVE ✓] {name}")
                else:
                    print(f"[DEAD ✗] {url[:60]}...")
            
            except Exception as e:
                print(f"[ERROR] {url[:60]}...")
            
            # Прогресс
            if completed % 50 == 0 or completed == total:
                pct = (completed * 100) // total
                live = sum(1 for ch in channels.values() if ch["is_live"])
                print(f"Progress: {completed}/{total} ({pct}%) | Live: {live}", flush=True)
    
    live_count = sum(1 for ch in channels.values() if ch["is_live"])
    print(f"\n[RESULT] Live channels: {live_count}/{total}\n")
    
    return channels


# ============================================================
# WRITE FINAL M3U
# ============================================================

def write_final_m3u(channels):
    """Пишет финальный плейлист pull_correct.m3u"""
    
    # Оставляем только живые каналы
    live_channels = {url: ch for url, ch in channels.items() if ch["is_live"]}
    
    # Сортируем по исходному порядку
    live_channels = OrderedDict(sorted(live_channels.items(), 
                                      key=lambda x: list(channels.keys()).index(x[0])))
    
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Verified Playlist\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Live channels: {len(live_channels)}\n")
        f.write("\n")
        
        for idx, (url, ch) in enumerate(live_channels.items(), 1):
            # Берём EXTINF из live потока
            extinf = ch["extinf_live"]
            
            # Добавляем tvg-chno если его нет
            if 'tvg-chno=' not in extinf:
                extinf = extinf.replace('#EXTINF:-1', f'#EXTINF:-1 tvg-chno="{idx}"', 1)
            
            f.write(extinf + "\n")
            f.write(url + "\n")
    
    print(f"[OK] Playlist saved: {OUTPUT_M3U}")
    print(f"[OK] Total channels: {len(live_channels)}\n")
    
    return len(live_channels)


# ============================================================
# WRITE REPORT
# ============================================================

def write_report(total, live):
    """Пишет отчет проверки"""
    success_rate = (live / total * 100) if total > 0 else 0
    
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("PLAYLIST VERIFICATION REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total channels in pool: {total}\n")
        f.write(f"Live channels: {live}\n")
        f.write(f"Dead channels: {total - live}\n")
        f.write(f"Success rate: {success_rate:.1f}%\n")
        f.write("=" * 70 + "\n")
    
    print(f"[OK] Report saved: {REPORT_TXT}\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 70)
    print("PLAYLIST VERIFICATION & CORRECTION")
    print("=" * 70)
    
    # Load playlist
    channels = load_pool_m3u()
    if not channels:
        print("[ERROR] Playlist not loaded")
        return
    
    total_channels = len(channels)
    
    # Verify streams
    channels = verify_all_streams(channels)
    
    # Get live count
    live_channels = sum(1 for ch in channels.values() if ch["is_live"])
    
    if live_channels == 0:
        print("[ERROR] No live channels found")
        return
    
    # Write outputs
    write_final_m3u(channels)
    write_report(total_channels, live_channels)
    
    print("=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    print(f"Total: {total_channels}")
    print(f"Live: {live_channels}")
    print(f"Dead: {total_channels - live_channels}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()