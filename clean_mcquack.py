#!/usr/bin/env python3
"""Преобразование потоков McQuack в IPTV-плейлисте."""

import re
import sys
import urllib.request
from pathlib import Path

PLAYLIST_URL = "https://raw.githubusercontent.com/findmydevice364-hub/Iptv-ru-full2/refs/heads/main/output/all.m3u"

DOWNLOADED_PLAYLIST = Path("output/all.m3u")
OUTPUT_CLEAN = Path("output/all_clean.m3u")
OUTPUT_ROOT = Path("all_clean.m3u")

PATTERN = re.compile(
    r"http://stream\.mcquack\.net/(\d+)/index\.m3u8"
)

def clean_playlist(text: str) -> tuple[str, int]:
    """Заменяет исходные ссылки и возвращает число замен."""
    replacement = (
        r"http://stream.mcquack.net/\1/"
        r"tracks-v1a1/mono.ts.m3u8"
        r"?ads=0&filter.tracks=v1a1"
    )
    return PATTERN.subn(replacement, text)


def main() -> int:
    # Убедимся, что папка output существует
    DOWNLOADED_PLAYLIST.parent.mkdir(parents=True, exist_ok=True)

    print(f"Скачивание плейлиста с URL: {PLAYLIST_URL}")
    try:
        with urllib.request.urlopen(PLAYLIST_URL) as response:
            original = response.read().decode("utf-8-sig")
    except Exception as e:
        print(f"ОШИБКА: не удалось скачать плейлист: {e}", file=sys.stderr)
        return 1

    # Сохраняем скачанный оригинал в папку output/
    DOWNLOADED_PLAYLIST.write_text(original, encoding="utf-8")

    # Обрабатываем содержимое
    cleaned, count = clean_playlist(original)

    # Сохраняем результаты
    OUTPUT_CLEAN.write_text(cleaned, encoding="utf-8")
    OUTPUT_ROOT.write_text(cleaned, encoding="utf-8")

    print(f"Обработка завершена. Замен ссылок: {count}")
    print(f"Файлы сохранены:\n  - {OUTPUT_CLEAN}\n  - {OUTPUT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
