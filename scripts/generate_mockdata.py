#!/usr/bin/env python3
"""
Generate frontend/src/data/mockData.ts from the content directory structure.

Usage:
    python scripts/generate_mockdata.py
        # uses content/ as a source, outputs to frontend/src/data/mockData.ts
    
    python scripts/generate_mockdata.py path/to/content
        # uses a specified directory as a source
    
    python scripts/generate_mockdata.py -o path/to/output.ts
        # uses content/ as a source, outputs to a specified file
    
    python scripts/generate_mockdata.py path/to/content -o path/to/output.ts
        # uses specified directory as a source, outputs to a specified file

Arguments:
    content_dir             Path to the content directory (default: content/)
    -o, --output FILE       Path to an output file (default: frontend/src/data/mockData.ts)
    -h, --help              Show this help message and exit
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import sys
from pathlib import Path

LANGUAGES = ['en', 'es', 'fr', 'bg']

VOICE_CONFIG = {
    'en': {'type': 'male', 'face': 1},
    'es': {'type': 'female', 'face': 2},
    'fr': {'type': 'male', 'face': 3},
    'bg': {'type': 'female', 'face': 4},
}

MOCK_PROVIDER = 'mockup'


def mock_voice_parameters(lang: str) -> dict:
    """Stand-in generation parameters, shaped like the real provider's metadata sidecar."""
    return {'voice': f'{lang}-mock-voice', 'speed': 1.0}


def ts_object_literal(d: dict) -> str:
    parts = []
    for key, value in d.items():
        if isinstance(value, str):
            parts.append(f"{key}: '{js_str(value)}'")
        else:
            parts.append(f'{key}: {value}')
    return '{ ' + ', '.join(parts) + ' }'

IMAGE_EXTS = {'.png', '.jpg', '.jpeg'}

def read_csv(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [row[0].strip() for row in csv.reader(f) if row]


def folder_from_item_id(item_id: str) -> str:
    """Mirror ContentProvider._folder_name_from_item_id ('I_WaterBottle' -> 'water_bottle').

    Duplicated for the same reason as in testenv_seed.py: this script must not import backend/src.
    """
    _, _, remainder = item_id.partition('_')
    folder = re.sub(r'(?<!^)(?=[A-Z])', '_', remainder or item_id).lower()
    return re.sub(r'_+', '_', folder).strip('_')


def find_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    result = []
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() in IMAGE_EXTS and not f.name.endswith('.meta'):
            result.append(f)
    return result


def find_audio(directory: Path, stem: str, lang: str) -> Path | None:
    if not directory.exists():
        return None
    target = f'{stem}_{lang}.mp3'
    for f in directory.iterdir():
        if f.name == target:
            return f
    # Fallback: match stem_{lang}_*.mp3 (e.g. honey_en_01.mp3)
    prefix = f'{stem}_{lang}_'
    for f in directory.iterdir():
        if f.suffix == '.mp3' and f.stem.startswith(prefix):
            return f
    return None


def js_str(s: str) -> str:
    return s.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def content_path_uri(p: Path, content_root: Path) -> str:
    try:
        rel = p.relative_to(content_root)
        return f"/content/{rel.as_posix()}"
    except ValueError:
        return ''


def build_items(cat_dir: Path, content_root: Path, cat_key: str) -> tuple[list[dict], str]:
    prefix = None
    if (cat_dir / 'items_en.csv').exists():
        prefix = 'items'
        ids_file = cat_dir / 'items_ids.csv'
    elif (cat_dir / 'images_en.csv').exists():
        prefix = 'images'
        ids_file = cat_dir / 'images_names.csv'
    else:
        return [], ''

    item_ids = read_csv(ids_file)
    item_names: dict[str, list[str]] = {}
    for lang in LANGUAGES:
        item_names[lang] = read_csv(cat_dir / f'{prefix}_{lang}.csv')

    items = []
    category_thumbnail = ''
    for i_idx in range(len(item_ids)):
        name_en = item_names['en'][i_idx] if i_idx < len(item_names['en']) else ''
        # items_* content names each folder after the item id; the legacy images_* layout after the English name.
        if prefix == 'items':
            folder = folder_from_item_id(item_ids[i_idx])
        else:
            folder = name_en.lower().replace(' ', '_')
        item_dir = cat_dir / folder

        imgs = find_images(item_dir)
        img_uris = [content_path_uri(img, content_root) for img in imgs]

        thumbnail = img_uris[0] if img_uris else ''
        if not category_thumbnail and img_uris:
            category_thumbnail = thumbnail

        voices = []
        for lang in LANGUAGES:
            audio = find_audio(item_dir, folder, lang)
            audio_uri = content_path_uri(audio, content_root) if audio else ''
            trans_name = item_names[lang][i_idx] if i_idx < len(item_names[lang]) else ''
            vc = VOICE_CONFIG[lang]
            voices.append({
                'id': f'{folder}-{lang}',
                'name': trans_name,
                'language': lang,
                'type': vc['type'],
                'fileUri': audio_uri,
                'provider': MOCK_PROVIDER,
                'parameters': mock_voice_parameters(lang),
            })

        items.append({
            'id': item_ids[i_idx],
            'name': name_en,
            'images': img_uris,
            'thumbnailUri': thumbnail,
            'voices': voices,
        })

    # Fallback thumbnail: use category-level image
    if not category_thumbnail:
        cat_imgs = find_images(cat_dir)
        if cat_imgs:
            category_thumbnail = content_path_uri(cat_imgs[0], content_root)

    return items, category_thumbnail


def build_placeholder_items(cat_key: str) -> list[dict]:
    return []


def generate(content_path: Path, script_path: Path | None = None, output_path: Path | None = None) -> str:
    lines: list[str] = []

    # Header comment with generator info and date
    if script_path and output_path:
        try:
            rel = Path(script_path).resolve().relative_to(output_path.parent.resolve())
            lines.append(f"// Generated by {rel} on {datetime.date.today()}")
        except ValueError:
            lines.append(f"// Generated by {script_path.name} on {datetime.date.today()}")
    else:
        lines.append(f"// Generated on {datetime.date.today()}")
    lines.append('')

    # Read category-level data
    cat_ids = read_csv(content_path / 'categories_ids.csv')
    cat_names: dict[str, list[str]] = {}
    for lang in LANGUAGES:
        cat_names[lang] = read_csv(content_path / f'categories_{lang}.csv')

    descriptions: dict[str, list[str]] = {}
    for lang in LANGUAGES:
        descriptions[lang] = read_csv(content_path / f'descriptions_{lang}.csv')

    # Header
    lines.append("import type { Voice, Item, Image } from '../types/viewModels'")
    lines.append('')
    lines.append('interface MockCategory {')
    lines.append('  id: string')
    lines.append('  name: Record<string, string>')
    lines.append('  description: Record<string, string>')
    lines.append('  images: Image[]')
    lines.append('  archived: boolean')
    lines.append('  thumbnailUri?: string')
    lines.append('  voices: Voice[]')
    lines.append('  items: Item[]')
    lines.append('}')
    lines.append('')
    lines.append('export const mockCategories: MockCategory[] = [')

    for idx in range(len(cat_ids)):
        cat_key = cat_names['en'][idx].lower()
        cat_dir = content_path / cat_key

        if not cat_dir.is_dir():
            continue

        items, cat_thumb = build_items(cat_dir, content_path, cat_key)

        # Use category-level image matching the category name if available
        for f in sorted(cat_dir.iterdir()):
            if f.suffix.lower() in IMAGE_EXTS and not f.name.endswith('.meta') and f.stem == cat_key:
                cat_thumb = content_path_uri(f, content_path)
                break

        # Build category name dict
        name_parts = []
        for lang in LANGUAGES:
            val = cat_names[lang][idx] if idx < len(cat_names[lang]) else ''
            name_parts.append(f"{lang}: '{js_str(val)}'")

        cat_name_en = cat_names['en'][idx] if idx < len(cat_names['en']) else cat_key

        # Category voices
        cat_voices = []
        for lang in LANGUAGES:
            audio = find_audio(cat_dir, cat_key, lang)
            audio_uri = content_path_uri(audio, content_path) if audio else ''
            trans_name = cat_names[lang][idx] if idx < len(cat_names[lang]) else ''
            vc = VOICE_CONFIG[lang]
            cat_voices.append({
                'id': f'{cat_key}-v-{lang}',
                'name': trans_name,
                'language': lang,
                'type': vc['type'],
                'fileUri': audio_uri,
                'provider': MOCK_PROVIDER,
                'parameters': mock_voice_parameters(lang),
            })

        # Description
        desc_parts = []
        for lang in LANGUAGES:
            val = descriptions[lang][idx] if idx < len(descriptions[lang]) else ''
            desc_parts.append(f"{lang}: '{js_str(val)}'")

        lines.append('  {')
        lines.append(f"    id: '{cat_ids[idx]}',")
        lines.append(f"    name: {{ {', '.join(name_parts)} }},")
        lines.append(f"    description: {{ {', '.join(desc_parts)} }},")
        lines.append('    archived: false,')
        lines.append(f"    thumbnailUri: '{js_str(cat_thumb)}',")
        lines.append('    images: [')
        for f in sorted(cat_dir.iterdir()):
            if f.suffix.lower() in IMAGE_EXTS and not f.name.endswith('.meta'):
                stem = f.stem
                if stem.startswith(f'{cat_key}_') and len(stem) == len(cat_key) + 3:
                    num = stem[len(cat_key) + 1:]
                    if num.isdigit():
                        uri = content_path_uri(f, content_path)
                        lines.append(f"      {{ id: '{cat_ids[idx]}-img-{num}', name: '{cat_name_en} {num}', language: 'en', iconUri: '{js_str(uri)}' }},")
        lines.append('    ],')
        lines.append('    voices: [')
        for v in cat_voices:
            lines.append('      {')
            lines.append(f"        id: '{v['id']}',")
            lines.append(f"        name: '{js_str(v['name'])}',")
            lines.append(f"        language: '{v['language']}',")
            lines.append(f"        type: '{v['type']}',")
            lines.append(f"        fileUri: '{js_str(v['fileUri'])}',")
            lines.append(f"        provider: '{js_str(v['provider'])}',")
            lines.append(f"        parameters: {ts_object_literal(v['parameters'])},")
            lines.append('      },')
        lines.append('    ],')
        lines.append('    items: [')
        for item in items:
            lines.append('      {')
            lines.append(f"        id: '{item['id']}',")
            lines.append(f"        name: '{js_str(item['name'])}',")
            img_list = ', '.join(f"'{p}'" for p in item['images'])
            lines.append(f"        images: [{img_list}],")
            lines.append(f"        thumbnailUri: '{js_str(item['thumbnailUri'])}',")
            lines.append('        voices: [')
            for v in item['voices']:
                lines.append('          {')
                lines.append(f"            id: '{v['id']}',")
                lines.append(f"            name: '{js_str(v['name'])}',")
                lines.append(f"            language: '{v['language']}',")
                lines.append(f"            type: '{v['type']}',")
                lines.append(f"            fileUri: '{js_str(v['fileUri'])}',")
                lines.append(f"            provider: '{js_str(v['provider'])}',")
                lines.append(f"            parameters: {ts_object_literal(v['parameters'])},")
                lines.append('          },')
            lines.append('        ],')
            lines.append('      },')
        lines.append('    ],')
        lines.append('  },')
    lines.append(']')
    lines.append('')
    return '\n'.join(lines)


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    parser = argparse.ArgumentParser(
        description='Generate frontend/src/data/mockData.ts from content directory'
    )
    parser.add_argument(
        'content_dir',
        nargs='?',
        default=str(project_root / 'content'),
        help='Path to the content directory (default: content/)',
    )
    parser.add_argument(
        '-o', '--output',
        default=str(project_root / 'frontend/src/data/mockData.ts'),
        help='Path to output file (default: frontend/src/data/mockData.ts)',
    )
    args = parser.parse_args()

    content_path = Path(args.content_dir)
    if not content_path.is_absolute():
        content_path = (project_root / content_path).resolve()
    else:
        content_path = content_path.resolve()
    if not content_path.is_dir():
        print(f"Error: '{content_path}' is not a directory", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()
    else:
        out_path = out_path.resolve()

    output = generate(content_path, script_path=Path(__file__), output_path=out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output)
    print(f"Generated {out_path}")


if __name__ == '__main__':
    main()
