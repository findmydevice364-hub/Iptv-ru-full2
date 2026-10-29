#!/usr/bin/env python3
"""Преобразование потоков McQuack и поиск каналов с мультидорожками в IPTV-плейлисте."""

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


def scan_mcquack_channels(text: str) -> None:
    """Отчёт по всем найденным и замененным потокам McQuack."""
    lines = text.splitlines()
    print("\n" + "="*85)
    print(" 1. ОТЧЁТ ПО ПОТОКАМ MCQUACK (ДО И ПОСЛЕ ЗАМЕНЫ):")
    print("="*85)
    
    found_count = 0
    for i in range(len(lines) - 1):
        line = lines[i]
        if line.startswith("#EXTINF:"):
            url_line = lines[i + 1] if i + 1 < len(lines) else ""
            if "stream.mcquack.net" in url_line:
                found_count += 1
                cleaned_url, _ = clean_playlist(url_line)
                print(f"\n[{found_count}] Канал: {line}")
                print(f"     Было:  {url_line}")
                print(f"     Стало: {cleaned_url}")
                
    if found_count == 0:
        print("Потоков McQuack не найдено.")
    print("="*85 + "\n")


def scan_multitrack_channels(text: str) -> None:
    """Отчёт по каналам с мультидорожками (языки, аудио-метки в названии или ссылках)."""
    lines = text.splitlines()
    print("\n" + "="*85)
    print(" 2. ОТЧЁТ ПО КАНАЛАМ С МУЛЬТИДОРОЖКАМИ (АЗУО/ЯЗЫКИ/ТРЕКИ):")
    print("="*85)
    
    found_count = 0
    # Ключевые слова, которые обычно указывают на наличие конкретных звуковых дорожек или языков
    multitrack_keywords = [
        "рус", "kaz", "каз", "eng", "en", "rus", 
        "дорожк", "звук", "audio", "track", 
        "(rus)", "(kaz)", "[rus]", "[kaz]", "ову", "мво"
    ]
    
    for i in range(len(lines) - 1):
        line = lines[i]
        if line.startswith("#EXTINF:"):
            url_line = lines[i + 1] if i + 1 < len(lines) else ""
            
            # Исключаем mcquack, чтобы они не смешивались
            if "stream.mcquack.net" not in url_line:
                combined_info = (line + " " + url_line).lower()
                
                # Ищем признаки мультидорожечности в названии или структуре ссылки
                if any(kw in combined_info for kw in multitrack_keywords):
                    found_count += 1
                    print(f"\n[{found_count}] Канал: {line}")
                    print(f"     Ссылка: {url_line}")
                    
    if found_count == 0:
        print("Каналов с явными мультидорожками по фильтрам не найдено.")
    print("="*85 + "\n")


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

    # 1. Запускаем отчёт по McQuack (до/после)
    scan_mcquack_channels(original)

    # 2. Запускаем отчёт по каналам с мультидорожками
    scan_multitrack_channels(original)

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
