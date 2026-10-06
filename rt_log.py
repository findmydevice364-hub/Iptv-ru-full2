import re
import csv
import json
import requests
from pathlib import Path


# ============================================================
# НАСТРОЙКИ
# ============================================================

PLAYLIST_URL = (
    "https://gitverse.ru/api/repos/radio641/tv/raw/branch/master/"
    "rossteleccom.net.m3u"
)

OUTPUT_DIR = Path("channel_database")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CSV_FILE = OUTPUT_DIR / "channels.csv"
PY_FILE = OUTPUT_DIR / "channels_data.py"
TXT_FILE = OUTPUT_DIR / "channels.txt"
JSON_FILE = OUTPUT_DIR / "channels.json"

TIMEOUT = 30


# ============================================================
# ТРАНСЛИТЕРАЦИЯ
# ============================================================

RU_TO_LAT = str.maketrans({
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D",
    "Е": "E", "Ё": "Yo", "Ж": "Zh", "З": "Z", "И": "I",
    "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N",
    "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T",
    "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
    "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
    "Э": "E", "Ю": "Yu", "Я": "Ya",

    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def transliterate(text: str) -> str:
    return text.translate(RU_TO_LAT)


# ============================================================
# ОБЩАЯ НОРМАЛИЗАЦИЯ
# ============================================================

def clean_spaces(text: str) -> str:
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_quotes(text: str) -> str:
    replacements = {
        "«": '"',
        "»": '"',
        "„": '"',
        "“": '"',
        "”": '"',
        "’": "'",
        "‘": "'",
        "`": "'",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def normalize_name(text: str) -> str:
    text = normalize_quotes(text)
    text = clean_spaces(text)
    return text.strip(" |-_.,;:")


# ============================================================
# КАЧЕСТВО
# ============================================================

QUALITY_PATTERN = re.compile(
    r"""
    [\s._-]*
    \(?
    (
        HD|
        FHD|
        UHD|
        4K|
        8K|
        SD|
        HQ|
        HDR|
        HDR10|
        HDR10\+
    )
    \)?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def detect_quality(name: str) -> list[str]:
    result = []

    for match in QUALITY_PATTERN.finditer(name):
        quality = match.group(1).upper()

        if quality not in result:
            result.append(quality)

    return result


def remove_quality(name: str) -> str:
    result = QUALITY_PATTERN.sub(" ", name)
    result = clean_spaces(result)

    return result.strip(" -_|.,;:")


# ============================================================
# ПОИСКОВЫЕ КЛЮЧИ
# ============================================================

def compact_cyrillic(text: str) -> str:
    return re.sub(
        r"[^а-яё0-9]",
        "",
        text.lower(),
    )


def compact_latin(text: str) -> str:
    text = transliterate(text)

    return re.sub(
        r"[^a-z0-9]",
        "",
        text.lower(),
    )


def make_search_key(text: str) -> str:
    """
    Унифицированный ключ поиска.

    Матч ТВ HD
    Матч-ТВ
    MATCH TV
    matchtv

    будут максимально близки при последующем поиске.
    """

    text = transliterate(text)
    text = text.lower()

    text = remove_quality(text)

    # Частые словесные варианты
    text = text.replace("television", "tv")
    text = text.replace("телевидение", "tv")
    text = text.replace("телеканал", "tv")

    text = re.sub(
        r"[^a-z0-9]",
        "",
        text,
    )

    return text


# ============================================================
# ДОБАВЛЕНИЕ АЛИАСА
# ============================================================

def add_alias(
    aliases: dict,
    value: str,
    alias_type: str,
    language: str,
    confidence: str,
):
    value = normalize_name(value)

    if not value or len(value) < 2:
        return

    key = value.casefold()

    if key not in aliases:

        aliases[key] = {
            "alias": value,
            "type": alias_type,
            "language": language,
            "confidence": confidence,
        }


# ============================================================
# ГЕНЕРАТОР АЛИАСОВ
# ============================================================

def make_aliases(original_name: str) -> list[dict]:

    original_name = normalize_name(original_name)

    aliases = {}

    # --------------------------------------------------------
    # ORIGINAL
    # --------------------------------------------------------

    add_alias(
        aliases,
        original_name,
        "original",
        "source",
        "exact",
    )

    # --------------------------------------------------------
    # РУССКИЕ ВАРИАНТЫ
    # --------------------------------------------------------

    add_alias(
        aliases,
        original_name.lower(),
        "case",
        "ru",
        "high",
    )

    add_alias(
        aliases,
        original_name.upper(),
        "case",
        "ru",
        "high",
    )

    # Нормализованные кавычки
    add_alias(
        aliases,
        normalize_quotes(original_name),
        "normalized",
        "ru",
        "high",
    )

    # Без качества
    no_quality = remove_quality(original_name)

    add_alias(
        aliases,
        no_quality,
        "without_quality",
        "ru",
        "high",
    )

    add_alias(
        aliases,
        no_quality.lower(),
        "without_quality",
        "ru",
        "high",
    )

    add_alias(
        aliases,
        no_quality.upper(),
        "without_quality",
        "ru",
        "high",
    )

    # --------------------------------------------------------
    # TV <-> ТВ
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        if re.search(
            r"\bТВ\b",
            value,
            re.IGNORECASE,
        ):

            add_alias(
                aliases,
                re.sub(
                    r"\bТВ\b",
                    "TV",
                    value,
                    flags=re.IGNORECASE,
                ),
                "tv_variant",
                "mixed",
                "high",
            )

        if re.search(
            r"\bTV\b",
            value,
            re.IGNORECASE,
        ):

            add_alias(
                aliases,
                re.sub(
                    r"\bTV\b",
                    "ТВ",
                    value,
                    flags=re.IGNORECASE,
                ),
                "tv_variant",
                "ru",
                "high",
            )

    # --------------------------------------------------------
    # MATCH <-> МАТЧ
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        if re.search(
            r"матч",
            value,
            re.IGNORECASE,
        ):

            add_alias(
                aliases,
                re.sub(
                    r"матч",
                    "MATCH",
                    value,
                    flags=re.IGNORECASE,
                ),
                "international",
                "en",
                "high",
            )

            add_alias(
                aliases,
                re.sub(
                    r"матч",
                    "MATCH!",
                    value,
                    flags=re.IGNORECASE,
                ),
                "international",
                "en",
                "high",
            )

        if re.search(
            r"match",
            value,
            re.IGNORECASE,
        ):

            add_alias(
                aliases,
                re.sub(
                    r"match!?",
                    "Матч",
                    value,
                    flags=re.IGNORECASE,
                ),
                "russian_variant",
                "ru",
                "high",
            )

    # --------------------------------------------------------
    # РУССКИЙ -> ЛАТИНИЦА
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        latin = transliterate(value)

        if latin != value:

            add_alias(
                aliases,
                latin,
                "transliteration",
                "latin",
                "high",
            )

            add_alias(
                aliases,
                latin.lower(),
                "transliteration",
                "latin",
                "high",
            )

            add_alias(
                aliases,
                latin.upper(),
                "transliteration",
                "latin",
                "high",
            )

    # --------------------------------------------------------
    # ЛАТИНСКАЯ ФОРМА БЕЗ КАЧЕСТВА
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        latin = transliterate(value)
        latin = remove_quality(latin)

        add_alias(
            aliases,
            latin,
            "latin_without_quality",
            "latin",
            "high",
        )

    # --------------------------------------------------------
    # ВАРИАНТЫ ПУНКТУАЦИИ
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        punctuation_variants = {
            value.replace("-", " "),
            value.replace("_", " "),
            value.replace(".", " "),
            value.replace("|", " "),
            value.replace("/", " "),
            re.sub(
                r"[-_.|/]+",
                " ",
                value,
            ),
        }

        for variant in punctuation_variants:

            add_alias(
                aliases,
                variant,
                "punctuation",
                "mixed",
                "normal",
            )

    # --------------------------------------------------------
    # КОМПАКТНЫЕ ФОРМЫ
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        cyr = compact_cyrillic(value)

        if cyr:

            add_alias(
                aliases,
                cyr,
                "compact",
                "ru",
                "normal",
            )

        lat = compact_latin(value)

        if lat:

            add_alias(
                aliases,
                lat,
                "compact",
                "latin",
                "normal",
            )

    # --------------------------------------------------------
    # ВАРИАНТ БЕЗ !
    # --------------------------------------------------------

    variants = list(aliases.values())

    for item in variants:

        value = item["alias"]

        if value.endswith("!"):

            add_alias(
                aliases,
                value[:-1].strip(),
                "punctuation",
                item["language"],
                "normal",
            )

    # --------------------------------------------------------
    # КАЧЕСТВЕННЫЕ ВАРИАНТЫ
    # --------------------------------------------------------

    base = no_quality

    qualities = [
        "HD",
        "FHD",
        "UHD",
        "4K",
        "8K",
        "SD",
        "HQ",
    ]

    for quality in qualities:

        add_alias(
            aliases,
            f"{base} {quality}",
            "quality",
            "ru",
            "normal",
        )

        add_alias(
            aliases,
            f"{base} ({quality})",
            "quality",
            "ru",
            "normal",
        )

        latin_base = transliterate(base)

        add_alias(
            aliases,
            f"{latin_base} {quality}",
            "quality",
            "latin",
            "normal",
        )

        add_alias(
            aliases,
            f"{latin_base} ({quality})",
            "quality",
            "latin",
            "normal",
        )

    # --------------------------------------------------------
    # СОРТИРОВКА
    # --------------------------------------------------------

    result = list(aliases.values())

    result.sort(
        key=lambda x: (
            0 if x["alias"] == original_name else 1,
            x["alias"].casefold(),
        )
    )

    return result


# ============================================================
# M3U PARSER
# ============================================================

def parse_m3u(text: str) -> list[dict]:

    channels = []

    current_name = None
    current_extinf = None

    seen_urls = set()

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        # ----------------------------------------------------
        # EXTINF
        # ----------------------------------------------------

        if line.startswith("#EXTINF:"):

            current_extinf = line

            parts = line.split(",", 1)

            if len(parts) > 1:
                current_name = normalize_name(
                    parts[1]
                )
            else:
                current_name = None

            continue

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        if (
            current_name
            and line
            and not line.startswith("#")
        ):

            url = line

            if url in seen_urls:

                current_name = None
                current_extinf = None
                continue

            aliases = make_aliases(
                current_name
            )

            channel_id = len(channels) + 1

            channels.append({
                "id": channel_id,
                "original_name": current_name,
                "url": url,
                "extinf": current_extinf or "",
                "quality": detect_quality(
                    current_name
                ),
                "search_key": make_search_key(
                    current_name
                ),
                "aliases": aliases,
            })

            seen_urls.add(url)

            current_name = None
            current_extinf = None

    return channels


# ============================================================
# CSV
# ============================================================

def save_csv(
    channels: list[dict],
    filename: Path,
):

    fields = [
        "channel_id",
        "original_name",
        "alias",
        "language",
        "alias_type",
        "confidence",
        "search_key",
        "quality",
        "url",
        "extinf",
    ]

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for channel in channels:

            for alias in channel["aliases"]:

                writer.writerow({
                    "channel_id": channel["id"],
                    "original_name": channel[
                        "original_name"
                    ],
                    "alias": alias["alias"],
                    "language": alias["language"],
                    "alias_type": alias["type"],
                    "confidence": alias[
                        "confidence"
                    ],
                    "search_key": channel[
                        "search_key"
                    ],
                    "quality": "|".join(
                        channel["quality"]
                    ),
                    "url": channel["url"],
                    "extinf": channel["extinf"],
                })


# ============================================================
# PYTHON DATABASE
# ============================================================

def save_python(
    channels: list[dict],
    filename: Path,
):

    alias_index = {}
    search_index = {}

    for channel in channels:

        # Алиас -> channel_id
        for alias in channel["aliases"]:

            key = alias["alias"].casefold()

            alias_index.setdefault(
                key,
                [],
            ).append(channel["id"])

        # Унифицированный ключ
        key = channel["search_key"]

        search_index.setdefault(
            key,
            [],
        ).append(channel["id"])

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "# =====================================================\n"
            "# AUTO-GENERATED IPTV CHANNEL DATABASE\n"
            "# =====================================================\n"
            "# SOURCE:\n"
            f"# {PLAYLIST_URL}\n"
            "# =====================================================\n\n"
        )

        f.write(
            "CHANNELS = "
            + repr(channels)
            + "\n\n"
        )

        f.write(
            "# alias.casefold() -> [channel_id, ...]\n"
        )

        f.write(
            "ALIAS_INDEX = "
            + repr(alias_index)
            + "\n\n"
        )

        f.write(
            "# normalized search key -> [channel_id, ...]\n"
        )

        f.write(
            "SEARCH_INDEX = "
            + repr(search_index)
            + "\n\n"
        )

        f.write(
            "CHANNEL_NAMES = "
            + repr([
                ch["original_name"]
                for ch in channels
            ])
            + "\n\n"
        )

        all_aliases = sorted(
            {
                alias["alias"]
                for channel in channels
                for alias in channel["aliases"]
            },
            key=str.casefold,
        )

        f.write(
            "ALL_ALIASES = "
            + repr(all_aliases)
            + "\n"
        )


# ============================================================
# TXT
# ============================================================

def save_txt(
    channels: list[dict],
    filename: Path,
):

    total_aliases = sum(
        len(channel["aliases"])
        for channel in channels
    )

    unique_aliases = len({
        alias["alias"].casefold()
        for channel in channels
        for alias in channel["aliases"]
    })

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "=" * 70 + "\n"
        )

        f.write(
            "IPTV CHANNEL NAME / ALIAS DATABASE\n"
        )

        f.write(
            "=" * 70 + "\n\n"
        )

        f.write(
            f"SOURCE:\n{PLAYLIST_URL}\n\n"
        )

        f.write(
            f"CHANNELS       : {len(channels)}\n"
        )

        f.write(
            f"TOTAL ALIASES  : {total_aliases}\n"
        )

        f.write(
            f"UNIQUE ALIASES : {unique_aliases}\n\n"
        )

        f.write(
            "=" * 70 + "\n\n"
        )

        # ----------------------------------------------------
        # КАЖДЫЙ КАНАЛ
        # ----------------------------------------------------

        for channel in channels:

            f.write(
                f"[CHANNEL {channel['id']}]\n"
            )

            f.write(
                f"ORIGINAL NAME : "
                f"{channel['original_name']}\n"
            )

            f.write(
                f"SEARCH KEY    : "
                f"{channel['search_key']}\n"
            )

            f.write(
                f"QUALITY       : "
                f"{', '.join(channel['quality']) or '-'}\n"
            )

            f.write(
                f"URL           : "
                f"{channel['url']}\n"
            )

            f.write(
                f"ALIASES       : "
                f"{len(channel['aliases'])}\n\n"
            )

            # Русские
            f.write(
                "  [RUSSIAN / SOURCE]\n"
            )

            for alias in channel["aliases"]:

                if alias["language"] in (
                    "ru",
                    "source",
                ):

                    f.write(
                        f"    {alias['alias']}"
                        f"    <{alias['type']}>"
                        f"    [{alias['confidence']}]\n"
                    )

            f.write("\n")

            # Латинские
            f.write(
                "  [LATIN / INTERNATIONAL]\n"
            )

            for alias in channel["aliases"]:

                if alias["language"] in (
                    "latin",
                    "en",
                    "mixed",
                ):

                    f.write(
                        f"    {alias['alias']}"
                        f"    <{alias['type']}>"
                        f"    [{alias['confidence']}]\n"
                    )

            f.write("\n")

            # Все
            f.write(
                "  [ALL ALIASES]\n"
            )

            for number, alias in enumerate(
                channel["aliases"],
                1,
            ):

                f.write(
                    f"    {number:04d}. "
                    f"{alias['alias']}"
                    f" | {alias['language']}"
                    f" | {alias['type']}"
                    f" | {alias['confidence']}\n"
                )

            f.write("\n")

            f.write(
                f"EXTINF:\n"
                f"{channel['extinf']}\n"
            )

            f.write(
                "\n"
                + "-" * 70
                + "\n\n"
            )

        # ----------------------------------------------------
        # ОРИГИНАЛЬНЫЕ ИМЕНА
        # ----------------------------------------------------

        f.write(
            "\n"
            + "=" * 70
            + "\n"
        )

        f.write(
            "ALL ORIGINAL NAMES\n"
        )

        f.write(
            "=" * 70
            + "\n\n"
        )

        for channel in channels:

            f.write(
                f"{channel['id']:06d} | "
                f"{channel['original_name']}\n"
            )

        # ----------------------------------------------------
        # ВСЕ УНИКАЛЬНЫЕ АЛИАСЫ
        # ----------------------------------------------------

        all_aliases = sorted(
            {
                alias["alias"]
                for channel in channels
                for alias in channel["aliases"]
            },
            key=str.casefold,
        )

        f.write(
            "\n"
            + "=" * 70
            + "\n"
        )

        f.write(
            "ALL UNIQUE ALIASES\n"
        )

        f.write(
            "=" * 70
            + "\n\n"
        )

        for number, alias in enumerate(
            all_aliases,
            1,
        ):

            f.write(
                f"{number:06d} | {alias}\n"
            )


# ============================================================
# JSON
# ============================================================

def save_json(
    channels: list[dict],
    filename: Path,
):

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            channels,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# DOWNLOAD
# ============================================================

def download_playlist() -> str:

    print("=" * 70)
    print("ЗАГРУЗКА PLAYLIST")
    print("=" * 70)

    response = requests.get(
        PLAYLIST_URL,
        timeout=TIMEOUT,
        headers={
            "User-Agent":
                "Mozilla/5.0 IPTV Database Builder"
        },
    )

    response.raise_for_status()

    text = response.text

    if "#EXTM3U" not in text[:2000]:

        raise ValueError(
            "Полученный файл не является корректным M3U."
        )

    print(
        f"Получено символов: {len(text):,}"
    )

    return text


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("IPTV CHANNEL DATABASE BUILDER")
    print("=" * 70)
    print()

    # 1. DOWNLOAD
    text = download_playlist()

    # 2. PARSE
    channels = parse_m3u(text)

    if not channels:

        raise RuntimeError(
            "Не найдено ни одного IPTV-канала."
        )

    # 3. STATISTICS
    total_aliases = sum(
        len(ch["aliases"])
        for ch in channels
    )

    unique_aliases = len({
        alias["alias"].casefold()
        for ch in channels
        for alias in ch["aliases"]
    })

    print()
    print("=" * 70)
    print("РЕЗУЛЬТАТ ПАРСИНГА")
    print("=" * 70)

    print(
        f"Каналов             : {len(channels):,}"
    )

    print(
        f"Алиасов             : {total_aliases:,}"
    )

    print(
        f"Уникальных алиасов  : {unique_aliases:,}"
    )

    # 4. SAVE
    save_csv(
        channels,
        CSV_FILE,
    )

    save_python(
        channels,
        PY_FILE,
    )

    save_txt(
        channels,
        TXT_FILE,
    )

    save_json(
        channels,
        JSON_FILE,
    )

    # 5. DONE
    print()
    print("=" * 70)
    print("ФАЙЛЫ СОЗДАНЫ")
    print("=" * 70)

    print(f"CSV  : {CSV_FILE}")
    print(f"PY   : {PY_FILE}")
    print(f"TXT  : {TXT_FILE}")
    print(f"JSON : {JSON_FILE}")

    print("=" * 70)


if __name__ == "__main__":
    main()