import requests
import re
from urllib.parse import urlparse
from datetime import datetime

LOGFILE = "cinerama_probe_log.txt"


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")


def detect_primary_source(target_duration, variant, avg_size):
    # International по хвосту/размеру
    if variant == "International" or (avg_size and avg_size < 200000):
        if target_duration == 3:
            return "ТВ3 International"
        return "International версия неизвестного канала"

    # Standard по TD
    if target_duration == 4:
        return "ТВ3 (Standard)"
    if target_duration == 6:
        return "ТВЦ"
    if target_duration == 2:
        return "НТВ"
    if target_duration == 10:
        return "Россия 1"
    if target_duration == 8:
        return "Россия 24"

    return "Неизвестный первоисточник"


def analyze_stream(url):
    log("=== START PROBE ===")
    log(f"STREAM_URL={url}")

    result = {
        "url": url,
        "cdn_host": None,
        "cdn_path": None,
        "variant": None,
        "quality": None,
        "target_duration": None,
        "segment_count": 0,
        "avg_segment_size": None,
        "primary_source": None,
        "first_segment": None
    }

    # 1. Плейлист
    try:
        text = requests.get(url, timeout=5).text
        log("Playlist downloaded OK")
    except Exception as e:
        log(f"ERROR downloading playlist: {e}")
        return result

    # 2. CDN
    parsed = urlparse(url)
    result["cdn_host"] = parsed.netloc
    result["cdn_path"] = parsed.path
    log(f"CDN_HOST={result['cdn_host']}")
    log(f"CDN_PATH={result['cdn_path']}")

    # 3. Variant / quality
    if "mono.m3u8" in url:
        result["variant"] = "International"
    elif "tracks-v3a1" in url:
        result["variant"] = "Standard"
        result["quality"] = "HD"
    elif "tracks-v1a1" in url:
        result["variant"] = "Standard"
        result["quality"] = "SD"
    else:
        result["variant"] = "Unknown"

    log(f"VARIANT={result['variant']}")
    log(f"QUALITY={result['quality']}")

    # 4. Target Duration
    m = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
    if m:
        td = int(m.group(1))
        result["target_duration"] = td
        log(f"TARGET_DURATION={td}")

    # 5. Сегменты
    segments = re.findall(r"(https?://[^\s]+\.ts)", text)
    result["segment_count"] = len(segments)
    log(f"SEGMENT_COUNT={result['segment_count']}")

    if segments:
        result["first_segment"] = segments[0]
        log(f"FIRST_SEGMENT={result['first_segment']}")

    # 6. Средний размер сегмента
    sizes = []
    for s in segments[:5]:
        try:
            rs = requests.get(s, timeout=5)
            sizes.append(len(rs.content))
        except:
            pass

    if sizes:
        avg = sum(sizes) / len(sizes)
        result["avg_segment_size"] = avg
        log(f"AVG_SEGMENT_SIZE={avg}")

    # 7. Первoисточник
    result["primary_source"] = detect_primary_source(
        result["target_duration"],
        result["variant"],
        result["avg_segment_size"]
    )
    log(f"PRIMARY_SOURCE={result['primary_source']}")

    log("=== END PROBE ===\n")
    return result


# Пример: ТВ3 International (CINERAMA Channel 1446)
if __name__ == "__main__":
    url = "https://stream1.cinerama.uz/1446/tracks-v1a1/mono.m3u8"
    info = analyze_stream(url)

    print("=== STREAM ANALYSIS RESULT ===")
    print(f"URL:             {info['url']}")
    print(f"CDN_HOST:        {info['cdn_host']}")
    print(f"CDN_PATH:        {info['cdn_path']}")
    print(f"VARIANT:         {info['variant']}")
    print(f"QUALITY:         {info['quality']}")
    print(f"TARGET_DURATION: {info['target_duration']}")
    print(f"SEGMENT_COUNT:   {info['segment_count']}")
    print(f"AVG_SEG_SIZE:    {info['avg_segment_size']}")
    print(f"FIRST_SEGMENT:   {info['first_segment']}")
    print(f"PRIMARY_SOURCE:  {info['primary_source']}")