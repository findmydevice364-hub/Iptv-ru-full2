#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IPTV INTELLIGENCE AUDITOR
Database Edition

Сбор публичных IPTV-источников:
- Main / All / Best M3U
- Казахстанские источники
- Uplink: сопоставление опубликованных URL
- TV1000 / Nowella / Romantica
- Master / Child HLS
- FFprobe и многодорожечное аудио
- Исторические результаты
- SQLite и отчёты
"""

import asyncio
import json
import logging
import re
import sqlite3
import time

from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import aiohttp


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = OUTPUT_DIR / "iptv_base.sqlite3"

SOURCES = {
    "MAIN_M3U": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/main/"
        "processed/combined_by_name.m3u"
    ),
    "ALL_M3U": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/main/"
        "output/all.m3u"
    ),
    "BEST_M3U": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/main/"
        "output/best.m3u"
    ),
    "KZ_ALL_NODES": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/"
        "refs/heads/main/kz_all_nodes_full.m3u"
    ),
    "UPLINK_M3U": "http://uplink.kz/video.m3u",
    "PROBE_REPORT": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/main/"
        "output/probe_full_report.txt"
    ),
    "DOMAINS": (
        "https://raw.githubusercontent.com/"
        "findmydevice364-hub/Iptv-ru-full2/main/"
        "domains.txt"
    ),
}

HISTORY_FILES = [
    "probe_multi.json",
    "probe_multi.txt",
    "real_multiaudio.m3u",
    "probe_all_ok.json",
    "probe_full_progress.json",
]

HTTP_TIMEOUT = 25
MAX_CONCURRENT_DOWNLOADS = 70
MAX_PROBES = 5000
RECHECK_DAYS = 7

HEADERS = {
    "User-Agent": "IPTV-Intelligence-Auditor/1.0"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

log = logging.getLogger("IPTV-AUDITOR")


# ============================================================
# DATABASE
# ============================================================

class Database:

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            url TEXT,
            status TEXT,
            last_checked TEXT
        );

        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            url_key TEXT,
            is_uplink INTEGER DEFAULT 0,
            token_parameter_present INTEGER DEFAULT 0,
            last_status TEXT,
            last_checked TEXT,
            probe_json TEXT
        );

        CREATE TABLE IF NOT EXISTS stream_sources (
            stream_id INTEGER,
            source_id INTEGER,
            first_seen TEXT,
            PRIMARY KEY(stream_id, source_id)
        );

        CREATE TABLE IF NOT EXISTS channel_names (
            id INTEGER PRIMARY KEY,
            stream_id INTEGER,
            original_name TEXT,
            normalized_name TEXT,
            quality TEXT,
            family TEXT
        );

        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            stream_id INTEGER,
            checked_at TEXT,
            status TEXT,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            playlist_type TEXT,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS playlist_relationships (
            parent_url TEXT,
            child_url TEXT,
            relation_type TEXT,
            PRIMARY KEY(parent_url, child_url)
        );

        CREATE TABLE IF NOT EXISTS domain_inventory (
            id INTEGER PRIMARY KEY,
            host TEXT,
            port INTEGER,
            reported_count INTEGER,
            source TEXT,
            UNIQUE(host, port, source)
        );
        """)

        self.conn.commit()

    def source_id(self, name, url):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO sources(name,url)
            VALUES(?,?)
            """,
            (name, url)
        )

        row = self.conn.execute(
            "SELECT id FROM sources WHERE name=?",
            (name,)
        ).fetchone()

        self.conn.commit()
        return row["id"]

    def add_stream(self, url, source_id, name=None):
        p = urlsplit(url)

        is_uplink = int(
            (p.hostname or "").lower()
            in ("uplink.kz", "fs.uplink.kz")
        )

        token_present = int(
            "token" in parse_qs(p.query)
        )

        self.conn.execute(
            """
            INSERT OR IGNORE INTO streams(
                url,url_key,is_uplink,token_parameter_present
            )
            VALUES(?,?,?,?)
            """,
            (
                url,
                url.lower(),
                is_uplink,
                token_present
            )
        )

        row = self.conn.execute(
            "SELECT id FROM streams WHERE url=?",
            (url,)
        ).fetchone()

        stream_id = row["id"]

        self.conn.execute(
            """
            INSERT OR IGNORE INTO stream_sources(
                stream_id,source_id,first_seen
            )
            VALUES(?,?,datetime('now'))
            """,
            (stream_id, source_id)
        )

        if name:
            normalized, quality, family = classify_channel(name)

            self.conn.execute(
                """
                INSERT INTO channel_names(
                    stream_id,original_name,
                    normalized_name,quality,family
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    stream_id,
                    name,
                    normalized,
                    quality,
                    family
                )
            )

        self.conn.commit()
        return stream_id

    def all_streams(self):
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM streams ORDER BY id"
            ).fetchall()
        ]

    def close(self):
        self.conn.commit()
        self.conn.close()


# ============================================================
# CHANNEL CLASSIFICATION
# ============================================================

def classify_channel(name):

    original = name.strip()
    upper = original.upper()

    family = None

    for candidate in (
        "TV1000",
        "NOWELLA",
        "ROMANTICA"
    ):
        if candidate in upper:
            family = candidate
            break

    quality = "UNKNOWN"

    for q in ("UHD", "4K", "FHD", "FULL HD", "HD", "SD"):
        if q in upper:
            quality = q
            break

    normalized = re.sub(
        r"\b(UHD|4K|FHD|FULL HD|HD|SD)\b",
        "",
        upper
    )

    normalized = re.sub(
        r"[^A-ZА-ЯЁ0-9]+",
        " ",
        normalized
    ).strip()

    return normalized, quality, family


# ============================================================
# HTTP
# ============================================================

async def fetch_text(session, url):

    try:
        async with session.get(url) as response:

            if response.status != 200:
                return None

            return await response.text(
                errors="replace"
            )

    except Exception as exc:
        log.warning("Download error %s: %s", url, exc)
        return None


# ============================================================
# M3U PARSER
# ============================================================

async def parse_m3u_source(
    session,
    db,
    source_name,
    source_url
):

    source_id = db.source_id(
        source_name,
        source_url
    )

    content = await fetch_text(
        session,
        source_url
    )

    if not content:
        return 0

    count = 0
    current_name = None

    for line in content.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            match = re.search(
                r",\s*(.*?)\s*$",
                line
            )

            current_name = (
                match.group(1).strip()
                if match else "UNKNOWN"
            )

        elif line.startswith("#"):
            continue

        elif line.startswith(
            ("http://", "https://")
        ):

            if is_media_segment(line):
                current_name = None
                continue

            db.add_stream(
                line,
                source_id,
                current_name
            )

            count += 1
            current_name = None

    log.info(
        "%s imported: %d",
        source_name,
        count
    )

    return count


def is_media_segment(url):

    path = urlsplit(url).path.lower()

    return path.endswith(
        (".ts", ".m4s", ".cmfv", ".cmfa")
    )


# ============================================================
# DOMAIN INVENTORY
# ============================================================

def parse_domain_inventory(content, db):

    count = 0

    for line in content.splitlines():

        line = line.strip()

        if not line or line.startswith("#"):
            continue

        match = re.match(
            r"^([^\\s]+?)(?::(\\d+))?(?::\\s*(\\d+))?$",
            line
        )

        if not match:
            continue

        host = match.group(1)
        port = int(match.group(2)) if match.group(2) else None
        reported_count = (
            int(match.group(3))
            if match.group(3) else None
        )

        db.conn.execute(
            """
            INSERT OR IGNORE INTO domain_inventory(
                host,port,reported_count,source
            )
            VALUES(?,?,?,?)
            """,
            (
                host,
                port,
                reported_count,
                "domains.txt"
            )
        )

        count += 1

    db.conn.commit()
    return count


# ============================================================
# HLS CLASSIFICATION
# ============================================================

def classify_hls_manifest(content):

    if "#EXT-X-STREAM-INF" in content:
        return "master"

    if "#EXT-X-MEDIA:" in content:
        return "master"

    if "#EXT-X-TARGETDURATION" in content:
        return "media"

    if "#EXTINF:" in content:
        return "media"

    return "unknown"


async def discover_hls_children(session, db):

    streams = db.all_streams()

    discovered = 0

    for item in streams:

        url = item["url"]

        if ".m3u8" not in url.lower():
            continue

        content = await fetch_text(
            session,
            url
        )

        if not content:
            continue

        playlist_type = classify_hls_manifest(
            content
        )

        db.conn.execute(
            """
            INSERT OR REPLACE INTO playlists(
                url,playlist_type,details
            )
            VALUES(?,?,?)
            """,
            (
                url,
                playlist_type,
                json.dumps(
                    {"discovered": True},
                    ensure_ascii=False
                )
            )
        )

        if playlist_type != "master":
            continue

        parent = url

        for line in content.splitlines():

            line = line.strip()

            if not line or line.startswith("#"):
                continue

            from urllib.parse import urljoin

            child = urljoin(parent, line)

            db.conn.execute(
                """
                INSERT OR IGNORE INTO playlist_relationships(
                    parent_url,child_url,relation_type
                )
                VALUES(?,?,?)
                """,
                (
                    parent,
                    child,
                    "hls_variant"
                )
            )

            source_id = db.source_id(
                "HLS_DISCOVERY",
                parent
            )

            db.add_stream(
                child,
                source_id,
                None
            )

            discovered += 1

    db.conn.commit()

    log.info(
        "HLS child playlists discovered: %d",
        discovered
    )


# ============================================================
# LEGACY HISTORY
# ============================================================

async def import_legacy_history(db):

    for filename in HISTORY_FILES:

        path = BASE_DIR / filename

        if not path.exists():
            continue

        log.info(
            "Importing historical file: %s",
            filename
        )

        source_id = db.source_id(
            "HISTORY_" + filename,
            str(path)
        )

        if path.suffix.lower() == ".m3u":

            content = path.read_text(
                encoding="utf-8",
                errors="replace"
            )

            current_name = None

            for line in content.splitlines():

                line = line.strip()

                if line.startswith("#EXTINF"):
                    match = re.search(
                        r",\s*(.*?)\s*$",
                        line
                    )

                    current_name = (
                        match.group(1)
                        if match else None
                    )

                elif line.startswith("http"):
                    db.add_stream(
                        line,
                        source_id,
                        current_name
                    )
                    current_name = None

        elif path.suffix.lower() == ".json":

            try:
                data = json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue

            records = (
                data if isinstance(data, list)
                else data.get("streams", [])
                if isinstance(data, dict)
                else []
            )

            for record in records:

                if not isinstance(record, dict):
                    continue

                url = (
                    record.get("url")
                    or record.get("stream_url")
                )

                if url and str(url).startswith("http"):
                    db.add_stream(
                        str(url),
                        source_id,
                        record.get("name")
                    )


# ============================================================
# UPLINK PUBLISHED URL COMPARISON
# ============================================================

def uplink_pair_key(url):

    try:
        p = urlsplit(url)

        host = (p.hostname or "").lower()

        if host not in (
            "uplink.kz",
            "fs.uplink.kz"
        ):
            return None

        return (
            p.path.rstrip("/").lower(),
            host
        )

    except ValueError:
        return None


def export_uplink_comparison(items):

    groups = defaultdict(list)

    for item in items:

        key = uplink_pair_key(
            item["url"]
        )

        if not key:
            continue

        path, host = key

        query = parse_qs(
            urlsplit(item["url"]).query
        )

        has_token = "token" in query

        groups[path].append({
            "url": item["url"],
            "host": host,
            "published_token_variant": has_token,
            "status": item.get(
                "last_status",
                "not_checked"
            )
        })

    rows = []

    counts = {
        "paths": len(groups),
        "both_published": 0,
        "without_token_only": 0,
        "with_token_only": 0
    }

    for path, variants in sorted(groups.items()):

        plain = [
            v for v in variants
            if not v["published_token_variant"]
        ]

        token = [
            v for v in variants
            if v["published_token_variant"]
        ]

        if plain and token:
            category = "both_published"

        elif token:
            category = "with_token_only"

        else:
            category = "without_token_only"

        counts[category] += 1

        rows.append({
            "path": path,
            "match_status": category,
            "without_token": plain,
            "published_with_token": token
        })

    (OUTPUT_DIR / "UPLINK_COMPARISON.json").write_text(
        json.dumps(
            {
                "counts": counts,
                "pairs": rows
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    with (
        OUTPUT_DIR / "UPLINK_COMPARISON.txt"
    ).open("w", encoding="utf-8") as f:

        f.write("UPLINK URL COMPARISON\n\n")
        f.write(json.dumps(counts, indent=2))
        f.write("\n\n")

        for row in rows:

            f.write(
                f"[{row['match_status']}] "
                f"{row['path']}\n"
            )

            for variant in row["without_token"]:
                f.write(
                    "  PLAIN: "
                    + variant["url"]
                    + "\n"
                )

            for variant in row["published_with_token"]:
                f.write(
                    "  PUBLISHED TOKEN: "
                    + variant["url"]
                    + "\n"
                )

    return counts


# ============================================================
# FFPROBE
# ============================================================

async def probe_stream(url):

    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=20
        )

        if process.returncode != 0:
            return {
                "status": "failed",
                "error": stderr.decode(
                    errors="replace"
                )[:1000]
            }

        data = json.loads(
            stdout.decode(
                errors="replace"
            )
        )

        streams = data.get("streams", [])

        audio = [
            s for s in streams
            if s.get("codec_type") == "audio"
        ]

        languages = [
            s.get("tags", {}).get("language", "unknown")
            for s in audio
        ]

        return {
            "status": "working",
            "audio_count": len(audio),
            "audio_languages": languages,
            "video_count": sum(
                s.get("codec_type") == "video"
                for s in streams
            ),
            "format": data.get("format", {}).get(
                "format_name"
            )
        }

    except asyncio.TimeoutError:

        process.kill()

        return {
            "status": "timeout"
        }

    except Exception as exc:

        return {
            "status": "error",
            "error": str(exc)
        }


async def run_probes(db):

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_DOWNLOADS
    )

    streams = db.all_streams()

    async def worker(item):

        async with semaphore:

            result = await probe_stream(
                item["url"]
            )

            stream_id = item["id"]

            db.conn.execute(
                """
                UPDATE streams
                SET last_status=?,
                    last_checked=datetime('now'),
                    probe_json=?
                WHERE id=?
                """,
                (
                    result.get("status"),
                    json.dumps(
                        result,
                        ensure_ascii=False
                    ),
                    stream_id
                )
            )

            db.conn.execute(
                """
                INSERT INTO observations(
                    stream_id,checked_at,status,details
                )
                VALUES(?,datetime('now'),?,?)
                """,
                (
                    stream_id,
                    result.get("status"),
                    json.dumps(
                        result,
                        ensure_ascii=False
                    )
                )
            )

            db.conn.commit()

            return result

    checked = 0

    for item in streams[:MAX_PROBES]:

        await worker(item)
        checked += 1

        if checked % 50 == 0:
            log.info(
                "FFprobe progress: %d/%d",
                checked,
                min(len(streams), MAX_PROBES)
            )

    return checked


# ============================================================
# OUTPUTS
# ============================================================

def save_outputs(db):

    items = db.all_streams()

    uplink_stats = export_uplink_comparison(
        items
    )

    working = []
    failed = []
    multiaudio = []

    families = {
        "TV1000": [],
        "NOWELLA": [],
        "ROMANTICA": []
    }

    for item in items:

        try:
            probe = json.loads(
                item.get("probe_json") or "{}"
            )
        except Exception:
            probe = {}

        if probe.get("status") == "working":
            working.append(item["url"])
        else:
            failed.append(item["url"])

        if probe.get("audio_count", 0) >= 2:
            multiaudio.append(item["url"])

        names = db.conn.execute(
            """
            SELECT family
            FROM channel_names
            WHERE stream_id=?
            """,
            (item["id"],)
        ).fetchall()

        for row in names:

            family = row["family"]

            if family in families:
                families[family].append(
                    item["url"]
                )

    def write_m3u(filename, urls):

        with (
            OUTPUT_DIR / filename
        ).open("w", encoding="utf-8") as f:

            f.write("#EXTM3U\n")

            for url in sorted(set(urls)):
                f.write(
                    "#EXTINF:-1,"
                    + url
                    + "\n"
                )
                f.write(url + "\n")

    write_m3u(
        "ALL_STREAMS.m3u",
        [x["url"] for x in items]
    )

    write_m3u(
        "WORKING_STREAMS.m3u",
        working
    )

    write_m3u(
        "UNVERIFIED.m3u",
        failed
    )

    write_m3u(
        "MULTIAUDIO.m3u",
        multiaudio
    )

    for family, urls in families.items():

        write_m3u(
            family + "_VARIANTS.m3u",
            urls
        )

    audit = {
        "statistics": {
            "unique_urls": len(items),
            "working": len(working),
            "failed_or_unverified": len(failed),
            "multiaudio": len(multiaudio),
            "uplink": uplink_stats,
            "families": {
                key: len(value)
                for key, value in families.items()
            }
        },
        "streams": items
    }

    (OUTPUT_DIR / "audit.json").write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    (OUTPUT_DIR / "audit_summary.txt").write_text(
        json.dumps(
            audit["statistics"],
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return audit["statistics"]


# ============================================================
# MAIN
# ============================================================

async def main():

    started = time.time()

    db = Database(DB_FILE)

    timeout = aiohttp.ClientTimeout(
        total=HTTP_TIMEOUT
    )

    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT_DOWNLOADS
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers=HEADERS
        ) as session:

            for name, url in SOURCES.items():
                db.source_id(name, url)

            # Domain inventory
            content = await fetch_text(
                session,
                SOURCES["DOMAINS"]
            )

            if content:
                parse_domain_inventory(
                    content,
                    db
                )

            # Public M3U sources
            for name in (
                "MAIN_M3U",
                "ALL_M3U",
                "BEST_M3U",
                "KZ_ALL_NODES",
                "UPLINK_M3U"
            ):

                await parse_m3u_source(
                    session,
                    db,
                    name,
                    SOURCES[name]
                )

            # Historical results
            await import_legacy_history(db)

            # HLS master/child discovery
            await discover_hls_children(
                session,
                db
            )

        # FFprobe
        checked = await run_probes(db)

        stats = save_outputs(db)

        log.info(
            "FFprobe checked: %d",
            checked
        )

        log.info(
            "Unique URLs: %d",
            stats["unique_urls"]
        )

        log.info(
            "Finished in %.1f seconds",
            time.time() - started
        )

    finally:
        db.close()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        log.warning("Interrupted")
        raise SystemExit(130)