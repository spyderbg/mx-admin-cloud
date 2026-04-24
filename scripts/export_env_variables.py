#!/usr/bin/env python3

import os
from pathlib import Path

def main():
    # Get the directory of the current file
    current_file_dir = Path(__file__).resolve().parent

    # Resolve <current_file>/../content and ../archive
    content_dir = (current_file_dir / "../content").resolve()
    archive_dir = (current_file_dir / "../archive").resolve()

    # Print the export commands for the terminal
    print(f'export CONTENT_DIR="{content_dir}"')
    print(f'export ARCHIVE_DIR="{archive_dir}"')

if __name__ == "__main__":
    main()
