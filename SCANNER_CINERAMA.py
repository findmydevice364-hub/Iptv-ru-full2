import os
import re
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict


# ============================================================
# CINERAMA VERIFIED 1
# ============================================================

MAX_THREADS = 50
TIMEOUT = 3

START_ID = 0
END_ID = 6000

STREAM8_BASE = "https://stream8.cinerama.uz"
STREAM0_BASE = "https://stream0.cinerama.uz"
STREAM1_BASE = "https://stream1.cinerama.uz"

OUTPUT_FILE = "cinerama_Verified_1.m3u"
REPORT_FILE = "SKALA_DREG_REPORT.txt"

# ОБА старых плейлиста используются ТОЛЬКО ДЛЯ СВЕРКИ
PREVIOUS_FILES = [
    "CINERAMA_VERIFIED.m3u",
    "cinerama_Verified_1.m3u",
]

COPYRIGHT = "© Phoenix 89S - Verify 1"


# ============================================================
# HTTP SESSION
# ============================================================

def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 Cinerama-Scanner/1.0",
        "Accept": "*/*",
        "Connection": "keep-alive",
    })
    return session


# ============================================================
# URL
# ============================================================

def build_url(base, stream_id):
    return f"{base}/{stream_id}/tracks-v1a1/mono.m3u8"


# ============================================================
# НОРМАЛИЗАЦИЯ НАЗВАНИЯ
# ============================================================

def clean_name(name):
    name = name.strip()

    # Убираем лишние пробелы
    name = re.sub(r"\s+", " ", name)

    # Иногда название может содержать запятые/служебные хвосты
    return name.strip(" ,")


# ============================================================
# ПОЛУЧЕНИЕ EXTINF
# ============================================================

def extract_extinf_name(text):
    if not text:
        return None

    for line in text.splitlines():
        line = line.strip()

        if line.startswith("#EXTINF:"):
            # Последняя запятая отделяет параметры EXTINF от названия
            if "," in line:
                name = line.split(",", 1)[1].strip()

                if name:
                    return clean_name(name)

    return None


# ============================================================
# ИЗВЛЕЧЕНИЕ TVG-ID
# ============================================================

def extract_tvg_id(extinf):
    if not extinf:
        return None

    match = re.search(
        r'tvg-id=["\']([^"\']+)["\']',
        extinf,
        re.IGNORECASE
    )

    if match:
        return match.group(1).strip()

    return None


# ============================================================
# STREAM8
#
# ВАЖНО:
# Stream8 НЕ ПОПАДАЕТ В ИТОГ.
#
# Он используется только для:
# 1. генерации URL
# 2. проверки живучести
# 3. получения #EXTINF / названия
# ============================================================

def scan_stream8(stream_id):
    session = make_session()

    url = build_url(STREAM8_BASE, stream_id)

    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True
        )

        if response.status_code != 200:
            return None

        text = response.text

        if not text or "#EXTINF:" not in text:
            return None

        name = extract_extinf_name(text)

        if not name:
            return None

        return {
            "id": stream_id,
            "name": name,
            "source_url": url,
        }

    except Exception:
        return None

    finally:
        session.close()


# ============================================================
# СКАНИРОВАНИЕ STREAM8 0-6000
# ============================================================

def scan_stream8():
    found = []

    total = END_ID - START_ID + 1
    completed = 0

    print()
    print("=" * 60)
    print("СКАНИРОВАНИЕ STREAM8")
    print(f"Диапазон: {START_ID}-{END_ID}")
    print(f"Потоков: {MAX_THREADS}")
    print("=" * 60)
    print()

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:

        futures = {
            executor.submit(scan_stream8, stream_id): stream_id
            for stream_id in range(START_ID, END_ID + 1)
        }

        for future in as_completed(futures):
            completed += 1

            try:
                result = future.result()

                if result:
                    found.append(result)

                    print(
                        f"[FOUND] Stream8 ID={result['id']} "
                        f"-> {result['name']}"
                    )

            except Exception:
                pass

            print(
                f"Прогресс: {completed}/{total}",
                flush=True
            )

    found.sort(key=lambda x: x["id"])

    print()
    print(f"[SCAN] Найдено Stream8: {len(found)}")

    return found


# ============================================================
# ПРОВЕРКА STREAM0 / STREAM1
#
# Название уже получено из Stream8.
# Здесь мы НЕ ИЩЕМ НАЗВАНИЕ.
# Проверяем только соответствующий поток.
# ============================================================

def check_mirror(base_url, stream8_item, group_name):
    stream_id = stream8_item["id"]

    url = build_url(base_url, stream_id)

    session = make_session()

    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True
        )

        if response.status_code != 200:
            return None

        text = response.text

        if not text:
            return None

        # Поток должен реально отдавать M3U8
        if "#EXTINF:" not in text:
            return None

        return {
            "id": stream_id,
            "name": stream8_item["name"],
            "group": group_name,
            "url": url,
            "response": response.status_code,
            "tvg_id": stream8_item.get("tvg_id"),
        }

    except Exception:
        return None

    finally:
        session.close()


# ============================================================
# ФОРМИРОВАНИЕ STREAM0 + STREAM1
# ============================================================

def build_mirrors(stream8_channels):
    result = []

    total = len(stream8_channels) * 2
    completed = 0

    print()
    print("=" * 60)
    print("ПРОВЕРКА ЗЕРКАЛ STREAM0 / STREAM1")
    print("=" * 60)
    print()

    jobs = []

    for item in stream8_channels:
        jobs.append(
            (
                STREAM0_BASE,
                "Stream0",
                item
            )
        )

        jobs.append(
            (
                STREAM1_BASE,
                "Stream1",
                item
            )
        )

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:

        futures = {
            executor.submit(
                check_mirror,
                base_url,
                item,
                group
            ): (group, item["id"])
            for base_url, group, item in jobs
        }

        for future in as_completed(futures):
            completed += 1

            group, stream_id = futures[future]

            try:
                item = future.result()

                if item:
                    result.append(item)

                    print(
                        f"[LIVE] {group} "
                        f"ID={item['id']} "
                        f"-> {item['name']}"
                    )

            except Exception:
                pass

            print(
                f"Зеркала: {completed}/{total}",
                flush=True
            )

    # Stream0 сначала, Stream1 потом
    group_order = {
        "Stream0": 0,
        "Stream1": 1,
    }

    result.sort(
        key=lambda x: (
            x["id"],
            group_order.get(x["group"], 99)
        )
    )

    print()
    print(f"[MIRRORS] Живых Stream0/Stream1: {len(result)}")

    return result


# ============================================================
# ПАРСИНГ СТАРОГО M3U
#
# НИКАКОЙ ГЕНЕРАЦИИ ИЗ СТАРОГО M3U.
# ТОЛЬКО ЧТЕНИЕ ДЛЯ СВЕРКИ.
# ============================================================

def parse_m3u(filename):
    result = OrderedDict()

    if not os.path.exists(filename):
        return result

    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as f:
            lines = [line.rstrip("\n") for line in f]

    except Exception as e:
        print(f"[WARN] Не удалось прочитать {filename}: {e}")
        return result

    current_extinf = None

    for line in lines:

        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            current_extinf = line
            continue

        if line.startswith("http") and current_extinf:

            url = line

            # Определяем группу именно по URL
            if "stream0.cinerama.uz" in url:
                group = "Stream0"

            elif "stream1.cinerama.uz" in url:
                group = "Stream1"

            elif "stream8.cinerama.uz" in url:
                group = "Stream8"

            else:
                current_extinf = None
                continue

            # ID из URL
            match = re.search(
                r"cinerama\.uz/(\d+)/",
                url
            )

            if not match:
                current_extinf = None
                continue

            stream_id = int(match.group(1))

            name = extract_extinf_name(current_extinf)

            key = (group, stream_id)

            result[key] = {
                "id": stream_id,
                "name": name or "",
                "group": group,
                "url": url,
            }

            current_extinf = None

    return result


# ============================================================
# СВЕРКА
# ============================================================

def make_key(item):
    return (
        item["group"],
        int(item["id"])
    )


def build_current_map(current):
    result = OrderedDict()

    for item in current:
        result[make_key(item)] = item

    return result


def compare_one_old_playlist(filename, current_map):
    old_map = parse_m3u(filename)

    report = {
        "file": filename,
        "old_count": len(old_map),
        "added": [],
        "removed": [],
        "changed_name": [],
        "unchanged": [],
    }

    old_keys = set(old_map.keys())
    new_keys = set(current_map.keys())

    # Новые
    for key in sorted(new_keys - old_keys):
        report["added"].append(current_map[key])

    # Исчезнувшие
    for key in sorted(old_keys - new_keys):
        report["removed"].append(old_map[key])

    # Есть в обоих
    for key in sorted(old_keys & new_keys):

        old = old_map[key]
        new = current_map[key]

        old_name = clean_name(old.get("name", ""))
        new_name = clean_name(new.get("name", ""))

        if old_name != new_name:
            report["changed_name"].append({
                "old": old,
                "new": new,
            })
        else:
            report["unchanged"].append(new)

    return report


# ============================================================
# ОТЧЁТ
# ============================================================

def write_report(
    reports,
    stream8_count,
    current_count
):
    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write("CINERAMA VERIFIED 1 — ОТЧЁТ СВЕРКИ\n")
        f.write("=" * 70 + "\n")
        f.write(
            f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write("\n")

        f.write("НОВЫЙ ПРОХОД\n")
        f.write("-" * 70 + "\n")
        f.write(
            f"Stream8 найдено: {stream8_count}\n"
        )
        f.write(
            f"Итоговых Stream0/Stream1: {current_count}\n"
        )
        f.write("\n")

        for report in reports:

            f.write("=" * 70 + "\n")
            f.write(
                f"СВЕРКА С: {report['file']}\n"
            )
            f.write("=" * 70 + "\n")

            f.write(
                f"Было записей: {report['old_count']}\n"
            )

            f.write(
                f"Новых: {len(report['added'])}\n"
            )

            f.write(
                f"Исчезло: {len(report['removed'])}\n"
            )

            f.write(
                f"Изменено название: "
                f"{len(report['changed_name'])}\n"
            )

            f.write(
                f"Без изменений: "
                f"{len(report['unchanged'])}\n"
            )

            f.write("\n")

            if report["added"]:
                f.write("ДОБАВЛЕНЫ:\n")

                for item in report["added"]:
                    f.write(
                        f"+ {item['group']} "
                        f"ID={item['id']} "
                        f"{item['name']}\n"
                    )

                f.write("\n")

            if report["removed"]:
                f.write("ИСЧЕЗЛИ:\n")

                for item in report["removed"]:
                    f.write(
                        f"- {item['group']} "
                        f"ID={item['id']} "
                        f"{item['name']}\n"
                    )

                f.write("\n")

            if report["changed_name"]:
                f.write("ИЗМЕНИЛОСЬ НАЗВАНИЕ:\n")

                for item in report["changed_name"]:
                    old = item["old"]
                    new = item["new"]

                    f.write(
                        f"* {new['group']} "
                        f"ID={new['id']}\n"
                    )

                    f.write(
                        f"  БЫЛО: {old['name']}\n"
                    )

                    f.write(
                        f"  СТАЛО: {new['name']}\n"
                    )

                f.write("\n")


# ============================================================
# EXTINF ДЛЯ НОВОГО M3U
# ============================================================

def make_extinf(item, channel_number):
    name = item["name"]

    tvg_id = item.get("tvg_id")

    if not tvg_id:
        tvg_id = f"cinerama_{item['id']}"

    return (
        f'#EXTINF:-1 '
        f'tvg-chno="{channel_number}" '
        f'tvg-id="{tvg_id}" '
        f'group-title="{item["group"]}",'
        f'{name}'
    )


# ============================================================
# ЗАПИСЬ НОВОГО ПЛЕЙЛИСТА
#
# ВАЖНО:
# STREAM8 ЗДЕСЬ НЕТ.
#
# Только Stream0 + Stream1.
# ============================================================

def write_playlist(items):

    group_order = {
        "Stream0": 0,
        "Stream1": 1,
    }

    items = sorted(
        items,
        key=lambda x: (
            int(x["id"]),
            group_order.get(x["group"], 99)
        )
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write("#EXTM3U\n")
        f.write(
            f"# Playlist verified by {COPYRIGHT}\n"
        )
        f.write(
            f"# Generated: "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        channel_number = 1

        for item in items:

            f.write(
                make_extinf(
                    item,
                    channel_number
                )
                + "\n"
            )

            f.write(
                item["url"]
                + "\n"
            )

            channel_number += 1


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("CINERAMA VERIFIED 1")
    print("=" * 70)
    print()
    print(
        "Stream8 = ИСТОЧНИК ДЛЯ ПОИСКА ID + НАЗВАНИЯ"
    )
    print(
        "Stream0/Stream1 = ИТОГОВЫЕ ПРОВЕРЯЕМЫЕ ПОТОКИ"
    )
    print(
        "Старые M3U = ТОЛЬКО СВЕРКА"
    )
    print()

    # --------------------------------------------------------
    # 1. STREAM8
    # --------------------------------------------------------

    stream8_channels = scan_stream8()

    if not stream8_channels:
        print()
        print(
            "[ERROR] Stream8 ничего не найдено."
        )
        print(
            "[ERROR] Итоговый M3U не будет сформирован."
        )
        return

    # --------------------------------------------------------
    # 2. STREAM0 + STREAM1
    # --------------------------------------------------------

    current = build_mirrors(
        stream8_channels
    )

    # --------------------------------------------------------
    # 3. НЕ ДАЁМ STREAM8 ПОПАСТЬ В ИТОГ
    # --------------------------------------------------------

    current = [
        item
        for item in current
        if item["group"] in (
            "Stream0",
            "Stream1"
        )
    ]

    # --------------------------------------------------------
    # 4. КАРТА НОВОГО РЕЗУЛЬТАТА
    # --------------------------------------------------------

    current_map = build_current_map(
        current
    )

    # --------------------------------------------------------
    # 5. СВЕРЯЕМСЯ С ОБОИМИ СТАРЫМИ M3U
    # --------------------------------------------------------

    reports = []

    for filename in PREVIOUS_FILES:

        # Не сверяем файл сам с собой после его создания
        if os.path.abspath(filename) == os.path.abspath(
            OUTPUT_FILE
        ):
            # Если OUTPUT_FILE уже существовал ДО запуска,
            # он всё равно должен участвовать в сверке.
            pass

        report = compare_one_old_playlist(
            filename,
            current_map
        )

        reports.append(report)

    # --------------------------------------------------------
    # 6. ПИШЕМ НОВЫЙ M3U
    # --------------------------------------------------------

    write_playlist(current)

    # --------------------------------------------------------
    # 7. ПИШЕМ ОТЧЁТ
    # --------------------------------------------------------

    write_report(
        reports,
        len(stream8_channels),
        len(current)
    )

    # --------------------------------------------------------
    # 8. СТАТИСТИКА
    # --------------------------------------------------------

    stream0_count = sum(
        1
        for item in current
        if item["group"] == "Stream0"
    )

    stream1_count = sum(
        1
        for item in current
        if item["group"] == "Stream1"
    )

    print()
    print("=" * 70)
    print("ГОТОВО")
    print("=" * 70)
    print()
    print(
        f"Stream8 найдено: {len(stream8_channels)}"
    )
    print(
        f"Stream0 живых:  {stream0_count}"
    )
    print(
        f"Stream1 живых:  {stream1_count}"
    )
    print(
        f"ИТОГО записей:  {len(current)}"
    )
    print()
    print(
        f"Плейлист: {OUTPUT_FILE}"
    )
    print(
        f"Отчёт:    {REPORT_FILE}"
    )
    print()
    print(
        "Stream8 в итоговый M3U НЕ записывается."
    )
    print(
        "Старые M3U используются только для сверки."
    )
    print()


if __name__ == "__main__":
    main()