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
TEST_M3U = "test_cinerama_300.m3u"
REPORT_TXT = "SKALA_DREG_REPORT.txt"
POOL_ANALYSIS_TXT = "POOL_ANALYSIS.txt"
DEBUG_LOG = "debug.log"

MAX_THREADS = 50
TIMEOUT = 5

START_ID = 0
END_ID = 6000  # СКАНИРУЕМ ВСЕ 6000!

TEST_CHANNELS = 300  # Тестовый плейлист на 300 каналов

COPYRIGHT = "© Phoenix 89S - Verify 1"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

debug_log = []

# Выдуманные названия каналов для тестового плейлиста
TEST_CHANNEL_NAMES = [
    "Channel One", "Channel Two", "Channel Three", "Channel Four", "Channel Five",
    "Movie Zone", "Sports HD", "News Live", "Music TV", "Kids Channel",
    "Documentary", "Comedy Show", "Drama Series", "Action Movies", "Family Channel",
    "Nature World", "Tech Today", "Business News", "Weather Channel", "Travel Guide",
    "Food Network", "Fashion TV", "Art Channel", "Music Classic", "Cartoon Network",
    "Cinema Gold", "History Channel", "Science Lab", "Education Plus", "Discovery",
    "Adventure Zone", "Mystery Zone", "Romance Channel", "Horror Zone", "Fantasy Zone",
    "Animation World", "Sports Plus", "Games Zone", "E-Sports", "Live Gaming",
    "Music Hits", "Classic Hits", "Dance Mix", "Hip Hop", "Pop Stars",
    "Rock Channel", "Jazz Vibes", "Blues Master", "Country Roads", "Latin Beats",
    "World Cinema", "Indian Movies", "Korean Drama", "Chinese Films", "Japanese Anime",
    "European Cinema", "Hollywood Classics", "Bollywood", "Tollywood", "Kollywood",
    "Pakistani TV", "Turkish Drama", "Arabic Channel", "Persian TV", "Hebrew Channel",
    "African Channel", "Brazilian TV", "Mexican Channel", "Spanish Cinema", "Portuguese TV",
    "Russian Channel", "Ukrainian TV", "Polish Channel", "Czech TV", "German Channel",
    "French Cinema", "Italian Movies", "Greek Channel", "Romanian TV", "Bulgarian Channel",
    "Serbian TV", "Croatian Channel", "Hungarian Channel", "Slovak TV", "Slovenian Channel",
    "Norwegian Channel", "Swedish TV", "Danish Channel", "Finnish TV", "Estonian Channel",
    "Latvian Channel", "Lithuanian TV", "Baltic Channel", "Nordic Channel", "Scandinavian",
    "UK Classics", "BBC Alternative", "Sky Movies", "Premium Drama", "Elite Series",
    "Blockbuster Zone", "Indie Films", "Short Films", "Documentary Plus", "Travel Channel",
    "Cooking Zone", "Fitness Plus", "Beauty Channel", "Lifestyle TV", "Home Design",
    "Real Estate TV", "Automotive Zone", "Motor Sports", "Extreme Sports", "Outdoor Zone",
    "Mountain Zone", "Beach Zone", "Desert Zone", "Forest Zone", "Ocean Zone",
    "Night Channel", "Morning Show", "Afternoon Zone", "Evening News", "Late Night",
    "Prime Time", "Golden Hour", "Special Events", "Live Concert", "Awards Show",
    "Game Show", "Talent Show", "Reality TV", "Dating Show", "Competition Zone",
    "Crime Drama", "Legal Drama", "Medical Drama", "Police Zone", "Detective Zone",
    "Mystery Thriller", "Horror Special", "Sci-Fi Zone", "Superhero Zone", "Fantasy Special",
    "Magic Zone", "Paranormal Zone", "UFO Channel", "Conspiracy Zone", "Ancient Mysteries",
    "Space Channel", "Astronomy Zone", "Weather Science", "Climate Zone", "Earth Science",
    "Ocean Science", "Marine Life", "Wildlife Zone", "Animal Planet", "Pet Channel",
    "Bird Zone", "Insect Zone", "Reptile Zone", "Fish Zone", "Plant Zone",
    "Gardening Tips", "Agriculture Zone", "Farm Life", "Cooking Show", "Baking Show",
    "Recipe Zone", "Dining Guide", "Restaurant Zone", "Chef Zone", "Culinary Arts",
    "Wine Tasting", "Beer Zone", "Coffee Talk", "Tea Time", "Beverage Zone",
    "Breakfast Club", "Lunch Break", "Dinner Special", "Snack Zone", "Dessert Zone",
    "Sweet Treats", "Bakery Zone", "Pastry Zone", "Chocolate Zone", "Candy Zone",
    "Health Zone", "Wellness Channel", "Yoga Zone", "Meditation", "Fitness Training",
    "Gym Zone", "Sports Academy", "Coaching Zone", "Training Tips", "Performance Zone",
    "Victory Zone", "Championship", "Tournament Zone", "Match Zone", "Game Day",
    "Stadium Live", "Fan Zone", "League Channel", "Team Zone", "Player Focus",
    "Coach Talk", "Analysis Zone", "Highlights Zone", "Best Moments", "Epic Fails",
    "Reaction Zone", "Comedy Zone", "Funny Videos", "Laugh Zone", "Stand-up Comedy",
    "Comedy Series", "Sketch Zone", "Improv Zone", "Parody Zone", "Satire Channel",
    "Meme Zone", "Viral Videos", "Trending Zone", "Social Media", "Influencer Zone",
    "Celebrity News", "Entertainment News", "Red Carpet", "Awards Coverage", "Fan Exclusive",
    "Behind Scenes", "Making Of", "Documentary Plus", "Biography Zone", "History Special",
    "Historical Drama", "Period Piece", "Classic Literature", "Novel Adaptation", "Story Zone",
    "Audiobook Channel", "Podcast Zone", "Radio Show", "Interview Zone", "Talk Show",
    "Discussion Forum", "Debate Zone", "Political Zone", "News Analysis", "Breaking News",
    "World News", "Local News", "Weather Live", "Traffic Zone", "Alert Zone",
    "Emergency Info", "Safety Zone", "Protection Guide", "Security Tips", "Cyber Zone",
    "Tech News", "Gadget Zone", "Gaming News", "Video Games", "Console Zone",
    "PC Gaming", "Mobile Games", "Online Games", "Esports Pro", "Gaming League",
]

def log_debug(msg):
    """Логирует отладочные сообщения"""
    debug_log.append(msg)
    print(f"[DEBUG] {msg}")

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
# STEP 1: LOAD MEGA PLAYLIST
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
            log_debug(f"MEGA status: {response.status_code}")
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
        log_debug(f"MEGA load error: {e}")
        return mega_items
    finally:
        session.close()


# ============================================================
# STEP 2: GENERATE FULL URL POOL (0-6000)
# ============================================================

def generate_full_url_pool():
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
    print(f"Example 1: {pool[0]['url']}")
    print(f"Example 2: {pool[100]['url']}")
    print(f"Example 3: {pool[1000]['url']}")
    print()
    
    return pool


# ============================================================
# STEP 3: SCAN ALL URLS FOR EXTINF - Stream8
# ============================================================

def scan_stream8_url(item):
    """Проверяет одну ссылку Stream8"""
    session = make_session()
    stream_id = item["id"]
    url = item["url"]
    
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        
        if response.status_code != 200:
            if stream_id < 10:
                log_debug(f"ID={stream_id}: Status {response.status_code}")
            return None
        
        text = response.text
        
        if stream_id < 10:
            log_debug(f"ID={stream_id}: Got response, len={len(text)}")
            if len(text) > 0:
                log_debug(f"ID={stream_id}: First 300 chars: {text[:300]}")
        
        if not text or "#EXTINF:" not in text:
            if stream_id < 10:
                log_debug(f"ID={stream_id}: No #EXTINF found")
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
    
    except Exception as e:
        if stream_id < 10:
            log_debug(f"ID={stream_id}: Exception: {str(e)[:100]}")
        return None
    finally:
        session.close()


def step_3_scan_stream8(full_pool):
    print()
    print("=" * 70)
    print("STEP 3: SCAN ALL 6001 STREAM8 URLs FOR EXTINF")
    print("=" * 70)
    print()
    
    found = []
    total = len(full_pool)
    completed = 0
    
    print(f"[INFO] Scanning {total} Stream8 URLs...")
    print(f"[INFO] Testing first 10 URLs with detailed logging...")
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
            except Exception as e:
                log_debug(f"Future exception: {e}")
            
            if completed % 500 == 0 or completed == total:
                pct = (completed * 100) // total
                print(f"Progress: {completed}/{total} ({pct}%)", flush=True)
    
    found.sort(key=lambda x: int(x["id"]))
    print()
    print(f"[SUCCESS] Stream8 live with EXTINF: {len(found)}")
    print()
    
    return found


# ============================================================
# CREATE TEST M3U WITH 300 DUMMY CHANNELS
# (Both Stream0 and Stream1)
# ============================================================

def create_test_m3u():
    """Создаёт тестовый M3U с 300 выдуманными каналами для обоих зеркал"""
    print()
    print("=" * 70)
    print(f"CREATING TEST M3U WITH {TEST_CHANNELS} DUMMY CHANNELS (BOTH MIRRORS)")
    print("=" * 70)
    print()
    
    with open(TEST_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# TEST PLAYLIST - {TEST_CHANNELS} Dummy Channels\n")
        f.write(f"# Both Stream0 and Stream1 mirrors\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Verified by {COPYRIGHT}\n")
        f.write("\n")
        
        channel_number = 1
        
        # Stream0 - первые 150 каналов
        for i in range(150):
            stream_id = i
            tvg_id = f"test_stream0_{stream_id}"
            name = TEST_CHANNEL_NAMES[i % len(TEST_CHANNEL_NAMES)]
            
            extinf = (
                f'#EXTINF:-1 tvg-chno="{channel_number}" '
                f'tvg-id="{tvg_id}" '
                f'group-title="Stream0 Test",'
                f'{name} (Stream0)'
            )
            
            url = build_url(STREAM0_BASE, stream_id)
            
            f.write(extinf + "\n")
            f.write(url + "\n")
            channel_number += 1
        
        # Stream1 - вторые 150 каналов
        for i in range(150, TEST_CHANNELS):
            stream_id = i
            tvg_id = f"test_stream1_{stream_id}"
            name = TEST_CHANNEL_NAMES[i % len(TEST_CHANNEL_NAMES)]
            
            extinf = (
                f'#EXTINF:-1 tvg-chno="{channel_number}" '
                f'tvg-id="{tvg_id}" '
                f'group-title="Stream1 Test",'
                f'{name} (Stream1)'
            )
            
            url = build_url(STREAM1_BASE, stream_id)
            
            f.write(extinf + "\n")
            f.write(url + "\n")
            channel_number += 1
    
    print(f"[SUCCESS] Test M3U created: {TEST_M3U}")
    print(f"[INFO] Stream0 dummy channels: 150")
    print(f"[INFO] Stream1 dummy channels: 150")
    print(f"[INFO] Total dummy channels: {TEST_CHANNELS}")
    print()


# ============================================================
# STEP 4: CHECK STREAM0
# ============================================================

def check_mirror_url(base_url, stream_id, channel_name, group_name):
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
    print()
    print("=" * 70)
    print("STEP 4: CHECK STREAM0 MIRRORS")
    print("=" * 70)
    print()
    
    if not stream8_items:
        print("[SKIP] No Stream8 items to check")
        return []
    
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
# STEP 5: CHECK STREAM1
# ============================================================

def step_5_check_stream1(stream8_items):
    print()
    print("=" * 70)
    print("STEP 5: CHECK STREAM1 MIRRORS")
    print("=" * 70)
    print()
    
    if not stream8_items:
        print("[SKIP] No Stream8 items to check")
        return []
    
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
# WRITE DEBUG LOG
# ============================================================

def write_debug_log():
    with open(DEBUG_LOG, "w", encoding="utf-8") as f:
        f.write("DEBUG LOG - CINERAMA SCANNER\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Scanned: {END_ID - START_ID + 1} URLs (0-{END_ID})\n")
        f.write("\n")
        for line in debug_log:
            f.write(line + "\n")
    print(f"[INFO] Debug log written: {DEBUG_LOG}")


# ============================================================
# WRITE M3U
# ============================================================

def make_extinf_line(item, channel_number):
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
    with open(POOL_ANALYSIS_TXT, "w", encoding="utf-8") as f:
        f.write("CINERAMA POOL ANALYSIS\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n")
        f.write(f"MEGA: {len(mega_items)} IDs\n")
        f.write(f"Full pool: {full_pool_count} URLs generated (0-{END_ID})\n")
        f.write(f"Test M3U: {TEST_CHANNELS} dummy channels (150 Stream0 + 150 Stream1)\n")
        f.write("\n")


def write_report(mega_items, stream8_items, stream0_items, stream1_items):
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
        f.write(f"TEST M3U: {TEST_CHANNELS} dummy channels\n")
        f.write("\n")
        
        if stream8_items:
            f.write("STREAM8 CHANNELS (first 50)\n")
            f.write("-" * 70 + "\n")
            for item in stream8_items[:50]:
                f.write(f"ID={item['id']} | {item['name']}\n")
            if len(stream8_items) > 50:
                f.write(f"... and {len(stream8_items) - 50} more\n")
            f.write("\n")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CINERAMA SCANNER - FULL 6000 SCAN + TEST M3U (300 DUMMY)")
    print("=" * 70)
    
    # Создаём тестовый M3U первым
    create_test_m3u()
    
    # Реальное сканирование
    mega_items = load_mega_playlist()
    full_pool = generate_full_url_pool()
    stream8_items = step_3_scan_stream8(full_pool)
    
    if stream8_items:
        stream0_items = step_4_check_stream0(stream8_items)
        stream1_items = step_5_check_stream1(stream8_items)
    else:
        print("[INFO] No Stream8 channels found, but test M3U created")
        stream0_items = []
        stream1_items = []
    
    write_pool_analysis(mega_items, len(full_pool))
    write_m3u(stream8_items, stream0_items, stream1_items)
    write_report(mega_items, stream8_items, stream0_items, stream1_items)
    write_debug_log()
    
    print()
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print()
    print(f"Stream8: {len(stream8_items)}")
    print(f"Stream0: {len(stream0_items)}")
    print(f"Stream1: {len(stream1_items)}")
    print()
    print("FILES GENERATED:")
    print(f"  {OUTPUT_M3U} - Real scan results (Stream8/Stream0/Stream1)")
    print(f"  {TEST_M3U} - Test playlist with 300 dummy channels (150 Stream0 + 150 Stream1)")
    print(f"  {REPORT_TXT} - Final report with statistics")
    print(f"  {POOL_ANALYSIS_TXT} - Pool analysis")
    print(f"  {DEBUG_LOG} - Debug log with first 10 URLs details")
    print()


if __name__ == "__main__":
    main()