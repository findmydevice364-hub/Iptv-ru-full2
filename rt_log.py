import csv
import re
from pathlib import Path

import requests


PLAYLIST_URLS = [
    "https://gitverse.ru/api/repos/radio641/tv/raw/branch/master/rossteleccom.net.m3u",
    "https://raw.githubusercontent.com/radio641/tv/master/rossteleccom.net.m3u",
]


def fetch_playlist(urls: list[str]) -> str:
    """Пытается загрузить M3U из списка URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    last_error = None
    for url in urls:
        try:
            resp = requests.get(url, timeout=20, headers=headers)
            resp.raise_for_status()
            text = resp.text
            if "#EXTM3U" in text[:200]:
                return text
            last_error = ValueError(f"URL {url} вернул не M3U: {text[:120]!r}")
        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("Не удалось получить M3U-плейлист")


def make_aliases(name: str) -> list[str]:
    """Генерирует варианты названия одного канала для сопоставления."""
    n = (name or "").strip()
    if not n:
        return []

    aliases = {n}

    # Удаляем типичный мусор: (HD), (4K), (UHD), |, кавычки и лишние пробелы
    base = re.sub(r"\s*[\(\[\|]\s*(HD|4K|UHD|FHD)\s*[\)\]\|]?\s*", " ", n, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip(" |«»\"'")
    if base:
        aliases.add(base)

    # Разные написания одного и того же
    upper_base = base.upper()
    if "MATCH" in upper_base:
        aliases.add(base.replace("MATCH", "Матч"))
        aliases.add(base.replace("MATCH!", "Матч"))
    if "Матч" in base:
        aliases.add(base.replace("Матч", "MATCH!"))
        aliases.add(base.replace("Матч", "MATCH"))

    # Если есть HD — вариант без него, и наоборот
    if " HD" in base:
        aliases.add(base.replace(" HD", ""))
    else:
        aliases.add(base + " HD")

    # Убираем лишний мусор и пустые строки
    cleaned = []
    for a in aliases:
        a = a.strip().strip(" |«»\"'")
        if a:
            cleaned.append(a)

    # Сортируем и удаляем дубликаты
    return sorted(set(cleaned))


def parse_m3u(text: str) -> list[dict]:
    """Парсит M3U и возвращает список каналов с алиасами."""
    channels = []
    seen_names = set()
    current_name = None

    for raw in text.splitlines():
        line = raw.strip()

        if line.startswith("#EXTINF:"):
            parts = line.split(",", 1)
            current_name = parts[1].strip() if len(parts) > 1 else None
            continue

        if not line or line.startswith("#"):
            continue

        if not current_name:
            continue

        if current_name in seen_names:
            current_name = None
            continue

        seen_names.add(current_name)
        channels.append({
            "name": current_name,
            "url": line,
            "aliases": make_aliases(current_name),
        })
        current_name = None

    return channels


def save_txt_blocks(channels: list[dict], filename: str = "channels.txt"):
    """TXT с отдельными блоками [NAMES] и [ALIASES]."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write("[NAMES]\n")
        for ch in channels:
            f.write(ch["name"] + "\n")

        f.write("\n[ALIASES]\n")
        for ch in channels:
            for alias in ch["aliases"]:
                if alias != ch["name"]:
                    f.write(f"{alias} → {ch['name']}\n")


def save_py_module(channels: list[dict], filename: str = "channels_data.py"):
    """Python-модуль: from channels_data import CHANNELS, ALIASES."""
    channel_names = [ch["name"] for ch in channels]
    alias_map = {}

    for ch in channels:
        for alias in ch["aliases"]:
            if alias != ch["name"]:
                alias_map[alias] = ch["name"]

    with open(filename, "w", encoding="utf-8") as f:
        f.write("# Автогенерация, не редактировать вручную\n")
        f.write("CHANNELS = ")
        f.write(repr(channel_names))
        f.write("\n\n# алиас -> каноническое имя (для поиска/сопоставления)\n")
        f.write("ALIASES = ")
        f.write(repr(alias_map))
        f.write("\n")


def save_csv(channels: list[dict], filename: str = "channels.csv"):
    """CSV: name,url,canonical_name,alias. Удобно для pandas и анализа."""
    rows = []

    for ch in channels:
        rows.append({
            "name": ch["name"],
            "url": ch["url"],
            "canonical_name": ch["name"],
            "alias": ""
        })

        for alias in ch["aliases"]:
            if alias != ch["name"]:
                rows.append({
                    "name": alias,
                    "url": "",
                    "canonical_name": ch["name"],
                    "alias": alias
                })

    fieldnames = ["name", "url", "canonical_name", "alias"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    text = fetch_playlist(PLAYLIST_URLS)
    channels = parse_m3u(text)

    print(f"Найдено каналов: {len(channels)}")

    save_txt_blocks(channels, "channels.txt")
    save_py_module(channels, "channels_data.py")
    save_csv(channels, "channels.csv")

    print("Готово: channels.txt, channels_data.py, channels.csv созданы.")


if __name__ == "__main__":
    main()