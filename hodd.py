import time
from urllib.parse import urljoin

import requests
import m3u8

TARGET_URL = (
    "http://live-3.otcnet.ru/"
    "sportivny/tracks-v1a1/mono.ts.m3u8"
)

BASE_URL = "http://live-3.otcnet.ru"
TIMEOUT = 10
MAX_DEPTH = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "IPTV-Checker/1.0"
    )
}

# Проверяемые предположения о расположении мастер-плейлиста.
MASTER_CANDIDATES = [
    f"{BASE_URL}/master.m3u8",
    f"{BASE_URL}/index.m3u8",
    f"{BASE_URL}/playlist.m3u8",
    f"{BASE_URL}/master.m3u",
    f"{BASE_URL}/index.m3u",
    f"{BASE_URL}/sportivny/master.m3u8",
    f"{BASE_URL}/sportivny/index.m3u8",
    f"{BASE_URL}/sportivny/playlist.m3u8",
]

visited = set()
found_masters = set()
found_media = set()


def request_playlist(session, url):
    print(f"\n[ЗАПРОС] {url}")
    started = time.perf_counter()

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        elapsed = time.perf_counter() - started

        print(f"  HTTP: {response.status_code}")
        print(f"  Время: {elapsed:.3f} сек")
        print(
            "  Content-Type:",
            response.headers.get("Content-Type", "-")
        )
        print(f"  Конечный URL: {response.url}")

        if response.status_code != 200:
            return None, response.url

        body = response.text.lstrip("\ufeff \r\n\t")

        if not body.startswith("#EXTM3U"):
            print("  [НЕ HLS] Ответ не является HLS-плейлистом")
            return None, response.url

        return m3u8.loads(body, uri=response.url), response.url

    except requests.RequestException as exc:
        print(f"  [ОШИБКА СЕТИ] {exc}")
    except Exception as exc:
        print(f"  [ОШИБКА ПАРСИНГА] {exc}")

    return None, url


def inspect(session, url, depth=0):
    if depth > MAX_DEPTH:
        print("[ЛИМИТ] Превышена глубина вложенности")
        return

    if url in visited:
        return

    visited.add(url)

    playlist, final_url = request_playlist(session, url)

    if playlist is None:
        return

    if playlist.is_variant:
        found_masters.add(final_url)

        print("\n========== МАСТЕР-ПЛЕЙЛИСТ ==========")
        print(f"URL: {final_url}")
        print(f"Количество вариантов: {len(playlist.playlists)}")

        for number, variant in enumerate(playlist.playlists, 1):
            info = variant.stream_info
            child_url = urljoin(final_url, variant.uri)

            print(f"\nВариант #{number}")
            print(f"  URL: {child_url}")
            print(f"  Bandwidth: {info.bandwidth}")
            print(
                "  Average bandwidth:",
                getattr(info, "average_bandwidth", None)
            )
            print(f"  Разрешение: {info.resolution}")
            print(f"  Кодеки: {info.codecs}")
            print(f"  FPS: {info.frame_rate}")

            inspect(session, child_url, depth + 1)

        for media in playlist.media:
            media_url = (
                urljoin(final_url, media.uri)
                if media.uri
                else None
            )

            print("\nДополнительная дорожка:")
            print(f"  Тип: {media.type}")
            print(f"  Имя: {media.name}")
            print(f"  Язык: {media.language}")
            print(f"  Группа: {media.group_id}")
            print(f"  URI: {media_url or '(не указан)'}")

            if media_url:
                inspect(session, media_url, depth + 1)

    else:
        found_media.add(final_url)

        print("\n========== МЕДИА-ПЛЕЙЛИСТ ==========")
        print(f"URL: {final_url}")
        print(f"Сегментов: {len(playlist.segments)}")
        print(f"Target duration: {playlist.target_duration}")
        print(f"Live: {not playlist.is_endlist}")
        print(f"ENDLIST: {playlist.is_endlist}")

        if playlist.segments:
            indexes = sorted({
                0,
                len(playlist.segments) // 2,
                len(playlist.segments) - 1,
            })

            for index in indexes:
                segment = playlist.segments[index]
                segment_url = urljoin(final_url, segment.uri)

                print(
                    f"  Сегмент {index + 1}: {segment_url} "
                    f"({segment.duration} сек)"
                )


def main():
    with requests.Session() as session:
        print("=" * 60)
        print("1. ПРОВЕРКА ИСХОДНОГО ПОТОКА")
        print("=" * 60)

        inspect(session, TARGET_URL)

        print("\n" + "=" * 60)
        print("2. ПОИСК МАСТЕРА НА ЭТОМ ЖЕ УЗЛЕ")
        print("=" * 60)

        for candidate in MASTER_CANDIDATES:
            if candidate in visited:
                continue

            playlist, final_url = request_playlist(
                session, candidate
            )

            if playlist is None:
                continue

            if playlist.is_variant:
                print(f"\n[МАСТЕР НАЙДЕН] {final_url}")
            else:
                print(f"\n[HLS НАЙДЕН, НО НЕ МАСТЕР] {final_url}")

            inspect(session, final_url)

        print("\n" + "=" * 60)
        print("3. ИТОГОВЫЙ ОТЧЁТ")
        print("=" * 60)

        print("\nНайденные мастер-плейлисты:")
        for item in sorted(found_masters):
            print(f"  {item}")

        print("\nНайденные медиаплейлисты:")
        for item in sorted(found_media):
            print(f"  {item}")

        print(f"\nВсего мастеров: {len(found_masters)}")
        print(f"Всего медиаплейлистов: {len(found_media)}")
        print("Проверка завершена.")


if __name__ == "__main__":
    main()