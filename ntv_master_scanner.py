#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTV CDN/HLS auditor.

Outputs are written to Ntv/output/. This scanner tests explicitly configured
CDN nodes and candidate URL patterns; it does not bypass access controls.
Orbital URL templates are configurable because offset-to-path mapping must be
confirmed from a real NTV master playlist or operator documentation.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import socket
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("NTV_OUTPUT_DIR", str(ROOT / "Ntv" / "output")))
DEFAULT_CONFIG_URL = (
    "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/"
    "main/Ntv/ntv_scan_config.%20json"
)
CONFIG_PATH = Path(os.getenv("NTV_CONFIG", str(ROOT / "Ntv" / "ntv_scan_config.json")))
CONFIG_URL = os.getenv("NTV_CONFIG_URL", DEFAULT_CONFIG_URL)

DEFAULT_HOSTS = [
    "https://cdn.ntv.ru",
    "https://cdn2.ntv.ru",
    "https://cdn3.ntv.ru",
    "https://cdn-dvr.ntv.ru",
]
PATH_SUFFIXES = ("playlist.m3u8", "index.m3u8")

# Aliases are candidate path components, not claims that the origin uses them.
CHANNELS = [
    {"label": "НТВ — основной", "aliases": ["ntv0", "ntv", "ntv_main"]},
    {"label": "НТВ HD", "aliases": ["ntv0_hd", "ntv_hd"]},
    {"label": "НТВ Мир", "aliases": ["ntv_mir", "Mir"]},
    {"label": "НТВ Беларусь", "aliases": ["ntv_belarus", "Belarus"]},
    {"label": "НТВ Витрина", "aliases": ["vitrina"]},
    {"label": "НТВ — Следствие вели", "aliases": ["sledstvie_veli"]},
    {"label": "НТВ — Своя игра", "aliases": ["svoya_igra"]},
    {"label": "НТВ Право", "aliases": ["th_pravo", "pravo"]},
    {"label": "НТВ Стиль жизни", "aliases": ["th_style", "stil_zhizni", "style_life"]},
    {"label": "Неизвестная Россия", "aliases": ["unknown_russia", "neizvestnaya_rossiya"]},
    {"label": "НТВ Сериал", "aliases": ["th_serial", "serial"]},
    {"label": "НТВ-Хит", "aliases": ["th_hit"]},
]
TARGET_NAMES = [
    "НТВ — основной", "НТВ HD", "НТВ Мир", "НТВ Беларусь", "НТВ Витрина",
    "НТВ — Следствие вели", "НТВ — Своя игра", "НТВ Право",
    "НТВ Стиль жизни", "Неизвестная Россия", "НТВ Сериал", "НТВ-Хит",
]
ORBIT_OFFSETS = list(range(-1, 10))
QUALITIES = ("SD", "HD")
MAX_BODY = 2_000_000
MAX_URLS = 2500
MAX_DEPTH = 4
DEFAULT_CONCURRENCY = 24
DEFAULT_TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; NTV-HLS-Auditor/4.0)"
HLS_MARKER = "#EXTM3U"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normal(s: object) -> str:
    return str(s or "").casefold().replace("ё", "е")


def is_ntv_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "ntv.ru" or host.endswith(".ntv.ru")


def safe_filename_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "unknown"


def parse_attrs(line: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line):
        value = m.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        out[m.group(1)] = value
    return out


def parse_hls(body: str, base_url: str) -> dict:
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    is_master = any(x.startswith(("#EXT-X-STREAM-INF:", "#EXT-X-I-FRAME-STREAM-INF:", "#EXT-X-MEDIA:")) for x in lines)
    children, variants, extinf_entries = [], [], []
    pending_variant = None
    pending_extinf = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_variant = parse_attrs(line)
            continue
        if line.startswith("#EXT-X-I-FRAME-STREAM-INF:"):
            attrs = parse_attrs(line)
            uri = attrs.get("URI")
            if uri:
                child = urljoin(base_url, uri)
                variants.append({"url": child, "attrs": attrs})
                children.append(child)
            continue
        if line.startswith("#EXTINF:"):
            pending_extinf = line
            continue
        if line.startswith("#"):
            if line.startswith("#EXT-X-MEDIA:"):
                uri = parse_attrs(line).get("URI")
                if uri:
                    children.append(urljoin(base_url, uri))
            continue
        child = urljoin(base_url, line)
        if urlparse(child).scheme not in ("http", "https"):
            pending_variant = pending_extinf = None
            continue
        if pending_variant is not None:
            variants.append({"url": child, "attrs": pending_variant})
            pending_variant = None
        if pending_extinf is not None:
            extinf_entries.append({"extinf": pending_extinf, "url": child})
            pending_extinf = None
        if child.lower().split("?", 1)[0].endswith(".m3u8"):
            children.append(child)
    return {
        "master": is_master,
        "children": list(dict.fromkeys(children)),
        "variants": variants,
        "entries": extinf_entries,
        "segments": [
            urljoin(base_url, x) for x in lines
            if not x.startswith("#") and not x.lower().split("?", 1)[0].endswith(".m3u8")
        ][:5],
    }


def quality_from_attrs(attrs: dict) -> str:
    resolution = attrs.get("RESOLUTION", "")
    if resolution:
        m = re.match(r"\d+x(\d+)", resolution)
        if m:
            height = int(m.group(1))
            return "HD" if height >= 720 else "SD"
    bw = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or ""
    if bw.isdigit():
        return "HD" if int(bw) >= 2_000_000 else "SD"
    return "unknown"


def quality_label(attrs: dict) -> str:
    pieces = []
    if attrs.get("RESOLUTION"):
        pieces.append(attrs["RESOLUTION"])
    bw = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or ""
    if bw.isdigit():
        pieces.append(f"{int(bw)//1000} kbps")
    if attrs.get("CODECS"):
        pieces.append(attrs["CODECS"])
    return " / ".join(pieces)


def validate_config(user_cfg: object) -> dict:
    """Validate JSON and merge defaults; malformed optional config must not crash the scan."""
    if not isinstance(user_cfg, dict):
        raise ValueError("Корень JSON должен быть объектом.")

    cfg = {
        "hosts": DEFAULT_HOSTS,
        "channels": CHANNELS,
        "orbit_offsets": ORBIT_OFFSETS,
        "qualities": list(QUALITIES),
        "orbit_templates": [],
        "orbit_path_patterns_by_quality": {"SD": "ntv{offset}", "HD": "ntv{offset}_hd"},
        "extra_urls": [],
        "timezone_map": {},
    }
    cfg.update(user_cfg)

    if not isinstance(cfg.get("hosts"), list) or not cfg["hosts"]:
        raise ValueError("'hosts' должен быть непустым массивом URL.")
    cfg["hosts"] = [str(h).strip().rstrip("/") for h in cfg["hosts"] if str(h).strip()]
    if not cfg["hosts"] or any(not h.startswith(("http://", "https://")) for h in cfg["hosts"]):
        raise ValueError("Каждый элемент 'hosts' должен начинаться с http:// или https://.")

    if not isinstance(cfg.get("channels"), list):
        raise ValueError("'channels' должен быть массивом.")
    clean_channels = []
    for item in cfg["channels"]:
        if not isinstance(item, dict) or not item.get("label") or not isinstance(item.get("aliases"), list):
            raise ValueError("Каждый канал должен содержать 'label' и массив 'aliases'.")
        clean_channels.append({
            "label": str(item["label"]),
            "aliases": [str(a).strip() for a in item["aliases"] if str(a).strip()],
        })
    cfg["channels"] = clean_channels or CHANNELS

    try:
        cfg["orbit_offsets"] = [int(x) for x in cfg.get("orbit_offsets", ORBIT_OFFSETS)]
    except (TypeError, ValueError):
        raise ValueError("'orbit_offsets' должен содержать целые числа.")

    qualities = cfg.get("qualities", list(QUALITIES))
    if not isinstance(qualities, list):
        raise ValueError("'qualities' должен быть массивом.")
    cfg["qualities"] = [str(q).upper() for q in qualities if str(q).upper() in ("SD", "HD")]
    if not cfg["qualities"]:
        cfg["qualities"] = list(QUALITIES)

    for key in ("orbit_templates", "extra_urls"):
        if not isinstance(cfg.get(key, []), list):
            raise ValueError(f"'{key}' должен быть массивом.")
    if not isinstance(cfg.get("timezone_map", {}), dict):
        raise ValueError("'timezone_map' должен быть объектом.")
    if not isinstance(cfg.get("orbit_path_patterns_by_quality", {}), dict):
        cfg["orbit_path_patterns_by_quality"] = {"SD": "ntv{offset}", "HD": "ntv{offset}_hd"}
    return cfg


def load_config() -> dict:
    """Load local JSON first, then remote JSON; fall back to built-in defaults on any failure."""
    errors = []
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8-sig") as f:
                cfg = validate_config(json.load(f))
            print(f"Конфигурация загружена из файла: {CONFIG_PATH}")
            return cfg
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            errors.append(f"local config: {type(exc).__name__}: {exc}")

    try:
        import urllib.request
        req = urllib.request.Request(CONFIG_URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read(2_000_000).decode("utf-8-sig")
        remote_data = json.loads(raw)
        cfg = validate_config(remote_data)
        print(f"Конфигурация загружена по URL: {CONFIG_URL}")
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(remote_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return cfg
    except Exception as exc:
        errors.append(f"remote config: {type(exc).__name__}: {exc}")

    print("ПРЕДУПРЕЖДЕНИЕ: JSON-конфигурация недоступна или некорректна.")
    for error in errors:
        print(f"  - {error}")
    print("Продолжаю со встроенными настройками, чтобы сканер не падал.")
    return validate_config({})


def build_candidates(cfg: dict) -> list[dict]:
    rows = []
    # Channel aliases: exactly two independent patterns per alias and CDN node.
    for host in cfg["hosts"]:
        for channel in cfg["channels"]:
            for alias in channel["aliases"]:
                for suffix in PATH_SUFFIXES:
                    rows.append({
                        "url": f"{host.rstrip('/')}/{alias}/{suffix}",
                        "kind": "channel_path",
                        "channel": channel["label"],
                        "alias": alias,
                        "path_pattern": f"/{alias}/{suffix}",
                        "orbit_offset": "",
                        "quality_requested": "",
                        "mapping_status": "candidate_alias_unverified",
                    })
    # Orbital candidates: these naming hypotheses are probed but remain
    # unverified until a real master playlist confirms the offset mapping.
    for host in cfg["hosts"]:
        for offset in cfg.get("orbit_offsets", ORBIT_OFFSETS):
            for quality in cfg.get("qualities", QUALITIES):
                # Initial hypothesis: plain ntvN is SD and ntvN_hd is HD.
                # This naming convention is only a candidate and is recorded as such.
                patterns = cfg.get("orbit_path_patterns_by_quality", {
                    "SD": "ntv{offset}", "HD": "ntv{offset}_hd"
                })
                alias_pattern = patterns.get(quality, "ntv{offset}")
                alias = alias_pattern.format(offset=offset, offset_signed=f"{offset:+d}")
                for suffix in PATH_SUFFIXES:
                    path = f"{alias}/{suffix}"
                    rows.append({
                        "url": f"{host.rstrip('/')}/{path}",
                        "kind": "orbit_candidate",
                        "channel": "НТВ — орбитальный вариант",
                        "alias": alias,
                        "path_pattern": "/" + path,
                        "orbit_offset": offset,
                        "quality_requested": quality,
                        "mapping_status": "unverified_candidate_alias",
                    })
        for template in cfg.get("orbit_templates", []):
            for offset in cfg.get("orbit_offsets", ORBIT_OFFSETS):
                for quality in cfg.get("qualities", QUALITIES):
                    for suffix in PATH_SUFFIXES:
                        try:
                            path = template.format(
                                offset=offset,
                                offset_signed=f"{offset:+d}",
                                quality=quality.lower(),
                                quality_upper=quality.upper(),
                                suffix=suffix,
                            ).lstrip("/")
                        except (KeyError, ValueError):
                            continue
                        rows.append({
                            "url": f"{host.rstrip('/')}/{path}",
                            "kind": "orbit_candidate",
                            "channel": "НТВ — орбитальный вариант",
                            "alias": "",
                            "path_pattern": "/" + path,
                            "orbit_offset": offset,
                            "quality_requested": quality,
                            "mapping_status": "configured_template_unverified",
                        })
    for item in cfg.get("extra_urls", []):
        if isinstance(item, str):
            rows.append({
                "url": item, "kind": "extra_url", "channel": "",
                "alias": "", "path_pattern": urlparse(item).path,
                "orbit_offset": "", "quality_requested": "",
                "mapping_status": "user_configured",
            })
        elif isinstance(item, dict) and item.get("url"):
            rows.append({
                "url": item["url"], "kind": "extra_url",
                "channel": item.get("channel", ""),
                "alias": item.get("alias", ""),
                "path_pattern": urlparse(item["url"]).path,
                "orbit_offset": item.get("orbit_offset", ""),
                "quality_requested": item.get("quality_requested", ""),
                "mapping_status": item.get("mapping_status", "user_configured"),
            })
    # Deduplicate by URL but preserve the most descriptive metadata.
    unique = {}
    for row in rows:
        unique.setdefault(row["url"], row)
    return list(unique.values())


async def fetch(session: aiohttp.ClientSession, sem: asyncio.Semaphore, row: dict) -> dict:
    started = time.perf_counter()
    out = dict(row)
    out.update({
        "checked_at_utc": utc_now(), "final_url": "", "http_status": 0,
        "content_type": "", "bytes": 0, "latency_ms": 0.0, "is_hls": False,
        "is_master": False, "is_media_playlist": False, "valid_hls": False,
        "variant_count": 0, "variants_json": "[]", "resolution": "",
        "detected_quality": "unknown", "sample_segment_url": "",
        "sample_segment_status": "", "sample_segment_content_type": "",
        "timezone": "unknown", "error": "", "_body": "", "_children": [],
        "_variants": [], "_entries": [],
    })
    async with sem:
        try:
            async with session.get(row["url"], allow_redirects=True) as resp:
                raw = await resp.content.read(MAX_BODY)
                body = raw.decode("utf-8", errors="replace")
                out.update({
                    "final_url": str(resp.url),
                    "http_status": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "bytes": len(raw),
                    "is_hls": HLS_MARKER in body[:4096],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                })
                if out["is_hls"]:
                    parsed = parse_hls(body, str(resp.url))
                    out.update({
                        "is_master": parsed["master"],
                        "is_media_playlist": not parsed["master"],
                        "valid_hls": resp.status == 200,
                        "variant_count": len(parsed["variants"]),
                        "variants_json": json.dumps([
                            {"url": v["url"], "attrs": v["attrs"],
                             "detected_quality": quality_from_attrs(v["attrs"])}
                            for v in parsed["variants"]
                        ], ensure_ascii=False),
                        "_body": body, "_children": parsed["children"],
                        "_variants": parsed["variants"], "_entries": parsed["entries"],
                    })
                    if parsed["segments"]:
                        out["sample_segment_url"] = parsed["segments"][0]
                elif resp.status >= 400:
                    out["error"] = f"HTTP {resp.status}; response is not HLS"
                else:
                    out["error"] = "HTTP response is not an HLS playlist"
        except Exception as exc:
            # A bad URL or unexpected server response must not abort the whole scan.
            out["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def check_segment(session: aiohttp.ClientSession, sem: asyncio.Semaphore, row: dict) -> None:
    url = row.get("sample_segment_url")
    if not url or not row.get("valid_hls"):
        return
    async with sem:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                row["sample_segment_status"] = resp.status
                row["sample_segment_content_type"] = resp.headers.get("Content-Type", "")
                # Read only a tiny prefix to avoid downloading media.
                await resp.content.read(128)
        except Exception as exc:
            row["sample_segment_status"] = 0
            row["sample_segment_content_type"] = ""
            row["error"] = (row.get("error", "") + f" | segment: {type(exc).__name__}: {exc}").strip(" |")


def infer_channel(row: dict, inherited: str = "") -> str:
    hay = normal(" ".join([
        row.get("channel", ""), row.get("alias", ""), row.get("url", ""),
        row.get("final_url", ""), row.get("_body", ""),
    ]))
    for channel in CHANNELS:
        if any(normal(a) in hay for a in channel["aliases"]) or normal(channel["label"]) in hay:
            return channel["label"]
    return row.get("channel") or inherited or "НТВ — имя не определено"


def make_extinf(name: str, attrs: dict | None = None) -> str:
    details = quality_label(attrs or {})
    if details:
        name = f"{name} [{details}]"
    name = name.replace("\r", " ").replace("\n", " ").replace('"', "'")
    return f'#EXTINF:-1 group-title="НТВ",{name}'


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def discover_orbit_rows(results: list[dict]) -> list[dict]:
    """Find orbit/quality evidence in discovered master playlists.
    This extracts real variant URLs and attributes; it does not invent offset
    labels when a master playlist does not encode them.
    """
    out = []
    for r in results:
        for variant in r.get("_variants", []):
            attrs = variant.get("attrs", {})
            url = variant.get("url", "")
            path = urlparse(url).path
            m = re.search(r"(?<!\d)([+-]\d{1,2})(?!\d)", path)
            offset = int(m.group(1)) if m else ""
            out.append({
                "source_url": r.get("url", ""),
                "variant_url": url,
                "resolution": attrs.get("RESOLUTION", ""),
                "bandwidth": attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH", ""),
                "codecs": attrs.get("CODECS", ""),
                "detected_quality": quality_from_attrs(attrs),
                "orbit_offset_detected_from_path": offset,
                "mapping_status": "path_token_only_not_verified" if offset != "" else "offset_not_encoded",
            })
    return out


async def run_scan(cfg: dict, concurrency: int, timeout_s: int) -> tuple[list[dict], list[dict]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates(cfg)
    # Seed recursive exploration with all candidate URLs.
    queue = deque((row, 0, row.get("channel", "")) for row in candidates)
    queued = {row["url"] for row in candidates}
    by_url: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=timeout_s, connect=min(7, timeout_s))
    async with aiohttp.ClientSession(
        timeout=timeout, connector=connector,
        headers={"User-Agent": UA},
    ) as session:
        while queue and len(by_url) < MAX_URLS:
            batch = []
            while queue and len(batch) < concurrency:
                row, depth, inherited = queue.popleft()
                if row["url"] in by_url:
                    continue
                batch.append((row, depth, inherited))
            if not batch:
                break
            print(f"Проверка URL: {min(len(by_url)+len(batch), MAX_URLS)}/{MAX_URLS}")
            fetched = await asyncio.gather(*(fetch(session, sem, row) for row, _, _ in batch))
            for (original, depth, inherited), result in zip(batch, fetched):
                result["depth"] = depth
                result["inherited_channel"] = inherited
                result["channel_detected"] = infer_channel(result, inherited)
                by_url[original["url"]] = result
                if depth >= MAX_DEPTH:
                    continue
                for child in result.get("_children", []):
                    if not is_ntv_host(child) or child in queued or child in by_url or len(queued) >= MAX_URLS:
                        continue
                    queued.add(child)
                    child_row = {
                        "url": child, "kind": "recursive_hls_child",
                        "channel": result["channel_detected"], "alias": "",
                        "path_pattern": urlparse(child).path,
                        "orbit_offset": "", "quality_requested": "",
                        "mapping_status": "discovered_from_hls",
                    }
                    queue.append((child_row, depth + 1, result["channel_detected"]))
        # Probe one small prefix of a segment for successful media playlists.
        await asyncio.gather(*(check_segment(session, sem, r) for r in by_url.values()))
    results = list(by_url.values())
    return results, candidates


def create_reports(results: list[dict], cfg: dict) -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Report all requests, retaining candidate provenance and HLS details.
    report_fields = [
        "checked_at_utc", "kind", "channel", "channel_detected", "alias",
        "path_pattern", "orbit_offset", "quality_requested", "mapping_status",
        "url", "final_url", "http_status", "content_type", "bytes",
        "latency_ms", "is_hls", "is_master", "is_media_playlist", "valid_hls",
        "variant_count", "variants_json", "sample_segment_url",
        "sample_segment_status", "sample_segment_content_type", "timezone",
        "depth", "error",
    ]
    report_rows = []
    streams = {}
    masters = []
    target_lines = []
    node_checks = []
    timezone_map = cfg.get("timezone_map", {})
    for r in results:
        r["timezone"] = timezone_map.get(urlparse(r.get("final_url") or r["url"]).hostname or "", "unknown")
        report_rows.append(r)
        node_checks.append({
            "checked_at_utc": r.get("checked_at_utc", ""),
            "cdn_node": urlparse(r["url"]).hostname or "",
            "channel": r.get("channel", ""),
            "alias": r.get("alias", ""),
            "path_pattern": r.get("path_pattern", ""),
            "kind": r.get("kind", ""),
            "orbit_offset": r.get("orbit_offset", ""),
            "quality_requested": r.get("quality_requested", ""),
            "http_status": r.get("http_status", ""),
            "final_url": r.get("final_url", ""),
            "latency_ms": r.get("latency_ms", ""),
            "is_hls": r.get("is_hls", False),
            "valid_hls": r.get("valid_hls", False),
            "sample_segment_status": r.get("sample_segment_status", ""),
            "timezone": r.get("timezone", "unknown"),
            "mapping_status": r.get("mapping_status", ""),
            "error": r.get("error", ""),
        })
        if r.get("is_master"):
            masters.append(f"URL: {r['url']}\nHTTP: {r['http_status']} FINAL: {r['final_url']}\n{r.get('_body','')}\n\n{'='*72}\n\n")
        parent = r.get("channel_detected") or r.get("channel") or "НТВ — имя не определено"
        for v in r.get("_variants", []):
            url = v["url"]
            if not is_ntv_host(url):
                continue
            attrs = v.get("attrs", {})
            name = parent
            q = quality_from_attrs(attrs)
            streams.setdefault(url, {
                "name": name, "url": url, "resolution": attrs.get("RESOLUTION", ""),
                "bandwidth": attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH", ""),
                "codecs": attrs.get("CODECS", ""), "detected_quality": q,
                "source_url": r["url"], "orbit_offset": r.get("orbit_offset", ""),
                "quality_requested": r.get("quality_requested", ""),
                "extinf": make_extinf(name, attrs),
            })
        for e in r.get("_entries", []):
            url = e["url"]
            if not is_ntv_host(url):
                continue
            extinf = e["extinf"]
            name = extinf.rsplit(",", 1)[-1].strip() if "," in extinf else parent
            streams.setdefault(url, {
                "name": name or parent, "url": url, "resolution": "",
                "bandwidth": "", "codecs": "", "detected_quality": "unknown",
                "source_url": r["url"], "orbit_offset": r.get("orbit_offset", ""),
                "quality_requested": r.get("quality_requested", ""),
                "extinf": extinf,
            })
        # Valid media playlist can itself be a playable URL.
        if r.get("valid_hls") and r.get("is_media_playlist"):
            url = r.get("final_url") or r["url"]
            if is_ntv_host(url):
                streams.setdefault(url, {
                    "name": parent, "url": url, "resolution": "",
                    "bandwidth": "", "codecs": "", "detected_quality": "unknown",
                    "source_url": r["url"], "orbit_offset": r.get("orbit_offset", ""),
                    "quality_requested": r.get("quality_requested", ""),
                    "extinf": make_extinf(parent),
                })

    paths = {}
    p = OUTPUT_DIR / "ntv_report.csv"
    write_csv(p, report_rows, report_fields); paths[p.name] = p

    p = OUTPUT_DIR / "ntv_streams.csv"
    stream_fields = ["name", "url", "resolution", "bandwidth", "codecs", "detected_quality", "source_url", "orbit_offset", "quality_requested"]
    write_csv(p, list(streams.values()), stream_fields); paths[p.name] = p

    p = OUTPUT_DIR / "ntv_found.m3u"
    with p.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in sorted(streams.values(), key=lambda x: (x["name"], x["url"])):
            f.write(s["extinf"] + "\n" + s["url"] + "\n")
    paths[p.name] = p

    p = OUTPUT_DIR / "ntv_masters.txt"
    p.write_text("".join(masters) or "Мастер-плейлисты не обнаружены в проверенных URL.\n", encoding="utf-8")
    paths[p.name] = p

    p = OUTPUT_DIR / "ntv_target_channels.txt"
    with p.open("w", encoding="utf-8") as f:
        for ch in cfg["channels"]:
            label = ch["label"]
            hits = [r for r in results if r.get("channel_detected") == label or r.get("channel") == label]
            f.write(f"{label}\n")
            if not hits:
                f.write("Совпадений не найдено в проверенных кандидатах; это не доказывает отсутствие канала.\n")
            for r in hits:
                f.write(
                    f"alias={r.get('alias','')} status={r.get('http_status',0)} "
                    f"HLS={r.get('is_hls',False)} valid={r.get('valid_hls',False)} "
                    f"latency_ms={r.get('latency_ms','')} URL={r.get('url','')} "
                    f"FINAL={r.get('final_url','')} error={r.get('error','')}\n"
                )
            f.write("\n")
    paths[p.name] = p

    p = OUTPUT_DIR / "ntv_node_checks.csv"
    write_csv(p, node_checks, [
        "checked_at_utc", "cdn_node", "channel", "alias", "path_pattern", "kind",
        "orbit_offset", "quality_requested", "http_status", "final_url", "latency_ms",
        "is_hls", "valid_hls", "sample_segment_status", "timezone", "mapping_status", "error",
    ])
    paths[p.name] = p

    p = OUTPUT_DIR / "ntv_node_timezones.csv"
    hosts = sorted({urlparse(r["url"]).hostname or "" for r in results})
    write_csv(p, [
        {"cdn_node": h, "timezone": timezone_map.get(h, "unknown"),
         "basis": "explicit_config" if h in timezone_map else "unknown_not_inferred_from_IP_or_hostname"}
        for h in hosts
    ], ["cdn_node", "timezone", "basis"])
    paths[p.name] = p

    orbit_evidence = discover_orbit_rows(results)
    p = OUTPUT_DIR / "ntv_orbit_evidence.csv"
    write_csv(p, orbit_evidence, [
        "source_url", "variant_url", "resolution", "bandwidth", "codecs",
        "detected_quality", "orbit_offset_detected_from_path", "mapping_status",
    ])
    paths[p.name] = p

    by_kind = {}
    for r in results:
        kind = r.get("kind", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    summary = {
        "generated_at_utc": utc_now(),
        "output_dir": str(OUTPUT_DIR),
        "candidate_count": len(results),
        "http_200_count": sum(r.get("http_status") == 200 for r in results),
        "hls_count": sum(bool(r.get("is_hls")) for r in results),
        "valid_hls_count": sum(bool(r.get("valid_hls")) for r in results),
        "master_playlist_count": sum(bool(r.get("is_master")) for r in results),
        "unique_stream_count": len(streams),
        "orbit_offsets_requested": cfg.get("orbit_offsets", ORBIT_OFFSETS),
        "qualities_requested": cfg.get("qualities", list(QUALITIES)),
        "orbit_template_count": len(cfg.get("orbit_templates", [])),
        "orbit_warning": (
            "Offsets -1..+9 and SD/HD are probed using candidate aliases ntv{offset} "
            "and ntv{offset}_hd, plus any configured templates. These alias mappings "
            "are unverified until confirmed by a real master playlist. Requested quality "
            "is not treated as proof: detected quality comes from HLS resolution/bitrate."
        ),
        "counts_by_kind": by_kind,
        "timezone_policy": "unknown unless explicitly configured in timezone_map",
        "reports": sorted(paths.keys()),
    }
    p = OUTPUT_DIR / "ntv_scan_summary.json"
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths[p.name] = p
    return paths


async def async_main(args) -> None:
    cfg = load_config()
    if args.concurrency:
        concurrency = args.concurrency
    else:
        concurrency = DEFAULT_CONCURRENCY
    timeout = args.timeout or DEFAULT_TIMEOUT
    print(f"NTV scanner: nodes={len(cfg['hosts'])}, concurrency={concurrency}")
    print(f"Кандидатов URL: {len(build_candidates(cfg))}")
    results, candidates = await run_scan(cfg, concurrency, timeout)
    paths = create_reports(results, cfg)
    print(f"\nСканирование завершено: проверено {len(results)} URL.")
    print(f"HTTP 200: {sum(r.get('http_status') == 200 for r in results)}")
    print(f"HLS: {sum(bool(r.get('is_hls')) for r in results)}; валидных HLS: {sum(bool(r.get('valid_hls')) for r in results)}")
    print(f"Потоков в M3U: {sum(1 for _ in open(OUTPUT_DIR / 'ntv_found.m3u', encoding='utf-8') if _.startswith('#EXTINF'))}")
    print(f"Отчёты: {OUTPUT_DIR}")
    for name in paths:
        print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="NTV CDN and HLS auditor")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    if args.concurrency < 1 or args.timeout < 1:
        parser.error("--concurrency and --timeout must be positive")
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
