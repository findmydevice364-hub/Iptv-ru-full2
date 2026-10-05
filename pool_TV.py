import re
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

POOL_M3U_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/pool_tv.m3u"
OUTPUT_M3U = "pull_correct.m3u"
REPORT_TXT = "pull_correct_report.txt"

MAX_THREADS = 50
TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# ============================================================
# SESSION
# ============================================================

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ============================================================
# PARSE M3U POOL
# ============================================================

def parse_m3u(text):
    channels = OrderedDict()
    current_extinf = None

    for line in text.splitlines():
        line = line.strip()

        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue

        if current_extinf and line.startswith("http"):
            url = line
            name = current_extinf.split(",", 1)[-1].strip()

            channels[url] = {
                "url": url,
                "name": name,
                "extinf_original": current_extinf,
                "extinf_live": None,
                "is_live": False
            }
            current_extinf = None

    return channels


# ============================================================
# TRY TO LOAD FULL EXTINF FROM STREAM
# ============================================================

POSSIBLE_PLAYLISTS = [
    "index.m3u8",
    "playlist.m3u8",
    "tracks-v1a1/playlist.m3u8",
    "tracks-v1a1/index.m3u8"
]

def try_load_parent_extinf(url):
    base = url.rsplit("/", 1)[0]
    session = make_session()

    for pl in POSSIBLE_PLAYLISTS:
        test_url = f"{base}/{pl}"

        try:
            r = session.get(test_url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            text = r.text.replace("\r", "")

            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    return line

        except:
            continue

    session.close()
    return None


# ============================================================
# GENERATE EXTINF IF MISSING
# ============================================================

def generate_extinf(name, idx):
    return (
        f'#EXTINF:-1 tvg-id="{name}" tvg-name="{name}" '
        f'tvg-logo="" group-title="Undefined" tvg-chno="{idx}", {name}'
    )


# ============================================================
# VERIFY STREAMS
# ============================================================

def verify_all_streams(channels):
    total = len(channels)
    completed = 0

    print(f"\nVERIFYING {total} STREAMS\n")

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(try_load_parent_extinf, url): url
            for url in channels
        }

        for future in as_completed(futures):
            completed += 1
            url = futures[future]
            ch = channels[url]

            try:
                extinf = future.result()

                if extinf:
                    ch["extinf_live"] = extinf
                    ch["is_live"] = True
                    name = extinf.split(",", 1)[-1].strip()
                    print(f"[LIVE ✓] {name}")

                else:
                    ch["is_live"] = False
                    print(f"[DEAD ✗] {url}")

            except:
                ch["is_live"] = False
                print(f"[ERROR] {url}")

            if completed % 50 == 0 or completed == total:
                live = sum(1 for c in channels.values() if c["is_live"])
                pct = (completed * 100) // total
                print(f"Progress {completed}/{total} ({pct}%) | Live {live}")

    return channels


# ============================================================
# WRITE FINAL M3U
# ============================================================

def write_final_m3u(channels):
    live_channels = OrderedDict(
        (url, ch) for url, ch in channels.items() if ch["is_live"]
    )

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Verified Playlist\n")
        f.write(f"# Generated: {datetime.now()}\n")
        f.write(f"# Live channels: {len(live_channels)}\n\n")

        for idx, (url, ch) in enumerate(live_channels.items(), 1):
            extinf = ch["extinf_live"]

            if not extinf:
                extinf = generate_extinf(ch["name"], idx)

            if 'tvg-chno=' not in extinf:
                extinf = extinf.replace("#EXTINF:-1", f'#EXTINF:-1 tvg-chno="{idx}"')

            f.write(extinf + "\n")
            f.write(url + "\n")

    print(f"[OK] Playlist saved: {OUTPUT_M3U}")
    print(f"[OK] Total channels: {len(live_channels)}\n")


# ============================================================
# WRITE REPORT
# ============================================================

def write_report(total, live):
    success_rate = (live / total * 100) if total > 0 else 0

    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("PLAYLIST VERIFICATION REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now()}\n")
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

    session = make_session()
    r = session.get(POOL_M3U_URL, timeout=15)
    session.close()

    channels = parse_m3u(r.text)
    print(f"[OK] Loaded {len(channels)} channels\n")

    channels = verify_all_streams(channels)

    live_channels = sum(1 for ch in channels.values() if ch["is_live"])

    write_final_m3u(channels)
    write_report(len(channels), live_channels)

    print("=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    print(f"Total: {len(channels)}")
    print(f"Live: {live_channels}")
    print(f"Dead: {len(channels) - live_channels}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()