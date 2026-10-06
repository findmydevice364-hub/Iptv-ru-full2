#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RU IPTV MEGA PARSER (ULTRA-EXPANDED) + external channel database

Sources are public M3U/M3U8/XMLTV URLs. No stream URL is invented.
Dead streams are preserved. Alternatives are found from already discovered
streams using canonical channel identity + aliases + fuzzy similarity.

External canonical channel database:
  channels.csv
  channels.json
  channels_data.py
from findmydevice364-hub/Iptv-ru-full2.
The Python database is parsed safely with AST/literal_eval; it is NEVER exec'd.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import csv
import gzip
import io
import json
import logging
import random
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

OUT = Path("mega_iptv_output")
OUT.mkdir(parents=True, exist_ok=True)
DB = OUT / "mega_iptv.db"
LOG = OUT / "mega_parser.log"
STABLE_STATE_DB = OUT / "stable_state.json"

TARGET_CHANNELS = 20_000
TARGET_RU = 10_000
MIN_ALTERNATIVES = 12
TARGET_ALTERNATIVES = 20
CHECK_WORKERS = 256
FETCH_WORKERS = 32
ALT_SIMILARITY_THRESHOLD = 0.60
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 12
CACHE_TTL = 6 * 3600
MAX_BYTES = 80 * 1024 * 1024
STABLE_LATENCY_THRESHOLD_MS = 1500
STREAM_CHECK_RETRIES = 1
ALT_INDEX_MIN_TOKEN_LEN = 3
ALT_DIVERSITY_BONUS = 8.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140 Safari/537.36 RU-IPTV-Mega-Parser/3.0"
)
USER_AGENT_POOL = [
    USER_AGENT,
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "VLC/3.0.20 LibVLC/3.0.20",
    "Lavf/60.16.100",
]

BASE_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://smolnp.github.io/IPTVru/IPTVru.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_russia.m3u8",
    "https://dearbulut.github.io/iptv/playlists/country/ru.m3u",
    "https://raw.githubusercontent.com/substanc1/iptv-russia/main/streams/ru.m3u",
    "https://iptv-org.github.io/iptv/index.m3u",
    "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "https://iptv-org.github.io/iptv/regions/cis.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/countries/kz.m3u",
    "https://iptv-org.github.io/iptv/countries/kg.m3u",
    "https://iptv-org.github.io/iptv/countries/tj.m3u",
    "https://iptv-org.github.io/iptv/countries/tm.m3u",
    "https://iptv-org.github.io/iptv/countries/uz.m3u",
    "https://iptv-org.github.io/iptv/countries/am.m3u",
    "https://iptv-org.github.io/iptv/countries/az.m3u",
    "https://iptv-org.github.io/iptv/countries/ge.m3u",
    "https://iptv-org.github.io/iptv/countries/md.m3u",
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://dearbulut.github.io/iptv/playlists/best.m3u",
    "https://dearbulut.github.io/iptv/playlists/online.m3u",
    "https://smolnp.github.io/IPTVru/IPTVstable.m3u8",
    "https://smolnp.github.io/IPTVru/IPTVmir.m3u8",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/result.m3u",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv6/result.m3u",
    "https://raw.githubusercontent.com/denxvofficial/IPTV/refs/heads/main/iptv.m3u",
    "https://raw.githubusercontent.com/denxvofficial/IPTV/refs/heads/main/iptv-top.m3u",
    "https://raw.githubusercontent.com/devsground/IPTV/master/all/grouped_by_country.m3u",
    "https://raw.githubusercontent.com/devsground/IPTV/master/all/grouped_by_country_and_content.m3u",
    "https://raw.githubusercontent.com/devsground/IPTV/master/all/grouped_by_content.m3u",
    "https://github.com/MaximKiselev/iptv/raw/refs/heads/main/playlist.m3u",
    "https://gitverse.ru/api/repos/mAreXx/IPTV/raw/branch/master/iptv-playlist.m3u",
]
IPTV_ORG_PLAYLISTS = "https://raw.githubusercontent.com/iptv-org/iptv/master/PLAYLISTS.md"
EPG_SOURCES = [
    (1, "epg.one", "https://epg.one/epg2.xml.gz"),
    (2, "teleguide", "https://www.teleguide.info/download/new3/xmltv.xml.gz"),
]
EXTRA_EPG: list[str] = []

CHANNEL_DB_CSV_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/channel_database/channels.csv"
CHANNEL_DB_JSON_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/channel_database/channels.json"
CHANNEL_DB_PY_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/main/channel_database/channels_data.py"
CHANNEL_DB_MAX_BYTES = 64 * 1024 * 1024
CHANNEL_DB_MATCH_THRESHOLD = 0.72
CHANNEL_DB_ALIAS_MATCH = 0.98
CHANNEL_DB: list[dict] = []
CHANNEL_DB_BY_ID: dict[str, dict] = {}
CHANNEL_DB_BY_NAME: dict[str, list[dict]] = defaultdict(list)
CHANNEL_DB_BY_ALIAS: dict[str, list[dict]] = defaultdict(list)
CHANNEL_DB_TOKEN_INDEX: dict[str, list[dict]] = defaultdict(list)

CINERAMA_HOST_REPLACEMENTS = {
    "https://stream8.cinerama.uz": "https://stream1.cinerama.uz",
    "http://stream8.cinerama.uz": "http://stream1.cinerama.uz",
}
BAD_NAME_TOKENS = {"xxx", "porn", "porno", "pornhub", "adult", "sex", "erotic", "18+", "казино", "casino", "bet", "ставки", "букмекер"}
RU_WORDS = {
    "россия", "российский", "русский", "русская", "москва", "мск", "санкт-петербург", "петербург", "питер",
    "регион", "область", "край", "республика", "чувашия", "татарстан", "башкортостан", "сибирь", "урал",
    "кубань", "дон", "сахалин", "калининград", "новосибирск", "екатеринбург", "казань", "самара", "омск",
    "томск", "владивосток", "хабаровск", "архангельск", "мурманск", "рус", "ru", "cis", "снг", "беларусь",
    "казахстан", "кыргызстан", "узбекистан", "армения", "азербайджан", "молдова",
}
ORBIT_RE = re.compile(r"(?i)(?:\s*[\[(]?\+?(-?\d{1,2})\s*(?:h|ч)?[\])]?)\s*$")
QUALITY_RE = re.compile(r"(?i)\b(?:uhd|4k|fhd|full\s*hd|hd|sd|8k|2160p|1440p|1080p|720p|576p|480p)\b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("mega")

@dataclass
class Stream:
    url: str
    source: str = ""
    alive: Optional[bool] = None
    latency_ms: Optional[int] = None
    status: Optional[int] = None
    content_type: str = ""
    bitrate: Optional[int] = None
    checked_at: int = 0
    failures: int = 0
    successes: int = 0
    orbit: str = "+0"
    quality: str = "UNKNOWN"
    host: str = ""
    region: str = "UNK"
    operator: str = ""
    alternative_of: str = ""
    alternative_rank: int = 0
    similarity: float = 0.0
    is_alternative: bool = False

    def key(self) -> str:
        return normalize_url(self.url)

@dataclass
class Channel:
    key: str
    name: str
    original_names: list[str] = field(default_factory=list)
    tvg_id: str = ""
    tvg_name: str = ""
    logo: str = ""
    group: str = ""
    country: str = ""
    language: str = ""
    russian_priority: bool = False
    sources: set[str] = field(default_factory=set)
    streams: dict[str, Stream] = field(default_factory=dict)
    epg_source: str = ""
    epg_confidence: float = 0.0
    tvg_shift: str = ""
    base_name: str = ""
    orbit: str = "+0"
    quality: str = "UNKNOWN"
    dead_streams: dict[str, Stream] = field(default_factory=dict)
    db_id: str = ""
    db_name: str = ""
    db_aliases: list[str] = field(default_factory=list)
    db_country: str = ""
    db_language: str = ""
    db_network: str = ""
    db_owners: list[str] = field(default_factory=list)
    db_categories: list[str] = field(default_factory=list)
    db_website: str = ""
    db_match_score: float = 0.0
    db_match_type: str = ""

    def add_stream(self, stream: Stream) -> None:
        k = stream.key()
        if not k:
            return
        old = self.streams.get(k)
        if old is None:
            self.streams[k] = stream
            return
        old.source = old.source or stream.source
        if stream.alive is True and old.alive is not True:
            old.alive = True
        if stream.latency_ms is not None:
            old.latency_ms = stream.latency_ms
        if stream.status is not None:
            old.status = stream.status
        old.content_type = stream.content_type or old.content_type
        if stream.orbit != "+0" and old.orbit == "+0": old.orbit = stream.orbit
        if stream.quality != "UNKNOWN" and old.quality == "UNKNOWN": old.quality = stream.quality
        old.failures = max(old.failures, stream.failures)
        old.successes = max(old.successes, stream.successes)
        old.is_alternative = old.is_alternative or stream.is_alternative
        if not old.alternative_of: old.alternative_of = stream.alternative_of
        old.alternative_rank = old.alternative_rank or stream.alternative_rank
        old.similarity = max(old.similarity, stream.similarity)

    def stream_list(self) -> list[Stream]:
        return list(self.streams.values())

    def all_streams_including_dead(self) -> list[Stream]:
        out = list(self.streams.values())
        for k, s in self.dead_streams.items():
            if k not in self.streams:
                out.append(s)
        return out

def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", s).strip()

def normalize_name(name: str) -> str:
    s = clean_text(name).lower()
    s = re.sub(r"\([^)]*\+\d+[^)]*\)", " ", s)
    s = re.sub(r"\[[^]]*\]", " ", s)
    s = re.sub(r"\b\d{1,4}\s*[.)-]\s*", " ", s)
    s = QUALITY_RE.sub(" ", s)
    s = re.sub(r"\b(рус|russia|ru)\b", " ", s)
    s = re.sub(r"[^\w\sа-яА-ЯёЁ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_orbit(name: str) -> str:
    m = ORBIT_RE.search(str(name or "").strip())
    if not m: return "+0"
    n = int(m.group(1))
    return "+0" if n == 0 else f"+{n}" if n > 0 else str(n)

def extract_quality(name: str) -> str:
    m = QUALITY_RE.search(str(name or ""))
    if not m: return "UNKNOWN"
    q = m.group(0).upper().replace(" ", "")
    return {"FULLHD":"FHD", "1080P":"FHD", "720P":"HD", "576P":"SD", "480P":"SD", "2160P":"UHD", "4K":"UHD", "1440P":"QHD", "8K":"8K"}.get(q, q)

def base_channel_name(name: str) -> str:
    return re.sub(r"\s+", " ", QUALITY_RE.sub("", ORBIT_RE.sub("", clean_text(name)))).strip()

def quality_rank(q: str) -> int:
    return {"8K":60, "UHD":50, "4K":50, "QHD":40, "FHD":35, "HD":25, "SD":10, "UNKNOWN":0}.get((q or "UNKNOWN").upper(), 0)

def host_from_url(url: str) -> str:
    try: return urllib.parse.urlsplit(url).hostname or ""
    except Exception: return ""

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url: return ""
    try:
        p = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), re.sub(r"/{2,}", "/", p.path), p.query, ""))
    except Exception: return url

def classify_stream_meta(url: str, name: str = "", source: str = "") -> dict:
    host = host_from_url(url)
    t = f"{url} {name} {source}".lower()
    region = "UNK"
    if any(x in t for x in (".ru", "russia", "россия", "moscow", "москва", "wink", "rostelecom")): region = "RU"
    elif any(x in t for x in (".kz", "kazakh", "qazaq", "almaty", "astana")): region = "KZ"
    elif any(x in t for x in (".by", "belarus", "минск", "minsk")): region = "BY"
    elif any(x in t for x in (".uz", "uzbek", "tashkent")): region = "UZ"
    h = host.lower()
    operator = "Wink" if "wink" in h else "Rostelecom/RT" if "rostelecom" in h or re.search(r"\brt\b", h) else "Nginx" if "nginx" in h else ""
    return {"host":host, "region":region, "operator":operator}

def apply_host_rewrites(url: str) -> str:
    u = (url or "").strip()
    if not u: return u
    return re.sub(r"(?i)(https?://)stream8\.cinerama\.uz", r"\1stream1.cinerama.uz", u, count=1)

def host_rewrite_candidates(url: str) -> list[str]:
    out=[]
    for u in (apply_host_rewrites(url), url):
        if u and u not in out: out.append(u)
    return out

def russian_score(name: str, group: str, country: str, language: str, source: str) -> int:
    text = " ".join((name, group, country, language, source)).lower()
    score = 5 if re.search(r"[а-яё]", text) else 0
    if country.lower() in {"ru","russia","rus"}: score += 10
    if language.lower().startswith("ru") or language.lower() in {"rus","russian"}: score += 10
    score += sum(1 for w in RU_WORDS if w in text)
    return score

def is_bad_name(name: str) -> bool:
    low=clean_text(name).lower()
    return any(x in low for x in BAD_NAME_TOKENS)

def canonical_key(name: str, tvg_id: str = "") -> str:
    if tvg_id: return "id:" + clean_text(tvg_id).lower()
    return "name:" + normalize_name(name)

def request_bytes(url: str, timeout: int = READ_TIMEOUT, max_bytes: int = MAX_BYTES) -> bytes:
    req=urllib.request.Request(url, headers={"User-Agent":random.choice(USER_AGENT_POOL),"Accept":"*/*","Connection":"close"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        chunks=[]; total=0
        while True:
            chunk=r.read(256*1024)
            if not chunk: break
            total += len(chunk)
            if total > max_bytes: raise ValueError(f"response exceeds {max_bytes} bytes: {url}")
            chunks.append(chunk)
        data=b"".join(chunks)
        if url.lower().split("?",1)[0].endswith(".gz") or data[:2]==b"\x1f\x8b": data=gzip.decompress(data)
        return data

def fetch_text(url: str, max_bytes: int = MAX_BYTES) -> str:
    return request_bytes(url, max_bytes=max_bytes).decode("utf-8","replace")

# --------------------------- CHANNEL DATABASE -----------------------------
def _db_list(v) -> list[str]:
    if v is None: return []
    if isinstance(v,(list,tuple,set)): return [clean_text(str(x)) for x in v if clean_text(str(x))]
    if isinstance(v,str):
        t=v.strip()
        if not t: return []
        if t[:1] in "[{(":
            try:
                x=ast.literal_eval(t)
                if isinstance(x,(list,tuple,set)): return [clean_text(str(y)) for y in x if clean_text(str(y))]
            except Exception: pass
        return [clean_text(x) for x in re.split(r"[|,;]",t) if clean_text(x)]
    return [clean_text(str(v))]

def _db_field(r: dict,*names,default=""):
    for n in names:
        if n in r and r[n] not in (None,""): return r[n]
    return default

def normalize_db_record(r) -> Optional[dict]:
    if not isinstance(r,dict): return None
    rid=clean_text(str(_db_field(r,"id","channel_id","tvg_id","slug","key",default="")))
    name=clean_text(str(_db_field(r,"name","channel_name","title","display_name","tvg_name",default="")))
    aliases=_db_list(_db_field(r,"alt_names","aliases","alias","alternative_names","names",default=[]))
    if name: aliases=[x for x in aliases if normalize_name(x)!=normalize_name(name)]
    if not rid and not name: return None
    return {"id":rid,"name":name or rid,"aliases":aliases,"network":clean_text(str(_db_field(r,"network","operator",default=""))),"owners":_db_list(_db_field(r,"owners","owner",default=[])),"country":clean_text(str(_db_field(r,"country","countries",default=""))),"language":clean_text(str(_db_field(r,"language","languages",default=""))),"categories":_db_list(_db_field(r,"categories","category","genres",default=[])),"website":clean_text(str(_db_field(r,"website","site","url",default="")))}

def load_channel_db_csv(data: bytes) -> list[dict]:
    return [r for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig","replace"))) if (r:=normalize_db_record(row))]

def load_channel_db_json(data: bytes) -> list[dict]:
    try: obj=json.loads(data.decode("utf-8-sig","replace"))
    except Exception as e: log.warning("CHANNEL DB JSON parse failed: %s",e); return []
    raw=obj if isinstance(obj,list) else [obj] if isinstance(obj,dict) and any(k in obj for k in ("id","name","title","channel_name","tvg_name")) else list(obj.values()) if isinstance(obj,dict) else []
    return [r for x in raw if (r:=normalize_db_record(x))]

def load_channel_db_py(data: bytes) -> list[dict]:
    try: tree=ast.parse(data.decode("utf-8","replace"))
    except Exception as e: log.warning("CHANNEL DB PY parse failed: %s",e); return []
    out=[]
    for node in tree.body:
        value=node.value if isinstance(node,ast.AnnAssign) else node.value if isinstance(node,ast.Assign) else None
        if value is None: continue
        try: obj=ast.literal_eval(value)
        except Exception: continue
        if isinstance(obj,dict):
            if any(k in obj for k in ("id","name","title","channel_name","tvg_name")): out.append(obj)
            else: out.extend(v for v in obj.values() if isinstance(v,dict))
        elif isinstance(obj,(list,tuple,set)): out.extend(v for v in obj if isinstance(v,dict))
    return [r for x in out if (r:=normalize_db_record(x))]

def merge_db(records: list[dict]) -> list[dict]:
    merged={}
    for r in records:
        k=r["id"] or "name:"+normalize_name(r["name"])
        if k not in merged: merged[k]=dict(r); continue
        d=merged[k]
        d["aliases"]=list(dict.fromkeys(d.get("aliases",[])+r.get("aliases",[])))
        for x in ("network","country","language","website"):
            if not d.get(x): d[x]=r.get(x,"")
        for x in ("owners","categories"): d[x]=list(dict.fromkeys(d.get(x,[])+r.get(x,[])))
    return list(merged.values())

def _significant_tokens(name: str) -> set[str]: return {x for x in normalize_name(name).split() if len(x)>=ALT_INDEX_MIN_TOKEN_LEN}

def build_db_indexes(records: list[dict]) -> None:
    global CHANNEL_DB,CHANNEL_DB_BY_ID,CHANNEL_DB_BY_NAME,CHANNEL_DB_BY_ALIAS,CHANNEL_DB_TOKEN_INDEX
    CHANNEL_DB=records; CHANNEL_DB_BY_ID={}; CHANNEL_DB_BY_NAME=defaultdict(list); CHANNEL_DB_BY_ALIAS=defaultdict(list); CHANNEL_DB_TOKEN_INDEX=defaultdict(list)
    for r in records:
        if r["id"]: CHANNEL_DB_BY_ID[r["id"].lower()]=r
        for value,target in [(r["name"],CHANNEL_DB_BY_NAME)]+[(a,CHANNEL_DB_BY_ALIAS) for a in r["aliases"]]:
            k=normalize_name(value)
            if not k: continue
            target[k].append(r)
            for t in _significant_tokens(k): CHANNEL_DB_TOKEN_INDEX[t].append(r)

def load_external_channel_database() -> int:
    allr=[]
    for label,url,parser in (("CSV",CHANNEL_DB_CSV_URL,load_channel_db_csv),("JSON",CHANNEL_DB_JSON_URL,load_channel_db_json),("PY",CHANNEL_DB_PY_URL,load_channel_db_py)):
        try:
            data=request_bytes(url,max_bytes=CHANNEL_DB_MAX_BYTES); rows=parser(data); allr.extend(rows); log.info("CHANNEL DB %s: records=%d bytes=%d",label,len(rows),len(data))
        except Exception as e: log.warning("CHANNEL DB %s failed: %s",label,e)
    rows=merge_db(allr); build_db_indexes(rows); log.info("CHANNEL DB READY: %d canonical records",len(rows)); return len(rows)

def similarity(a: str,b: str) -> float:
    if not a or not b: return 0.0
    if a==b: return 1.0
    aa=set(a.split()); bb=set(b.split())
    j=len(aa&bb)/len(aa|bb) if aa and bb else 0.0
    if a in b or b in a: j=max(j,0.86)
    seq=SequenceMatcher(None,a,b).ratio()
    sa={x for x in aa if len(x)>=ALT_INDEX_MIN_TOKEN_LEN}; sb={x for x in bb if len(x)>=ALT_INDEX_MIN_TOKEN_LEN}
    sig=len(sa&sb)/len(sa|sb) if sa and sb else 0.0
    short,longer=(sa,sb) if len(sa)<=len(sb) else (sb,sa)
    if short and short<=longer: sig=max(sig,0.90)
    return max(j,seq,sig)

def database_match_channel(name: str,tvg_id: str="",original_names: Optional[list[str]]=None):
    if tvg_id:
        tid=clean_text(tvg_id).lower()
        if tid in CHANNEL_DB_BY_ID: return CHANNEL_DB_BY_ID[tid],1.0,"id"
        if tid.split("@",1)[0] in CHANNEL_DB_BY_ID: return CHANNEL_DB_BY_ID[tid.split("@",1)[0]],0.99,"id-base"
    best=None; best_score=0.0; best_type=""
    for raw in [name]+list(original_names or []):
        k=normalize_name(raw)
        if not k: continue
        if CHANNEL_DB_BY_NAME.get(k): return CHANNEL_DB_BY_NAME[k][0],1.0,"name"
        if CHANNEL_DB_BY_ALIAS.get(k): return CHANNEL_DB_BY_ALIAS[k][0],CHANNEL_DB_ALIAS_MATCH,"alias"
        candidates=[]; seen=set()
        for token in _significant_tokens(k):
            for r in CHANNEL_DB_TOKEN_INDEX.get(token,[]):
                ident=r.get("id") or r.get("name")
                if ident not in seen: seen.add(ident); candidates.append(r)
        for r in candidates[:300]:
            score=max((similarity(k,normalize_name(x)) for x in [r["name"]]+r["aliases"] if x),default=0.0)
            if score>best_score: best,best_score,best_type=r,score,"fuzzy"
    return (best,best_score,best_type) if best is not None and best_score>=CHANNEL_DB_MATCH_THRESHOLD else (None,0.0,"")

def enrich_channel_from_database(ch: Channel) -> Channel:
    r,score,typ=database_match_channel(ch.name,ch.tvg_id,ch.original_names)
    if not r: return ch
    ch.db_id=r["id"]; ch.db_name=r["name"]; ch.db_aliases=list(r["aliases"]); ch.db_country=r["country"]; ch.db_language=r["language"]; ch.db_network=r["network"]; ch.db_owners=list(r["owners"]); ch.db_categories=list(r["categories"]); ch.db_website=r["website"]; ch.db_match_score=score; ch.db_match_type=typ
    if not ch.tvg_id and ch.db_id: ch.tvg_id=ch.db_id
    if not ch.country: ch.country=ch.db_country
    if not ch.language: ch.language=ch.db_language
    if not ch.group and ch.db_categories: ch.group=ch.db_categories[0]
    if str(ch.db_country).lower() in {"ru","rus","russia"} or str(ch.db_language).lower().startswith(("ru","rus","russian")): ch.russian_priority=True
    return ch

# ------------------------------- M3U --------------------------------------
_ATTR_RE=re.compile(r'([\w-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,]+))')
def parse_attrs(line: str)->dict[str,str]:
    return {m.group(1):next((x for x in m.groups()[1:] if x is not None),"") for m in _ATTR_RE.finditer(line)}
def parse_extinf_name(line: str)->str: return line.split(",",1)[1].strip() if "," in line else ""

def parse_m3u(text: str,source_url: str):
    channels=[]; epg=[]; current=None
    lines=text.replace("\r","").split("\n")
    for line in lines[:5]:
        if line.startswith("#EXTM3U"):
            a=parse_attrs(line)
            for k in ("x-tvg-url","url-tvg","tvg-url"):
                epg += [x.strip() for x in a.get(k,"").split(",") if x.strip()]
    for raw in lines:
        line=raw.strip()
        if not line: continue
        if line.startswith("#EXTINF"):
            a=parse_attrs(line); current={"name":parse_extinf_name(line) or a.get("tvg-name") or "Unknown","tvg_id":a.get("tvg-id",""),"tvg_name":a.get("tvg-name",""),"logo":a.get("tvg-logo",""),"group":a.get("group-title",""),"country":a.get("tvg-country",""),"language":a.get("tvg-language","")}; continue
        if line.startswith("#") or current is None or not re.match(r"https?://",line,re.I): continue
        name=clean_text(current["name"])
        if not name or is_bad_name(name): current=None; continue
        orbit=extract_orbit(name); quality=extract_quality(name); base=base_channel_name(name); meta=classify_stream_meta(line,name,source_url)
        ch=Channel(key=canonical_key(name,current["tvg_id"]),name=name,original_names=[name],tvg_id=current["tvg_id"],tvg_name=current["tvg_name"] or name,logo=current["logo"],group=current["group"],country=current["country"],language=current["language"],russian_priority=russian_score(name,current["group"],current["country"],current["language"],source_url)>=6,sources={source_url},base_name=base or name,orbit=orbit,quality=quality)
        for idx,u in enumerate(host_rewrite_candidates(line)):
            m=classify_stream_meta(u,name,source_url); ch.add_stream(Stream(url=u,source=source_url,orbit=orbit,quality=quality,host=m["host"] or meta["host"],region=m["region"],operator=m["operator"],is_alternative=idx>0,alternative_of=name if idx else "",alternative_rank=idx))
        channels.append(ch); current=None
    return channels,epg

def discover_iptv_org_playlists():
    try: text=fetch_text(IPTV_ORG_PLAYLISTS,max_bytes=15*1024*1024)
    except Exception as e: log.warning("iptv-org playlist index failed: %s",e); return []
    return sorted(set(re.findall(r"https://iptv-org\.github\.io/iptv/[^`\s)]+\.m3u",text)))

def load_sources_file(path: Optional[str]):
    if not path: return []
    p=Path(path)
    if not p.exists(): log.warning("sources file not found: %s",p); return []
    return [x.strip() for x in p.read_text(encoding="utf-8",errors="replace").splitlines() if x.strip() and not x.lstrip().startswith("#") and re.match(r"https?://",x.strip())]

def merge_channel(dst: Channel,src: Channel):
    for n in src.original_names:
        if n not in dst.original_names: dst.original_names.append(n)
    for attr in ("tvg_id","tvg_name","logo","group","country","language","base_name"):
        if not getattr(dst,attr) and getattr(src,attr): setattr(dst,attr,getattr(src,attr))
    dst.russian_priority |= src.russian_priority; dst.sources.update(src.sources)
    if quality_rank(src.quality)>quality_rank(dst.quality): dst.quality=src.quality
    if src.orbit!="+0" and dst.orbit=="+0": dst.orbit=src.orbit
    if src.db_id and not dst.db_id: dst.db_id=src.db_id
    if src.db_name and not dst.db_name: dst.db_name=src.db_name
    dst.db_aliases=list(dict.fromkeys(dst.db_aliases+src.db_aliases)); dst.db_owners=list(dict.fromkeys(dst.db_owners+src.db_owners)); dst.db_categories=list(dict.fromkeys(dst.db_categories+src.db_categories))
    for a in ("db_country","db_language","db_network","db_website"):
        if not getattr(dst,a): setattr(dst,a,getattr(src,a))
    if src.db_match_score>dst.db_match_score: dst.db_match_score=src.db_match_score; dst.db_match_type=src.db_match_type
    for s in src.streams.values(): dst.add_stream(s)

def find_channel(channels: dict[str,Channel],incoming: Channel):
    enrich_channel_from_database(incoming)
    if incoming.db_id:
        for c in channels.values():
            if c.db_id==incoming.db_id: return c
    if incoming.tvg_id and canonical_key(incoming.name,incoming.tvg_id) in channels: return channels[canonical_key(incoming.name,incoming.tvg_id)]
    key=canonical_key(incoming.name)
    if key in channels: return channels[key]
    toks=normalize_name(incoming.name).split(); prefix=" ".join(toks[:2])
    best=None; bs=0.0
    for c in channels.values():
        if prefix and not normalize_name(c.name).startswith(prefix): continue
        sc=similarity(normalize_name(incoming.name),normalize_name(c.name))
        if sc>bs: best,bs=c,sc
    return best if bs>=0.90 else None

def aggregate(parsed):
    channels={}; epgs=[]
    for source,items,se in parsed:
        epgs.extend(se)
        for incoming in items:
            found=find_channel(channels,incoming)
            if found is None: channels[incoming.key]=incoming
            else: merge_channel(found,incoming)
    for c in channels.values(): enrich_channel_from_database(c)
    return channels,list(dict.fromkeys(epgs))

# --------------------------- stream health --------------------------------
def init_db():
    con=sqlite3.connect(DB,check_same_thread=False); con.execute("PRAGMA journal_mode=WAL"); con.execute("CREATE TABLE IF NOT EXISTS stream_health(url TEXT PRIMARY KEY,checked INTEGER NOT NULL,alive INTEGER NOT NULL,status INTEGER,latency_ms INTEGER,content_type TEXT,successes INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0)"); con.commit(); return con

def cached_health(con,url):
    row=con.execute("SELECT url,checked,alive,status,latency_ms,content_type,successes,failures FROM stream_health WHERE url=?",(url,)).fetchone()
    if not row or int(time.time())-row[1]>CACHE_TTL: return None
    return dict(zip(("url","checked","alive","status","latency_ms","content_type","successes","failures"),row))

def probe(url):
    start=time.monotonic(); req=urllib.request.Request(url,headers={"User-Agent":random.choice(USER_AGENT_POOL),"Accept":"*/*","Connection":"close","Cache-Control":"no-cache"})
    with urllib.request.urlopen(req,timeout=READ_TIMEOUT) as r:
        status=getattr(r,"status",200); ct=r.headers.get("Content-Type",""); prefix=r.read(4096)
    if not prefix: raise IOError("empty response")
    return 200<=status<400,status,ct,int((time.monotonic()-start)*1000)

def check_stream(s: Stream):
    last=None
    for cand in host_rewrite_candidates(s.url):
        for attempt in range(STREAM_CHECK_RETRIES+1):
            try:
                alive,status,ct,lat=probe(cand); s.alive=alive; s.status=status; s.content_type=ct; s.latency_ms=lat; s.checked_at=int(time.time())
                if alive and cand!=s.url: s.url=cand
                if alive: s.successes+=1
                else: s.failures+=1
                m=classify_stream_meta(s.url,"",s.source); s.host=m["host"]; s.region=m["region"] if s.region=="UNK" else s.region; s.operator=s.operator or m["operator"]
                return s
            except Exception as e:
                last=e
                if attempt<STREAM_CHECK_RETRIES: time.sleep(0.15*(attempt+1))
                else: break
    s.alive=False; s.status=None; s.latency_ms=None; s.checked_at=int(time.time()); s.failures+=1
    return s

def stream_score(s):
    if s.alive is False: return -1000.0
    score=100 if s.alive is True else 0
    if s.latency_ms is not None: score+=max(0,40-s.latency_ms/100)
    if "m3u8" in s.content_type.lower() or "mpegurl" in s.content_type.lower(): score+=10
    score+=min(s.successes*2,20)-min(s.failures*5,30)+quality_rank(s.quality)*0.4
    if s.orbit=="+0": score+=2
    elif s.orbit in ("+1","-1"): score+=1
    if s.is_alternative and s.alive is True: score+=3+min(s.similarity,1)*2
    return score

def validate_streams(channels,workers,enabled):
    if not enabled: log.info("STREAM CHECK: disabled"); return
    con=init_db(); all_s=[s for c in channels.values() for s in c.streams.values()]; todo=[]
    for s in all_s:
        h=cached_health(con,s.key())
        if h: s.alive=bool(h["alive"]); s.status=h["status"]; s.latency_ms=h["latency_ms"]; s.content_type=h["content_type"] or ""; s.checked_at=h["checked"]; s.successes=h["successes"]; s.failures=h["failures"]
        else: todo.append(s)
    log.info("STREAM CHECK: unique=%d cache_hit=%d network=%d workers=%d",len(all_s),len(all_s)-len(todo),len(todo),workers)
    if todo:
        with cf.ThreadPoolExecutor(max_workers=max(1,workers)) as ex:
            for i,_ in enumerate(ex.map(check_stream,todo),1):
                if i%1000==0: log.info("STREAM CHECK: %d/%d",i,len(todo))
    con.executemany("INSERT OR REPLACE INTO stream_health(url,checked,alive,status,latency_ms,content_type,successes,failures) VALUES(?,?,?,?,?,?,?,?)",[(s.key(),s.checked_at or int(time.time()),int(bool(s.alive)),s.status,s.latency_ms,s.content_type,s.successes,s.failures) for s in all_s]); con.commit(); con.close()

def preserve_dead_streams(channels):
    for c in channels.values():
        for k,s in c.streams.items():
            if s.alive is False: c.dead_streams.setdefault(k,s)

# --------------------------- alternatives ---------------------------------
def build_alt_token_index(pool):
    idx=defaultdict(list)
    for c in pool:
        names=[c.db_name or c.base_name or c.name]+c.db_aliases
        for name in names:
            toks=_significant_tokens(name)
            for t in toks: idx[t].append(c)
    return idx

def find_alternatives_for_channel(ch,pool,min_similarity,target,index):
    target_name=normalize_name(ch.db_name or ch.base_name or ch.name)
    existing={normalize_url(s.url) for s in ch.stream_list()}; existing.update(normalize_url(s.url) for s in ch.dead_streams.values())
    hosts={(s.host or host_from_url(s.url)).lower() for s in ch.stream_list() if s.alive is not False}
    candidates=[]; seen=set()
    toks=_significant_tokens(ch.db_name or ch.base_name or ch.name)
    for t in toks:
        for o in index.get(t,[]):
            if o.key!=ch.key and o.key not in seen: seen.add(o.key); candidates.append(o)
    if len(candidates)<8:
        for o in pool:
            if o.key!=ch.key and o.key not in seen: seen.add(o.key); candidates.append(o)
            if len(candidates)>=400: break
    scored=[]
    ch_alias={normalize_name(x) for x in ch.db_aliases}
    for o in candidates:
        other=normalize_name(o.db_name or o.base_name or o.name); sim=similarity(target_name,other)
        if ch.db_id and o.db_id and ch.db_id==o.db_id: sim=1.0
        elif ch.db_name and o.db_name and normalize_name(ch.db_name)==normalize_name(o.db_name): sim=max(sim,0.98)
        elif ch_alias & {normalize_name(x) for x in o.db_aliases}: sim=max(sim,0.96)
        if other==target_name: sim=max(sim,0.95)
        if o.orbit!=ch.orbit and other==target_name: sim=min(1,sim+0.08)
        if o.quality!=ch.quality and o.quality!="UNKNOWN" and other==target_name: sim=min(1,sim+0.05)
        if sim<min_similarity: continue
        for s in o.stream_list():
            if s.alive is False: continue
            u=normalize_url(s.url)
            if not u or u in existing: continue
            host=(s.host or host_from_url(s.url)).lower(); diversity=ALT_DIVERSITY_BONUS if host and host not in hosts else 0
            scored.append((sim*100+diversity+stream_score(s),sim,s,o.name))
    scored.sort(key=lambda x:x[0],reverse=True); added=[]; used=set()
    for rank,sim,s,oname in scored:
        u=normalize_url(s.url)
        if u in existing: continue
        host=(s.host or host_from_url(s.url)).lower(); d=(s.region,host,s.operator)
        if d in used and len(added)<target: continue
        used.add(d); existing.add(u)
        a=Stream(url=s.url,source=s.source,alive=s.alive,latency_ms=s.latency_ms,status=s.status,content_type=s.content_type,bitrate=s.bitrate,checked_at=s.checked_at,failures=s.failures,successes=s.successes,orbit=s.orbit or extract_orbit(oname),quality=s.quality or extract_quality(oname),host=s.host or host_from_url(s.url),region=s.region,operator=s.operator,alternative_of=ch.name,alternative_rank=len(added)+1,similarity=sim,is_alternative=True)
        ch.add_stream(a); added.append(a)
        if sum(1 for x in ch.stream_list() if x.alive is True)>=target: break
    if sum(1 for x in ch.stream_list() if x.alive is True)<target:
        for _,sim,s,oname in scored:
            u=normalize_url(s.url)
            if u in existing: continue
            existing.add(u); a=Stream(url=s.url,source=s.source,alive=s.alive,latency_ms=s.latency_ms,status=s.status,content_type=s.content_type,checked_at=s.checked_at,failures=s.failures,successes=s.successes,orbit=s.orbit or extract_orbit(oname),quality=s.quality or extract_quality(oname),host=s.host or host_from_url(s.url),region=s.region,operator=s.operator,alternative_of=ch.name,alternative_rank=len(added)+1,similarity=sim,is_alternative=True); ch.add_stream(a); added.append(a)
            if sum(1 for x in ch.stream_list() if x.alive is True)>=target: break
    return added

def expand_alternatives(channels,min_similarity,target):
    pool=list(channels.values()); idx=build_alt_token_index(pool); total=0
    for c in pool:
        if sum(1 for s in c.stream_list() if s.alive is True)>=target: continue
        total += len(find_alternatives_for_channel(c,pool,min_similarity,target,idx))
    log.info("ALTERNATIVES EXPANSION: +%d streams",total); return total

# ------------------------------- EPG --------------------------------------
def load_xmltv(url):
    try: root=ET.fromstring(request_bytes(url,max_bytes=150*1024*1024))
    except Exception as e: log.warning("EPG failed %s: %s",url,e); return {}
    out={}
    for c in root.findall("channel"):
        cid=c.attrib.get("id","").strip(); names=[clean_text(x.text or "") for x in c.findall("display-name") if (x.text or "").strip()]
        if cid: out[cid]={"id":cid,"names":names,"logo":(c.find("icon").attrib.get("src","") if c.find("icon") is not None else "")}
    return out

def epg_match(channels,epg_sets):
    for c in channels.values():
        best=None; score=0
        for source,epg in epg_sets:
            if c.tvg_id and c.tvg_id in epg: best=(source,epg[c.tvg_id],1); break
            for item in epg.values():
                for n in item["names"]:
                    s=similarity(normalize_name(c.db_name or c.name),normalize_name(n))
                    if s>score: score=s; best=(source,item,s)
        if best and best[2]>=0.82:
            c.tvg_id=best[1]["id"]; c.epg_source=best[0]; c.epg_confidence=best[2]; c.logo=c.logo or best[1].get("logo","")

# ------------------------------ outputs -----------------------------------
def m3u_attr(s): return clean_text(s).replace('"',"'")
def write_m3u(channels,path,all_streams,min_streams=0,include_dead=False):
    lines=["#EXTM3U"]; count=0
    for c in channels:
        streams=sorted(c.all_streams_including_dead() if include_dead else c.stream_list(),key=stream_score,reverse=True)
        if not all_streams: streams=( [s for s in streams if s.alive is not False] or streams)[:1]
        if not streams: continue
        alive=sum(1 for s in streams if s.alive is not False)
        if min_streams and alive<min_streams: continue
        for rank,s in enumerate(streams,1):
            orbit=s.orbit or c.orbit or "+0"; quality=s.quality or c.quality or "UNKNOWN"; suffix=[]
            if all_streams and orbit not in c.name: suffix.append(orbit)
            if quality!="UNKNOWN" and quality.lower() not in c.name.lower(): suffix.append(quality)
            if rank>1: suffix.append(f"ALT {rank}")
            if s.alive is False: suffix.append("OFFLINE")
            attrs=[f'tvg-id="{m3u_attr(c.tvg_id)}"' if c.tvg_id else "",f'tvg-name="{m3u_attr(c.tvg_name or c.name)}"',f'tvg-logo="{m3u_attr(c.logo)}"' if c.logo else "",f'group-title="{m3u_attr(c.group or ("Россия" if c.russian_priority else "IPTV"))}"',f'stream-rank="{rank}"',f'backup-count="{max(0,len(streams)-1)}"',f'orbit="{m3u_attr(orbit)}"',f'quality="{m3u_attr(quality)}"']
            if s.is_alternative: attrs.append('x-alternative="1"')
            if s.alive is False: attrs.append('x-offline="1"')
            lines.append(f'#EXTINF:-1 {" ".join(x for x in attrs if x)},{m3u_attr(c.name)}' + (" ["+"] [".join(suffix)+"]" if suffix else "")); lines.append(s.url); count+=1
    path.write_text("\n".join(lines)+"\n",encoding="utf-8"); log.info("OUTPUT: %s bytes=%d entries=%d",path,path.stat().st_size,count); return count

def write_json(channels,path):
    data=[]
    for c in channels:
        d={"key":c.key,"name":c.name,"original_names":c.original_names,"tvg_id":c.tvg_id,"tvg_name":c.tvg_name,"logo":c.logo,"group":c.group,"country":c.country,"language":c.language,"russian_priority":c.russian_priority,"epg_source":c.epg_source,"epg_confidence":c.epg_confidence,"db_id":c.db_id,"db_name":c.db_name,"db_aliases":c.db_aliases,"db_country":c.db_country,"db_language":c.db_language,"db_network":c.db_network,"db_owners":c.db_owners,"db_categories":c.db_categories,"db_website":c.db_website,"db_match_score":c.db_match_score,"db_match_type":c.db_match_type,"stream_count":len(c.streams),"alive_stream_count":sum(1 for s in c.streams.values() if s.alive),"streams":[asdict(s) for s in sorted(c.streams.values(),key=stream_score,reverse=True)],"sources":sorted(c.sources)}
        data.append(d)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

def write_jsonl(channels,path):
    with path.open("w",encoding="utf-8") as f:
        for c in channels: f.write(json.dumps({"key":c.key,"name":c.name,"tvg_id":c.tvg_id,"db_id":c.db_id,"db_name":c.db_name,"db_aliases":c.db_aliases,"db_match_score":c.db_match_score,"db_match_type":c.db_match_type,"streams":[asdict(s) for s in sorted(c.streams.values(),key=stream_score,reverse=True)]},ensure_ascii=False)+"\n")

def write_txt(channels,path):
    with path.open("w",encoding="utf-8") as f:
        for c in channels:
            ss=sorted(c.streams.values(),key=stream_score,reverse=True); f.write(f"{c.name} | EPG={c.tvg_id or '-'} | streams={len(ss)} | alive={sum(1 for s in ss if s.alive)}\n"); [f.write(f" {i:03d}. {s.url}\n") for i,s in enumerate(ss,1)]

def write_stats(channels,source_count,epg_count,path):
    stats={"channels":len(channels),"russian_channels":sum(c.russian_priority for c in channels),"target_channels":TARGET_CHANNELS,"target_russian_channels":TARGET_RU,"stream_urls":sum(len(c.streams) for c in channels),"alive_stream_urls":sum(sum(s.alive is True for s in c.streams.values()) for c in channels),"channels_with_12plus_pool":sum(sum(s.alive is not False for s in c.streams.values())>=MIN_ALTERNATIVES for c in channels),"channels_with_12plus_alive":sum(sum(s.alive is True for s in c.streams.values())>=MIN_ALTERNATIVES for c in channels),"alternative_streams":sum(s.is_alternative for c in channels for s in c.streams.values()),"dead_streams_preserved":sum(len(c.dead_streams) for c in channels),"sources":source_count,"epg_sources":epg_count,"channel_database_records":len(CHANNEL_DB),"channels_matched_to_database":sum(bool(c.db_id) for c in channels),"channels_matched_by_id":sum(c.db_match_type in {"id","id-base"} for c in channels),"channels_matched_by_name":sum(c.db_match_type=="name" for c in channels),"channels_matched_by_alias":sum(c.db_match_type=="alias" for c in channels),"channels_matched_by_fuzzy":sum(c.db_match_type=="fuzzy" for c in channels),"channels_with_epg":sum(bool(c.tvg_id) for c in channels),"epg_matched_by_epg_one":sum(c.epg_source=="epg.one" for c in channels),"epg_matched_by_teleguide":sum(c.epg_source=="teleguide" for c in channels),"generated_at":int(time.time())}
    path.write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8"); return stats

# --------------------------- strict stable --------------------------------
def load_stable_state():
    try:
        x=json.loads(STABLE_STATE_DB.read_text(encoding="utf-8")); return x if isinstance(x,dict) else {}
    except Exception: return {}

def save_stable_state(m): STABLE_STATE_DB.write_text(json.dumps({k:v for k,v in m.items() if v},ensure_ascii=False,indent=2),encoding="utf-8")
def stable_ok(s): return s.alive is True and s.status==200 and s.latency_ms is not None and s.latency_ms<STABLE_LATENCY_THRESHOLD_MS and bool(s.url)

def select_stable(c,state):
    last=state.get(c.key); candidates=[]
    if last:
        candidates += [s for s in c.stream_list() if normalize_url(s.url)==normalize_url(last)]
    candidates += sorted([s for s in c.stream_list() if normalize_url(s.url)!=normalize_url(last)],key=stream_score,reverse=True)
    for s in candidates:
        t=check_stream(Stream(url=s.url))
        if stable_ok(t): state[c.key]=t.url; return t
    state[c.key]=""; return None

def write_stable(channels,path,state):
    lines=["#EXTM3U"]; count=0
    for c in channels:
        if not state.get(c.key): continue
        s=select_stable(c,state)
        if not s: continue
        attrs=[f'tvg-id="{m3u_attr(c.tvg_id)}"' if c.tvg_id else "",f'tvg-name="{m3u_attr(c.tvg_name or c.name)}"',f'tvg-logo="{m3u_attr(c.logo)}"' if c.logo else "",f'group-title="{m3u_attr(c.group or ("Россия" if c.russian_priority else "IPTV"))}"']
        lines += [f'#EXTINF:-1 {" ".join(x for x in attrs if x)},{m3u_attr(c.name)}',s.url]; count+=1
    path.write_text("\n".join(lines)+"\n",encoding="utf-8"); return count

def parse_args():
    p=argparse.ArgumentParser(description="RU IPTV MEGA PARSER + canonical channel database")
    p.add_argument("--sources-file"); p.add_argument("--workers",type=int,default=CHECK_WORKERS); p.add_argument("--fetch-workers",type=int,default=FETCH_WORKERS); p.add_argument("--max-channels",type=int,default=0); p.add_argument("--no-check",action="store_true"); p.add_argument("--no-iptv-org-expand",action="store_true"); p.add_argument("--min-alternatives",type=int,default=TARGET_ALTERNATIVES); p.add_argument("--similarity",type=float,default=ALT_SIMILARITY_THRESHOLD); p.add_argument("--no-alternatives",action="store_true"); return p.parse_args()

def main():
    args=parse_args(); db_count=load_external_channel_database(); log.info("EXTERNAL CHANNEL DATABASE: %d records",db_count)
    sources=list(BASE_SOURCES)+load_sources_file(args.sources_file)
    if not args.no_iptv_org_expand: sources += discover_iptv_org_playlists()
    sources=list(dict.fromkeys(normalize_url(x) for x in sources if x)); log.info("SOURCES: %d",len(sources))
    parsed=[]
    def fetch_parse(u):
        try: t=fetch_text(u); items,epgs=parse_m3u(t,u); return u,items,epgs,None
        except Exception as e: return u,[],[],repr(e)
    with cf.ThreadPoolExecutor(max_workers=max(1,args.fetch_workers)) as ex:
        for u,items,epgs,err in ex.map(fetch_parse,sources):
            if err: log.warning("SOURCE FAIL %s :: %s",u,err); continue
            parsed.append((u,items,epgs)); log.info("SOURCE: %s records=%d epg=%d",u,len(items),len(epgs))
    channels,embedded_epg=aggregate(parsed); log.info("CHANNELS AFTER MERGE: %d",len(channels))
    if args.max_channels and len(channels)>args.max_channels:
        ordered=sorted(channels.values(),key=lambda c:(not c.russian_priority,-len(c.streams),c.name))[:args.max_channels]; channels={c.key:c for c in ordered}
    epg_sets=[]
    for _,name,url in EPG_SOURCES:
        e=load_xmltv(url)
        if e: epg_sets.append((name,e))
    for u in embedded_epg[:30]:
        if any(u==x[2] for x in EPG_SOURCES): continue
        e=load_xmltv(u)
        if e: epg_sets.append((u,e))
    epg_match(channels,epg_sets)
    validate_streams(channels,max(1,args.workers),not args.no_check)
    preserve_dead_streams(channels)
    if not args.no_alternatives: expand_alternatives(channels,float(args.similarity),max(1,args.min_alternatives))
    channel_list=sorted(channels.values(),key=lambda c:(not c.russian_priority,-len(c.streams),-sum(s.alive is True for s in c.streams.values()),normalize_name(c.name)))
    write_m3u(channel_list,OUT/"mega_best.m3u",False)
    write_m3u(channel_list,OUT/"mega_all_streams.m3u",True)
    write_m3u(channel_list,OUT/"mega_12plus.m3u",True,MIN_ALTERNATIVES)
    write_m3u(channel_list,OUT/"mega_all_including_dead.m3u",True,include_dead=True)
    write_m3u(channel_list,OUT/"mega_with_alts.m3u",True)
    write_m3u(channel_list,OUT/"mega_orbits.m3u",True)
    ru=[c for c in channel_list if c.russian_priority]
    write_m3u(ru,OUT/"mega_russia.m3u",False)
    write_m3u(ru,OUT/"mega_russia_12plus.m3u",True,MIN_ALTERNATIVES)
    write_m3u(ru,OUT/"mega_russia_with_alts.m3u",True)
    write_m3u(ru,OUT/"mega_russia_all.m3u",True)
    state=load_stable_state()
    # Bootstrap only when no saved state exists; subsequent runs are strict.
    if not any(state.values()):
        for c in channel_list:
            for s in sorted(c.stream_list(),key=stream_score,reverse=True):
                if stable_ok(s): state[c.key]=s.url; break
    stable_count=write_stable(channel_list,OUT/"Stable_Ru_IPTV.m3u",state); save_stable_state(state)
    write_json(channel_list,OUT/"mega_channels.json"); write_jsonl(channel_list,OUT/"mega_channels.jsonl"); write_txt(channel_list,OUT/"mega_channels.txt")
    stats=write_stats(channel_list,len(parsed),len(epg_sets),OUT/"statistics.json")
    (OUT/"statistics.txt").write_text("\n".join(["RU IPTV MEGA PARSER (ULTRA-EXPANDED)","="*60,f"Channels: {stats['channels']}",f"Russian/CIS priority: {stats['russian_channels']}",f"Stream URLs: {stats['stream_urls']}",f"Alive stream URLs: {stats['alive_stream_urls']}",f"Alternative streams attached: {stats['alternative_streams']}",f"Dead streams preserved: {stats['dead_streams_preserved']}",f"Channels with >=12 alive: {stats['channels_with_12plus_alive']}",f"Channel database records: {stats['channel_database_records']}",f"Channels matched to database: {stats['channels_matched_to_database']}",f"DB ID matches: {stats['channels_matched_by_id']}",f"DB name matches: {stats['channels_matched_by_name']}",f"DB alias matches: {stats['channels_matched_by_alias']}",f"DB fuzzy matches: {stats['channels_matched_by_fuzzy']}",f"Stable: {stable_count}","Policy: non-working streams are preserved; URLs are never invented."])+'\n',encoding="utf-8")
    log.info("FINISHED | channels=%d RU=%d streams=%d alive=%d DB=%d matched=%d Stable=%d",stats['channels'],stats['russian_channels'],stats['stream_urls'],stats['alive_stream_urls'],stats['channel_database_records'],stats['channels_matched_to_database'],stable_count)
    return 0

if __name__=="__main__": raise SystemExit(main())
