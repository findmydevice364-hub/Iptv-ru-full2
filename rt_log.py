import re
import requests
import csv
from pathlib import Path

PLAYLIST_URL = "https://gitverse.ru/api/repos/radio641/tv/raw/branch/master/rossteleccom.net.m3u"

def make_aliases(name: str) -> list[str]:
    """Генерирует варианты названия одного канала для сопоставления."""
    n = name.strip()
    aliases = {n}

    # Чистим типичный мусор: (HD), (4K), |, «», кавычки, лишние пробелы
    base = re.sub(r"\s*[\(\[\|]\s*(HD|4K|UHD|FHD)\s*[\)\]\|]?\s*", " ", n, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip(" |«»\"'")
    aliases.add(base)

    # Разные написания одного и того же
    if "MATCH" in base.upper():
        aliases.add(base.replace("MATCH", "Матч"))
        aliases.add(base.replace("MATCH!", "Матч"))
    if "Матч" in base:
        aliases.add(base.replace("Матч", "MATCH!"))

    # Если есть HD — вариант без него, и наоборот
    if " HD" in base:
        aliases.add(base.replace(" HD", ""))
    else:
        aliases.add(base + " HD")

    return sorted(a for a in aliases if a)

def parse_m3u(text: str) -> list[dict]:
    channels, seen = [], set()
    cur_name = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            parts = line.split(",", 1)
            cur_name = parts[1].strip() if len(parts) > 1 else None
        elif line and not line.startswith("#"):
            if cur_name and cur_name not in seen:
                seen.add(cur_name)
                channels.append({
                    "name": cur_name,
                    "url": line,
                    "aliases": make_aliases(cur_name)
                })
            cur_name = None
    return channels

def save_txt_blocks(channels: list[dict], filename: str = "channels.txt"):
    """TXT с отдельными блоками [NAMES] и [ALIASES]."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write("[NAMES]\n")
        for ch in channels:
            f.write(ch["name"] + "\n")

        f.write("\n[ALIASES]\n")
        for ch in channels:
            for a in ch["aliases"]:
                if a != ch["name"]:
                    f.write(f"{a} → {ch['name']}\n")

def save_py_module(channels: list[dict], filename: str = "channels_data.py"):
    """Python-модуль: from channels_data import CHANNELS, ALIASES."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write("# Автогенерация, не редактировать вручную\n")
        f.write("CHANNELS = ")
        f.write(repr([ch["name"] for ch in channels]))
        f.write("\n\n# алиас -> каноническое имя (для поиска/сопоставления)\n")
        f.write("ALIASES = ")
        f.write(repr({a: ch["name"]
                      for ch in channels for a in ch["aliases"]}))
        f.write("\n")

def save_csv(channels: list[dict], filename: str = "channels.csv"):
    """CSV: name,url,canonical_name,alias. Удобно для pandas и анализа."""
    rows = []
    for ch in channels:
        # Сначала пишем строку с основным именем
        rows.append({
            "name": ch["name"],
            "url": ch["url"],
            "canonical_name": ch["name"],
            "alias": ""
        })
        # Потом строки с алиасами (чтобы в CSV было видно все варианты)
        for a in ch["aliases"]:
            if a != ch["name"]:
                rows.append({
                    "name": a,
                    "url": "",
                    "canonical_name": ch["name"],
                    "alias": a
                })
    
    fieldnames = ["name", "url", "canonical_name", "alias"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

# --- Запуск ---
resp = requests.get(PLAYLIST_URL, timeout=15)
resp.raise_for_status()
if "#EXTM3U" not in resp.text[:200]:
    raise ValueError("Сервер вернул не M3U — нужна RAW-ссылка.")

channels = parse_m3u(resp.text)
print(f"Найдено каналов: {len(channels)}")

save_txt_blocks(channels)      # -> channels.txt
save_py_module(channels)       # -> channels_data.py
save_csv(channels)             # -> channels.csv

print("Готово: channels.txt, channels_data.py, channels.csv созданы.")
