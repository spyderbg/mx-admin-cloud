#!/usr/bin/python3
#
# pip3 install openai --break-system-packages
#

import argparse
import csv
import os
import logging
from openai import OpenAI

client = OpenAI()

LANG_MAP = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pl": "Polish",
    "uk": "Ukrainian",
    "ro": "Romanian",
    "nl": "Dutch",
    "bg": "Bulgarian"
}

def translate_text(text, target_lang_code, category=None):
    if not text.strip():
        return text

    target_lang_full = LANG_MAP.get(target_lang_code, target_lang_code)

    system_content = (
        "You are a professional translator. "
        f"Translate the given English word into {target_lang_full}. "
        "ALWAYS treat the input as a NOUN (object name). "
        "Return ONLY the translated noun, singular form, "
        "no articles, no explanations."
    )

    if category:
        system_content += f" The category of the translated word is {category}."

    # print(f"System Content:\n{system_content}\n")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": system_content
            },
            {
                "role": "user",
                "content": text
            }
        ],
        temperature=0
    )

    return response.choices[0].message.content.strip()

def translate_csv(input_file, target_lang_code, category=None):
    base, ext = os.path.splitext(input_file)
    output_file = base.replace("_en", f"_{target_lang_code}") + ext

    with open(input_file, newline="", encoding="utf-8") as infile, \
         open(output_file, "w", newline="", encoding="utf-8") as outfile:

        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        for row in reader:
            translated_row = [
                translate_text(cell, target_lang_code, category) for cell in row
            ]
            writer.writerow(translated_row)

    print(f"Translated file written to: {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Translate CSV words using OpenAI API")
    parser.add_argument(
        "csv_file",
        help="Input CSV file (English)"
    )
    parser.add_argument(
        "-l",
        "--languages",
        nargs="+",
        required=True,
        help="Target language codes (e.g. fr es bg)"
    )
    parser.add_argument(
        "-c",
        "--category",
        help="Word category for better translation context (e.g. shapes, animals)"
    )

    args = parser.parse_args()
    
    for lang_code in args.languages:
        print(f"Translating to {LANG_MAP.get(lang_code, lang_code)}...")
        translate_csv(args.csv_file, lang_code, args.category)

if __name__ == "__main__":
    main()

