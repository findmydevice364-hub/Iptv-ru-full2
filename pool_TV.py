import re
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

TIMEOUT = 10
MAX_THREADS = 50

# ============================================================
# SESSION
# ============================================================

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ============================================================
# PARSE POOL
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

    with open("pull_correct.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Generated: {datetime.now()}\n\n")

        for idx, (url, ch) in enumerate(live_channels.items(), 1):
            extinf = ch["extinf_live"]

            if not extinf:
                extinf = generate_extinf(ch["name"], idx)

            if 'tvg-chno=' not in extinf:
                extinf = extinf.replace("#EXTINF:-1", f'#EXTINF:-1 tvg-chno="{idx}"')

            f.write(extinf + "\n")
            f.write(url + "\n")

    print(f"[OK] Saved pull_correct.m3u ({len(live_channels)} channels)")


# ============================================================
# MAIN
# ============================================================

def main():
    print("LOADING POOL...")

    session = make_session()
    r = session.get("https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/playlist.m3u")
    session.close()

    channels = parse_m3u(r.text)
    print(f"Loaded {len(channels)} channels")

    channels = verify_all_streams(channels)

    live = sum(1 for ch in channels.values() if ch["is_live"])
    print(f"Live: {live}")

    write_final_m3u(channels)


if __name__ == "__main__":
    main()