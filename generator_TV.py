from __future__ import annotations

import argparse
from pathlib import Path


def build_playlist(start: int, end: int, base_url: str) -> str:
    lines = ["#EXTM3U"]

    for channel_number in range(start, end + 1):
        title = f"Channel {channel_number}"
        lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{title}" group-title="Cinerama",{title}')
        lines.append(f"{base_url}/{channel_number}/tracks-v1a1/mono.m3u8")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Cinerama M3U playlist.")
    parser.add_argument("--start", type=int, default=1, help="First channel number")
    parser.add_argument("--end", type=int, default=1500, help="Last channel number")
    parser.add_argument(
        "--base-url",
        default="https://stream1.cinerama.uz",
        help="Base URL for stream sources"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("playlist.m3u"),
        help="Output file path"
    )

    args = parser.parse_args()

    if args.start < 1:
        raise ValueError("start must be >= 1")
    if args.end < args.start:
        raise ValueError("end must be >= start")

    playlist = build_playlist(args.start, args.end, args.base_url)
    args.output.write_text(playlist, encoding="utf-8")

    total = args.end - args.start + 1
    print(f"Плейлист успешно сгенерирован: {args.output} ({total} каналов)")


if __name__ == "__main__":
    main()