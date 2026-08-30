#!/usr/bin/python3
#
# pip3 install openai --break-system-packages
#

import os
import time
import csv
import argparse
from typing import List
from openai import OpenAI

# ---------------------
# CONFIG
# ---------------------
OUTPUT_DIR = "tts_audio"  # Base name; actual output dir is tts_audio_{lang}
VOICE = "alloy"  # You can try others like "verse" or "blithe"
# MODEL = "gpt-4o-mini-tts"
MODEL = "tts-1-hd"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def load_words_from_csv(csv_path: str) -> List[str]:
    """
    Load words from a CSV file.

    The CSV may contain one or more columns. All non-empty cells will be
    treated as candidate words. Leading/trailing whitespace is stripped.
    """
    words: List[str] = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                for cell in row:
                    w = cell.strip()
                    if w:
                        words.append(w)
    except FileNotFoundError:
        print(f"CSV file not found: {csv_path}")
    return words


def generate_audio(words: List[str], lang: str) -> None:
    """
    Generate TTS audio files for the given words in the specified language.

    Args:
        words: List of words to synthesize, e.g., ["potato", "onion"].
        lang: Language notation, e.g., "en".
    """
    output_dir = f"{OUTPUT_DIR}_{lang}"
    os.makedirs(output_dir, exist_ok=True)

    if not words:
        print("No words provided. Nothing to generate.")
        return

    for word in words:
        # Replace spaces with underscores in the output filename
        safe_word = word.replace(" ", "_")
        filename = f"{safe_word}_{lang}.mp3"
        filepath = os.path.join(output_dir, filename)

        print(f"Generating audio for '{word}' ({lang}) → {filename}")

        with client.audio.speech.with_streaming_response.create(
                model=MODEL,
                voice=VOICE,
                input=word
        ) as response:
            response.stream_to_file(filepath)

        time.sleep(1)

    print(f"\n✅ Done! Audio files are in '{output_dir}' folder.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate TTS audio files for words from a CSV.")
    parser.add_argument(
        "csv_file",
        nargs="?",
        default="words.csv",
        help="Path to CSV file containing words (default: words.csv)"
    )
    parser.add_argument(
        "-l",
        "--lang",
        default="en",
        help="Language notation, e.g., 'en' (default: en)"
    )
    args = parser.parse_args()

    print(f"Using CSV file: {args.csv_file}")
    print(f"Language: {args.lang}")

    words = load_words_from_csv(args.csv_file)
    generate_audio(words, args.lang)
