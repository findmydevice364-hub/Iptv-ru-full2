import re
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse, unquote

# ============================================================
# CONFIG
# ============================================================

POOL_M3U_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/pool_tv.m3u"
OUTPUT_M3U = "pull_correct.m3u"
REPORT_TXT = "pull_correct_report.txt"

MAX_THREADS = 50
TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

def parse_m3u(text: str) -> OrderedDict:
    channels = OrderedDict()
    current_extinf = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue

        if current_extinf and line.startswith("http"):
            url = line
            name = current_extinf.split(",", 1)[-1].strip() if "," in current_extinf else ""
            channels[url] = {
                "url": url,
                "name": name,
                "name_source": "pool",
                "extinf_original": current_extinf,
                "extinf_live": None,
                "is_live": False
            }
            current_extinf = None

    return channels


# ============================================================
# CLEAN + EXTRACT
# ============================================================

def clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip().strip('"').strip("'")
    if not name or len(name) < 2:
        return None
    if name.lower() in {
        "index", "playlist", "master", "live", "stream", "channel",
        "unknown", "null", "none", "video", "audio"
    }:
        return None
    name = re.sub(r"\.(m3u8?|ts)$", "", name, flags=re.IGNORECASE)
    return name.strip() or None


def extract_from_extinf(line: str) -> str | None:
    if not line.startswith("#EXTINF:") or "," not in line:
        return None
    return clean_name(line.split(",", 1)[1])


def extract_name_attr(line: str) -> str | None:
    m = re.search(r'NAME\s*=\s*"([^"]+)"', line, re.IGNORECASE)
    if m:
        return clean_name(m.group(1))
    m = re.search(r'NAME\s*=\s*([^,\s]+)', line, re.IGNORECASE)
    if m:
        return clean_name(m.group(1))
    return None


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ — ПРОВЕРКА + ТЯНЕМ ДАННЫЕ ИЗ ПОТОКА
# ============================================================

CANDIDATES = [
    "index.m3u8",
    "playlist.m3u8",
    "master.m3u8",
    "live.m3u8",
    "stream.m3u8",
    "channel.m3u8",
    "tracks-v1a1/index.m3u8",
    "tracks-v1a1/playlist.m3u8",
    "tracks-v1a2/index.m3u8",
    "tracks-v1a2/playlist.m3u8",
]

def probe_stream(url: str) -> tuple[bool, str | None, str | None, str | None]:
    """
    Возвращает:
        is_live: bool
        extinf: str | None
        name: str | None
        source: str | None   ("extinf" / "media" / "stream-inf" / None)
    """
    base = url.rsplit("/", 1)[0]
    session = make_session()
    seen = set()

    for pl in CANDIDATES:
        test_url = f"{base}/{pl}"
        if test_url in seen:
            continue
        seen.add(test_url)

        try:
            r = session.get(test_url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            text = r.text.replace("\r", "")
            if not text:
                continue

            # Признаки, что это реально HLS
            is_hls = (
                "#EXTM3U" in text or
                "#EXT-X-" in text or
                ".ts" in text or
                "EXTINF" in text
            )
            if not is_hls:
                continue

            # Поток живой. Теперь пытаемся вытащить имя.
            best_extinf = None
            best_name = None
            best_source = None

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue

                # 1. #EXTINF:...,Имя
                if line.startswith("#EXTINF:"):
                    name = extract_from_extinf(line)
                    if name:
                        return True, line, name, "extinf"
                    if not best_extinf:
                        best_extinf = line

                # 2. #EXT-X-MEDIA:NAME=...
                elif line.startswith("#EXT-X-MEDIA:"):
                    name = extract_name_attr(line)
                    if name and not best_name:
                        best_name = name
                        best_source = "media"
                        best_extinf = f'#EXTINF:-1 tvg-name="{name}",{name}'

                # 3. #EXT-X-STREAM-INF:NAME=...
                elif line.startswith("#EXT-X-STREAM-INF:"):
                    name = extract_name_attr(line)
                    if name and not best_name:
                        best_name = name
                        best_source = "stream-inf"
                        best_extinf = f'#EXTINF:-1 tvg-name="{name}",{name}'

            # Поток живой, имя может быть или не быть
            return True, best_extinf, best_name, best_source

        except Exception:
            continue

    session.close()
    return False, None, None, None


# ============================================================
# GENERATE EXTINF
# ============================================================

def generate_extinf(name: str, idx: int) -> str:
    safe = name.replace('"', "'")
    return (
        f'#EXTINF:-1 tvg-id="{safe}" tvg-name="{safe}" '
        f'tvg-logo="" group-title="Undefined" tvg-chno="{idx}",{safe}'
    )


# ============================================================
# VERIFY
# ============================================================

def verify_all_streams(channels: OrderedDict) -> OrderedDict:
    total = len(channels)
    completed = 0

    print(f"\nVERIFYING {total} STREAMS\n")

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(probe_stream, url): url
            for url in channels
        }

        for future in as_completed(futures):
            completed += 1
            url = futures[future]
            ch = channels[url]

            try:
                is_live, extinf, name_from_stream, source = future.result()

                if is_live:
                    ch["is_live"] = True
                    ch["extinf_live"] = extinf

                    # Имя: сначала из потока, иначе оставляем из пула
                    if name_from_stream:
                        ch["name"] = name_from_stream
                        ch["name_source"] = source
                        print(f"[LIVE ✓] {ch['name']}  ← {source}")
                    else:
                        # Имя из потока не нашли — оставляем оригинальное
                        ch["name_source"] = "pool"
                        print(f"[LIVE ✓] {ch['name']}  ← pool (в потоке имени нет)")

                else:
                    ch["is_live"] = False
                    print(f"[DEAD ✗] {url}")

            except Exception as e:
                ch["is_live"] = False
                print(f"[ERROR] {url} → {e}")

            if completed % 50 == 0 or completed == total:
                live = sum(1 for c in channels.values() if c["is_live"])
                pct = (completed * 100) // total
                print(f"Progress {completed}/{total} ({pct}%) | Live {live}")

    return channels


# ============================================================
# WRITE FINAL M3U
# ============================================================

def write_final_m3u(channels: OrderedDict):
    live_channels = OrderedDict(
        (url, ch) for url, ch in channels.items() if ch["is_live"]
    )

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write("# Verified Playlist\n")
        f.write(f"# Generated: {datetime.now()}\n")
        f.write(f"# Live channels: {len(live_channels)}\n\n")

        for idx, (url, ch) in enumerate(live_channels.items(), 1):
            # Всегда пишем чистое имя (из потока или из пула)
            f.write(generate_extinf(ch["name"], idx) + "\n")
            f.write(url + "\n")

    print(f"\n[OK] Playlist saved: {OUTPUT_M3U}")
    print(f"[OK] Total live channels: {len(live_channels)}\n")


# ============================================================
# REPORT
# ============================================================

def write_report(channels: OrderedDict):
    total = len(channels)
    live = sum(1 for c in channels.values() if c["is_live"])
    success_rate = (live / total * 100) if total else 0

    sources = {}
    for ch in channels.values():
        if ch["is_live"]:
            src = ch.get("name_source") or "unknown"
            sources[src] = sources.get(src, 0) + 1

    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("PLAYLIST VERIFICATION REPORT\n")
        f.write("=" * 70 + "\n")
        f.write(f"Date: {datetime.now()}\n")
        f.write(f"Total channels in pool: {total}\n")
        f.write(f"Live channels: {live}\n")
        f.write(f"Dead channels: {total - live}\n")
        f.write(f"Success rate: {success_rate:.1f}%\n\n")
        f.write("Name sources (live only):\n")
        for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            f.write(f"  {src:12} : {cnt}\n")
        f.write("=" * 70 + "\n")

    print(f"[OK] Report saved: {REPORT_TXT}")
    print("\nName sources:")
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {src:12} : {cnt}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 70)
    print("PLAYLIST VERIFICATION")
    print("Живой = ответил HLS | Имя = максимально из потока")
    print("=" * 70)

    session = make_session()
    r = session.get(POOL_M3U_URL, timeout=15)
    session.close()

    channels = parse_m3u(r.text)
    print(f"[OK] Loaded {len(channels)} channels\n")

    channels = verify_all_streams(channels)
    write_final_m3u(channels)
    write_report(channels)

    live = sum(1 for c in channels.values() if c["is_live"])
    print("\n" + "=" * 70)
    print("DONE")
    print(f"Total: {len(channels)}")
    print(f"Live:  {live}")
    print(f"Dead:  {len(channels) - live}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()