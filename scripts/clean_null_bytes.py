# clean_directory_advanced.py
import os
import sys
import shutil
import argparse
from concurrent.futures import ThreadPoolExecutor


def process_file(file_path, backup=False, dry_run=False):
    try:
        with open(file_path, 'rb') as f:
            content = f.read()

        cleaned_content = content.replace(b'\x00', b'')
        removed = len(content) - len(cleaned_content)

        if removed == 0:
            return f"[OK]    {file_path} - no null bytes found"

        if dry_run:
            return f"[DRY]   {file_path} - would remove {removed} null bytes"

        if backup:
            backup_path = file_path + ".bak"
            shutil.copy2(file_path, backup_path)

        with open(file_path, 'wb') as f:
            f.write(cleaned_content)

        return f"[FIXED] {file_path} - removed {removed} null bytes"

    except Exception as e:
        return f"[ERROR] {file_path} - {e}"


def scan_directory(directory, backup=False, dry_run=False, workers=4):
    python_files = []

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(
            lambda f: process_file(f, backup, dry_run),
            python_files
        )

    for result in results:
        print(result)


def main():
    parser = argparse.ArgumentParser(description="Remove null bytes from Python files in a directory.")
    parser.add_argument("directory", help="Target directory")
    parser.add_argument("--backup", action="store_true", help="Create backup files (.bak)")
    parser.add_argument("--dry-run", action="store_true", help="Only report changes without modifying files")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers (default: 4)")

    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: {args.directory} is not a valid directory")
        sys.exit(1)

    scan_directory(
        args.directory,
        backup=args.backup,
        dry_run=args.dry_run,
        workers=args.workers
    )


if __name__ == "__main__":
    main()