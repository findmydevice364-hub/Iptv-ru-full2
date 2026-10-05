#!/usr/bin/env python3
"""
Cinerama / HLS stream probe.
Reads configuration from YAML and analyses an m3u8 playlist.
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


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg or "stream_probe" not in cfg:
        raise ValueError("Config must contain top-level key 'stream_probe'")
    return cfg["stream_probe"]


def log(msg: str, logfile: Optional[str], enabled: bool) -> None:
    if not enabled:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, file=sys.stderr)
    if logfile:
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def detect_primary_source(
    target_duration: Optional[int],
    variant: Optional[str],
    avg_size: Optional[float],
    sources: List[Dict[str, Any]],
) -> str:
    """Match against the ordered list of origin rules from YAML."""
    for rule in sources:
        name = rule.get("name", "Unknown")

        # International + size check
        if rule.get("variant") == "International":
            if variant == "International" or (
                avg_size is not None and avg_size < rule.get("max_segment_size", 200_000)
            ):
                if target_duration == rule.get("target_duration"):
                    return name
            continue

        # Standard channels matched by target_duration only
        if target_duration is not None and target_duration == rule.get("target_duration"):
            return name

    return "Неизвестный первоисточник"


def analyze_stream(url: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    log_cfg = cfg.get("log", {})
    log_enabled = log_cfg.get("enabled", True)
    logfile = log_cfg.get("file") if log_enabled else None

    req_cfg = cfg.get("requests", {})
    timeout = req_cfg.get("timeout", 5)
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
        "primary_source": None,
        "first_segment": None,
    }

    log("=== START PROBE ===", logfile, log_enabled)
    log(f"STREAM_URL={url}", logfile, log_enabled)

    # 1. Download playlist
    text = None
    last_err = None
    for attempt in range(retry_count + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            text = resp.text
            log("Playlist downloaded OK", logfile, log_enabled)
            break
        except Exception as e:
            last_err = e
            log(f"ERROR downloading playlist (attempt {attempt + 1}): {e}", logfile, log_enabled)

    if text is None:
        log(f"Failed to download playlist after retries: {last_err}", logfile, log_enabled)
        log("=== END PROBE ===\n", logfile, log_enabled)
        return result

    # 2. CDN
    cdn_cfg = cfg.get("cdn_analysis", {})
    if cdn_cfg.get("enabled", True):
        parsed = urlparse(url)
        if cdn_cfg.get("detect_host", True):
            result["cdn_host"] = parsed.netloc
            log(f"CDN_HOST={result['cdn_host']}", logfile, log_enabled)
        if cdn_cfg.get("detect_path", True):
            result["cdn_path"] = parsed.path
            log(f"CDN_PATH={result['cdn_path']}", logfile, log_enabled)

    # 3. Variant / quality
    pl_cfg = cfg.get("playlist_analysis", {})
    if pl_cfg.get("enabled", True):
        if pl_cfg.get("detect_variant", True):
            if "mono.m3u8" in url:
                result["variant"] = "International"
            elif "tracks-v3a1" in url:
                result["variant"] = "Standard"
            elif "tracks-v1a1" in url:
                result["variant"] = "Standard"
            else:
                result["variant"] = "Unknown"
            log(f"VARIANT={result['variant']}", logfile, log_enabled)

        if pl_cfg.get("detect_quality", True):
            if "tracks-v3a1" in url:
                result["quality"] = "HD"
            elif "tracks-v1a1" in url:
                result["quality"] = "SD"
            else:
                result["quality"] = None
            log(f"QUALITY={result['quality']}", logfile, log_enabled)

        # 4. Target Duration
        if pl_cfg.get("detect_target_duration", True):
            m = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
            if m:
                result["target_duration"] = int(m.group(1))
                log(f"TARGET_DURATION={result['target_duration']}", logfile, log_enabled)

        # 5. Segments
        if pl_cfg.get("detect_segments", True):
            segments = re.findall(r"(https?://[^\s]+\.ts)", text)
            result["segment_count"] = len(segments)
            log(f"SEGMENT_COUNT={result['segment_count']}", logfile, log_enabled)

            if segments:
                result["first_segment"] = segments[0]
                log(f"FIRST_SEGMENT={result['first_segment']}", logfile, log_enabled)

            # 6. Average segment size
            if pl_cfg.get("detect_segment_size", True) and segments:
                sample_size = min(
                    int(pl_cfg.get("segment_sample_size", 5)),
                    len(segments),
                )
                sizes: List[int] = []
                for s in segments[:sample_size]:
                    try:
                        rs = requests.get(s, timeout=timeout)
                        sizes.append(len(rs.content))
                    except Exception as e:
                        log(f"Failed to fetch segment size {s}: {e}", logfile, log_enabled)

                if sizes:
                    avg = sum(sizes) / len(sizes)
                    result["avg_segment_size"] = avg
                    log(f"AVG_SEGMENT_SIZE={avg}", logfile, log_enabled)

    # 7. Primary source
    origin_cfg = cfg.get("origin_detection", {})
    if origin_cfg.get("enabled", True):
        sources = origin_cfg.get("sources", [])
        result["primary_source"] = detect_primary_source(
            result["target_duration"],
            result["variant"],
            result["avg_segment_size"],
            sources,
        )
        log(f"PRIMARY_SOURCE={result['primary_source']}", logfile, log_enabled)

    log("=== END PROBE ===\n", logfile, log_enabled)
    return result


def print_summary(info: Dict[str, Any], out_cfg: Dict[str, Any]) -> None:
    if not out_cfg.get("print_summary", True):
        return

    print("=== STREAM ANALYSIS RESULT ===")
    mapping = [
        ("include_url", "URL", "url"),
        ("include_cdn_host", "CDN_HOST", "cdn_host"),
        ("include_cdn_path", "CDN_PATH", "cdn_path"),
        ("include_variant", "VARIANT", "variant"),
        ("include_quality", "QUALITY", "quality"),
        ("include_target_duration", "TARGET_DURATION", "target_duration"),
        ("include_segment_count", "SEGMENT_COUNT", "segment_count"),
        ("include_avg_segment_size", "AVG_SEG_SIZE", "avg_segment_size"),
        ("include_first_segment", "FIRST_SEGMENT", "first_segment"),
        ("include_primary_source", "PRIMARY_SOURCE", "primary_source"),
    ]
    for flag, label, key in mapping:
        if out_cfg.get(flag, True):
            print(f"{label + ':':<18} {info.get(key)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HLS / Cinerama stream probe")
    parser.add_argument(
        "-c", "--config",
        default="stream_probe.yml",
        help="Path to YAML config (default: stream_probe.yml)",
    )
    parser.add_argument(
        "-u", "--url",
        help="Override stream URL from config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    url = args.url or cfg.get("url")
    if not url:
        print("Error: no stream URL provided (neither in config nor via --url)", file=sys.stderr)
        sys.exit(1)

    info = analyze_stream(url, cfg)
    print_summary(info, cfg.get("output", {}))


if __name__ == "__main__":
    main()