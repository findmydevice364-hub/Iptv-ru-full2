#!/usr/bin/env python3
"""
Cinerama MULTI-HLS stream probe
TD = главный источник реального названия канала.
YAML origin_detection = вторичный источник.
Один TXT, полная картина маслом.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml


# ============================
#  LOAD M3U CHANNEL LIST
# ============================

def load_channels_from_m3u(path: str) -> List[Dict[str, str]]:
    channels = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    name = None
    for line in lines:
        if line.startswith("#EXTINF"):
            name = line.split(",", 1)[1].strip()
        elif line.startswith("http"):
            channels.append({"name": name, "url": line.strip()})
    return channels


# ============================
#  TD-BASED REAL NAME DETECTOR
# ============================

def detect_real_name_td(td: Optional[int]) -> str:
    if td == 2:
        return "НТВ"
    if td == 3:
        return "ТВ3 International"
    if td == 4:
        return "ТВ3 Standard"
    if td == 6:
        return "ТВЦ"
    if td == 8:
        return "Россия 24"
    if td == 10:
        return "Россия 1"
    return "Unknown"


# ============================
#  YAML ORIGIN DETECTOR (SECONDARY)
# ============================

def detect_primary_source_yaml(
    target_duration: Optional[int],
    variant: Optional[str],
    avg_size: Optional[float],
    sources: List[Dict[str, Any]],
) -> str:
    if not sources:
        return "Unknown"

    for rule in sources:
        rule_name = str(rule.get("name", "Unknown"))
        rule_variant = rule.get("variant")
        rule_td = rule.get("target_duration")

        if rule_variant is not None and variant is not None and rule_variant != variant:
            continue

        if rule_td is not None and target_duration is not None and target_duration != rule_td:
            continue

        if rule_variant == "International":
            max_size = rule.get("max_segment_size", 200_000)
            if avg_size is not None:
                if avg_size < float(max_size):
                    return rule_name
            else:
                return rule_name

        if rule_td is not None and target_duration is not None and target_duration == rule_td:
            return rule_name

    return "Unknown"


# ============================
#  SINGLE STREAM ANALYSIS
# ============================

def analyze_stream(url: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    req_cfg = cfg.get("requests", {})
    timeout = int(req_cfg.get("timeout", 5))
    retry_count = max(0, int(req_cfg.get("retry_count", 1)))

    result: Dict[str, Any] = {
        "url": url,
        "cdn_host": None,
        "cdn_path": None,
        "variant": None,
        "quality": None,
        "target_duration": None,
        "segment_count": 0,
        "avg_segment_size": None,
        "first_segment": None,
        "primary_source_yaml": None,
        "primary_source_td": None,
        "master_playlist": False,
        "segment_fetch_errors": 0,
    }

    # Download playlist
    text: Optional[str] = None
    for attempt in range(retry_count + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            text = resp.text
            break
        except Exception:
            pass

    if text is None:
        return result

    # CDN
    parsed = urlparse(url)
    result["cdn_host"] = parsed.netloc
    result["cdn_path"] = parsed.path

    # Variant / quality
    if "mono.m3u8" in url:
        result["variant"] = "International"
        result["quality"] = "SD"
    elif "tracks-v3a1" in url:
        result["variant"] = "Standard"
        result["quality"] = "HD"
    elif "tracks-v1a1" in url:
        result["variant"] = "Standard"
        result["quality"] = "SD"
    else:
        result["variant"] = "Unknown"

    # Master playlist?
    if "#EXT-X-STREAM-INF" in text:
        result["master_playlist"] = True

    # TD
    m = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
    if m:
        result["target_duration"] = int(m.group(1))

    # Segments
    segments = re.findall(r"(https?://[^\s]+\.ts)", text)
    result["segment_count"] = len(segments)

    if segments:
        result["first_segment"] = segments[0]

        sizes = []
        for s in segments[:5]:
            try:
                rs = requests.get(s, timeout=timeout)
                rs.raise_for_status()
                sizes.append(len(rs.content))
            except Exception:
                result["segment_fetch_errors"] += 1

        if sizes:
            result["avg_segment_size"] = sum(sizes) / len(sizes)

    # YAML origin (secondary)
    origin_cfg = cfg.get("origin_detection", {})
    if origin_cfg.get("enabled", True):
        sources = origin_cfg.get("sources", [])
        result["primary_source_yaml"] = detect_primary_source_yaml(
            result["target_duration"],
            result["variant"],
            result["avg_segment_size"],
            sources,
        )

    # TD origin (PRIMARY)
    result["primary_source_td"] = detect_real_name_td(result["target_duration"])

    return result


# ============================
#  SAVE ONE BIG TXT
# ============================

def save_full_report(channels: List[Dict[str, str]], results: List[Dict[str, Any]]):
    with open("stream_full_report.txt", "w", encoding="utf-8") as f:
        f.write("=== FULL STREAM ANALYSIS REPORT ===\n")
        f.write(f"Generated at: {datetime.now()}\n\n")

        for ch, info in zip(channels, results):
            real_name = info["primary_source_td"]  # TD = главный
            yaml_name = info["primary_source_yaml"]

            f.write("============================================================\n")
            f.write(f"CHANNEL: {real_name} ({ch['name']})\n")
            f.write(f"URL: {info['url']}\n\n")

            f.write(f"CDN_HOST: {info['cdn_host']}\n")
            f.write(f"CDN_PATH: {info['cdn_path']}\n\n")

            f.write(f"VARIANT: {info['variant']}\n")
            f.write(f"QUALITY: {info['quality']}\n\n")

            f.write(f"TARGET_DURATION: {info['target_duration']}\n")
            f.write(f"SEGMENT_COUNT: {info['segment_count']}\n")
            f.write(f"FIRST_SEGMENT: {info['first_segment']}\n")
            f.write(f"AVG_SEG_SIZE: {info['avg_segment_size']}\n\n")

            f.write(f"PRIMARY_SOURCE_TD: {info['primary_source_td']}\n")
            f.write(f"PRIMARY_SOURCE_YAML: {yaml_name}\n\n")

            f.write(f"MASTER_PLAYLIST: {info['master_playlist']}\n")
            f.write(f"SEGMENTS_PRESENT: {info['segment_count'] > 0}\n")
            f.write(f"SEGMENT_FETCH_ERRORS: {info['segment_fetch_errors']}\n\n")

            f.write("COMMENTS:\n")
            if info["segment_count"] == 0:
                f.write("- Поток живой, но сегментов нет → пустой плейлист\n")
            if info["target_duration"]:
                f.write(f"- TD={info['target_duration']} → {info['primary_source_td']}\n")
            if yaml_name != info["primary_source_td"]:
                f.write(f"- YAML origin ({yaml_name}) отличается от TD → TD главный\n")

            f.write("============================================================\n\n")


# ============================
#  MAIN
# ============================

def main():
    parser = argparse.ArgumentParser(description="Multi Cinerama stream probe")
    parser.add_argument("-m", "--m3u", default="channels.m3u")
    parser.add_argument("-c", "--config", default="stream_probe.yml")
    args = parser.parse_args()

    # Load YAML config
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["stream_probe"]

    # Load channels
    channels = load_channels_from_m3u(args.m3u)

    # Analyze all
    results = [analyze_stream(ch["url"], cfg) for ch in channels]

    # Save one big TXT
    save_full_report(channels, results)


if __name__ == "__main__":
    main()