#!/usr/bin/python3
"""
Exports the names of folders in a specified directory to CSV files.
- Primary CSV: folder names with underscores replaced by spaces.
- Secondary CSV: same folder names formatted in PascalCase, prefixed with 'I_', saved with a '_names' suffix before the extension.

- Input folder can be provided as a program argument; current directory is used if not specified.
- Hidden directories (starting with '.') are ignored.
- Output file defaults to images.csv (created in the current working directory, unless a path is provided). The PascalCase file will then be images_names.csv.
"""

from pathlib import Path
import csv
import sys
import argparse
import re


def export_folder_names_to_csv(input_dir: Path, output_filename: str = "images.csv") -> None:
    """Scan the specified directory for subfolders and export their names to CSV files.

    Behavior:
    - Only immediate subdirectories of input_dir are included.
    - Hidden directories (names starting with '.') are ignored.
    - Each folder name has '_' replaced with ' '.
    - The primary output CSV contains one folder name per row, sorted alphabetically.
    - Additionally, a second CSV is created with the same base filename but with a '_names' suffix,
      containing the same folders formatted in PascalCase and prefixed with 'I_' (e.g., "my_folder" -> "I_MyFolder").
    """
    input_dir = Path(input_dir)

    def to_pascal(s: str) -> str:
        # Split on any non-alphanumeric characters and convert to PascalCase
        parts = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
        return "".join(p[:1].upper() + p[1:].lower() if p else "" for p in parts)

    try:
        # Collect immediate subdirectories, ignoring dot-prefixed (hidden) directories
        folder_names = [p.name for p in input_dir.iterdir() if p.is_dir() and not p.name.startswith('.')]
    except FileNotFoundError:
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(2)
    except NotADirectoryError:
        print(f"Input path is not a directory: {input_dir}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"Error while listing directories in {input_dir}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Replace underscores with spaces and sort
    cleaned_names = sorted(name.replace("_", " ") for name in folder_names)
    pascal_names = ["I_" + to_pascal(name) for name in cleaned_names]

    # Resolve output path (default to current working directory if relative)
    output_path = Path(output_filename)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    # Derive second output path with '_names' suffix before extension
    pascal_output_path = output_path.with_name(f"{output_path.stem}_names{output_path.suffix}")

    # Write primary CSV
    try:
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for name in cleaned_names:
                writer.writerow([name])
    except Exception as exc:
        print(f"Error while writing CSV to {output_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Write PascalCase CSV (prefixed with 'I_')
    try:
        with pascal_output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for name in pascal_names:
                writer.writerow([name])
    except Exception as exc:
        print(f"Error while writing CSV to {pascal_output_path}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Export immediate subfolder names of a directory to two CSV files: one with spaces (from underscores) and one PascalCase prefixed with 'I_' ('_names' suffix). Hidden directories are ignored."
        )
    )
    parser.add_argument(
        "input_folder",
        nargs="?",
        default=".",
        help="Directory to scan for immediate subfolders (default: current directory).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="images.csv",
        help="Output CSV filename (default: images.csv).",
    )

    args = parser.parse_args()
    export_folder_names_to_csv(args.input_folder, args.output)
