#!/usr/bin/env python3
"""Resolve YouTube channel names to channel IDs via yt-dlp.

Usage:
    python tools/resolve_channel_ids.py

This script reads data/channels_seed.json and attempts to resolve
placeholder YouTube channel IDs to real ones using yt-dlp.
Requires yt-dlp to be installed: pip install yt-dlp

Note: This is a helper for initial setup. Real channel IDs should be
manually verified and updated in channels_seed.json.
"""

import json
import subprocess
from pathlib import Path

from src.executables import resolve_executable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
YT_DLP_BIN = resolve_executable("yt-dlp")


# Known YouTube channel IDs (manually curated for accuracy)
KNOWN_CHANNEL_IDS = {
    "All-In Podcast": "UCESLZhusAkFfsNsApnjF_Cg",
    "BG2Pod": "UCnVOBpTEBYMPKqc8Fro1Ewg",
    "Lex Fridman Podcast": "UCSHZKyawb77ixDdsGog4iWA",
    "Acquired": "UCRhV1rWFSRUIp_hFgAlTVng",
    "The Knowledge Project": "UCiGcMB_FXQPD6d8AiYJRVXg",
    "Invest Like the Best": "UCV0iBa5lpTM-bYVLIXnXCTQ",
    "Dwarkesh Podcast": "UC2LCFMxIkk0VtFbPaX3s00A",
    "No Priors": "UCaHRaH2k2KTXvHfSiSJFe0A",
    "20VC": "UCf0PBRjhf0rF8fWBIxTuoWA",
    "My First Million": "UCN64HIrZNqFQYZ2BuyY-4zg",
    "The Tim Ferriss Show": "UCznv7Vf9nBdJYvBagFdAHWw",
    "Bankless": "UCAl9Ld79qaZxp9JzEOwd3aA",
    "a16z Podcast": "UCj2wTTBSZDCCjXuutsOZ17A",
    "Y Combinator YouTube": "UCcefcZRL2oaA_uBNeo5UOWg",
    "Founders Podcast": "UCxs-glMqQoMLC6R-ynbDkAA",
    "NVIDIA GTC": "UCHuiy8bXnmK5nisYHUd1J5g",
}


def resolve_via_ytdlp(channel_name: str) -> str | None:
    """Try to resolve a channel ID via yt-dlp search."""
    try:
        result = subprocess.run(
            [YT_DLP_BIN, "--flat-playlist", "--print", "channel_id", "-I", "1",
             f"ytsearch1:{channel_name} YouTube channel"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def main():
    channels_file = DATA_DIR / "channels_seed.json"
    with open(channels_file) as f:
        channels = json.load(f)

    updated = 0
    for channel in channels:
        if not channel["youtube_channel_id"].startswith("PLACEHOLDER_"):
            continue

        name = channel["name"]
        if name in KNOWN_CHANNEL_IDS:
            channel["youtube_channel_id"] = KNOWN_CHANNEL_IDS[name]
            print(f"  ✓ {name}: {KNOWN_CHANNEL_IDS[name]} (from known list)")
            updated += 1
        else:
            resolved = resolve_via_ytdlp(name)
            if resolved:
                channel["youtube_channel_id"] = resolved
                print(f"  ✓ {name}: {resolved} (via yt-dlp)")
                updated += 1
            else:
                print(f"  ✗ {name}: could not resolve (manual lookup needed)")

    with open(channels_file, "w") as f:
        json.dump(channels, f, indent=2)

    print(f"\nResolved {updated}/{len(channels)} channels")


if __name__ == "__main__":
    main()
