
#!/usr/bin/env python3
"""Преобразование потоков McQuack в IPTV-плейлисте."""

import re
import sys
from pathlib import Path

PLAYLIST = Path("processed/combined_by_name.m3u")

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
    if not PLAYLIST.is_file():
        print(f"ОШИБКА: файл не найден: {PLAYLIST}", file=sys.stderr)
        return 1

    # Сохраняем исходное содержимое, если изменений нет.
    original = PLAYLIST.read_text(encoding="utf-8-sig")
    cleaned, count = clean_playlist(original)

    if count:
        PLAYLIST.write_text(cleaned, encoding="utf-8")
    
    print(f"Обработка завершена. Замен ссылок: {count}")
    print(f"Файл: {PLAYLIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
