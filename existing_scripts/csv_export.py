#!/usr/bin/python3
#
# pip3 install pandas --break-system-packages
# pip3 install openpyxl --break-system-packages
#

import argparse
import csv
import os
import os.path
import re

import pandas as pd
from openpyxl.styles import Font

g_image_extensions = ('.png', '.jpg')
g_audio_extensions = ('.wav', '.mp3')

g_csv_filename = 'Assets.csv'
g_excel_filename = 'Assets.xlsx'

g_default_path = '../../Assets/_Client/Resources/Content'


def get_lang_code(filename):
    filename_lower = os.path.splitext(filename)[0].lower()
    if filename_lower.endswith('_bg'):
        return 'BG'
    if filename_lower.endswith('_es'):
        return 'ES'
    if filename_lower.endswith('_sp'):
        return 'SP'
    elif filename_lower.endswith('_fr'):
        return 'FR'
    elif filename_lower.endswith('_it'):
        return 'IT'
    elif filename_lower.endswith('_de'):
        return 'DE'
    return 'EN'


def get_description(filepath):
    parts = os.path.normpath(filepath).split(os.sep)
    description = parts[-2] if len(parts) > 1 else ''
    return description.replace('_', ' ')


def to_cap_snake_case(text):
    text = re.sub(r'[^a-zA-Z0-9]', '_', text)
    text = re.sub(r'_+', '_', text)
    return text.upper()


def to_camel_case(text):
    text = re.sub(r'_([a-zA-Z])', lambda m: m.group(1).upper(), text)
    return text


def to_pascal_case(text):
    if text == '':
        return text
    text = re.sub(r'_([a-zA-Z])', lambda m: m.group(1).upper(), text)
    return text[0].upper() + text[1:]


def get_id_asset(description, file_type, lang_code):
    type_abbr = 'I' if file_type == 'Image' else 'V'
    desc_snake = to_pascal_case(description.replace(' ', '_'))
    return f"A_{desc_snake}_{type_abbr}_{lang_code}"


def remove_extension(path):
    return os.path.splitext(path)[0]


def get_entry_id_asset(entry):
    return entry[0]


def get_entry_type(entry):
    return entry[1]


def get_entry_resource_path(entry):
    return entry[5]


def get_category(entry):
    resource_path = get_entry_resource_path(entry)
    base_dir = resource_path.split(os.sep)[0] if os.sep in resource_path else resource_path
    return to_pascal_case(base_dir)


def find_files(root_folder):
    file_entries = []
    for dirpath, dirnames, filenames in os.walk(root_folder):
        relative_folder = os.path.relpath(dirpath, root_folder)
        for f in filenames:
            lower_f = f.lower()
            resource_path = os.path.join(relative_folder, f)
            resource_path_no_ext = remove_extension(resource_path)
            description = get_description(resource_path)
            color = ''
            if lower_f.endswith(g_image_extensions):
                lang_code = get_lang_code(f)
                id_asset = get_id_asset(description, 'Image', lang_code)
                file_entries.append([id_asset, 'Image', lang_code, description, color, resource_path_no_ext])
            elif lower_f.endswith(g_audio_extensions):
                lang_code = get_lang_code(f)
                id_asset = get_id_asset(description, 'Voice', lang_code)
                file_entries.append([id_asset, 'Voice', lang_code, description, color, resource_path_no_ext])
    return file_entries


def group_files_by_resource_path(file_entries):
    grouped_files = {}
    for entry in file_entries:
        resource_path = os.path.dirname(get_entry_resource_path(entry))
        if resource_path not in grouped_files:
            grouped_files[resource_path] = []
        grouped_files[resource_path].append(entry)

    # sort files in each group - images first, then audio
    for group in grouped_files.values():
        group.sort(key=lambda x: (0 if get_entry_type(x) == 'Image' else 1, get_entry_id_asset(x)))

    return grouped_files


def group_files_by_category(grouped_files):
    grouped_files_by_category = {}

    for resource_path, entries in grouped_files.items():
        category = get_category(entries[0])
        if category not in grouped_files_by_category:
            grouped_files_by_category[category] = []
        grouped_files_by_category[category].append({resource_path: entries})

    # sort elements in each category by resource_path
    for category in grouped_files_by_category:
        grouped_files_by_category[category].sort(key=lambda x: list(x.keys())[0])

    return grouped_files_by_category


def export_to_csv(grouped_files_by_category, csv_filename):
    if os.path.exists(csv_filename):
        os.remove(csv_filename)
    with open(csv_filename, mode='w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['ID:Asset', 'type', 'lang_code', 'description', 'color', 'resource_path'])
        writer.writerow(['', '', '', '', '', ''])
        writer.writerow(['', '', '', '', '', ''])

        # Ensure categories are exported in alphabetical order for CSV as well
        for category in sorted(grouped_files_by_category.keys(), key=lambda c: c.lower()):
            category_files = grouped_files_by_category[category]
            for grouped_files in category_files:
                for resource_path, entries in sorted(grouped_files.items(), key=lambda x: x[1][0][3] if x[1] else ''):
                    for entry in entries:
                        writer.writerow(entry)
                    writer.writerow(['', '', '', '', '', ''])

    print(f"Exported file list to {csv_filename}.")


def convert_csv_to_excel(csv_filename, excel_filename):
    df = pd.read_csv(csv_filename, header=None)
    df.to_excel(excel_filename, index=False)

    # lock the first row
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
    wb = load_workbook(excel_filename)
    ws = wb.active
    ws.freeze_panes = 'B4'  # freeze both column A and the first 3 rows

    # apply gray background to the first two rows
    blue_fill = PatternFill(start_color='0070c0', end_color='0070c0', fill_type='solid')
    white_blue_fill = PatternFill(start_color='deebf7', end_color='deebf7', fill_type='solid')
    white_font = Font(color='FFFFFF', name='Calibri', bold=True)  # White text color with Calibri font
    for row in range(1, 3):
        for cell in ws[row]:
            cell.fill = blue_fill
            cell.font = white_font

    for row in range(3, 4):
        for cell in ws[row]:
            cell.fill = white_blue_fill
            cell.font = white_font

    # adjust column widths
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception as e:
                print(f"Error processing cell value: {str(e)}")
        adjusted_width = (max_length + 2)
        ws.column_dimensions[column_letter].width = adjusted_width

    wb.save(excel_filename)

    print(f"Converted {csv_filename} to {excel_filename}.")


def export_to_excel(grouped_files_by_category, excel_filename):
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font

    if os.path.exists(excel_filename):
        os.remove(excel_filename)

    wb = Workbook()

    header = ['ID:Asset', 'type', 'lang_code', 'description', 'color', 'resource_path']
    blue_fill = PatternFill(start_color='0070c0', end_color='0070c0', fill_type='solid')
    white_blue_fill = PatternFill(start_color='deebf7', end_color='deebf7', fill_type='solid')
    white_font = Font(color='FFFFFF', name='Calibri')
    white_font_bold = Font(color='FFFFFF', name='Calibri', bold=True)

    # Ensure sheets are created in alphabetical order by category name
    for idx, category in enumerate(sorted(grouped_files_by_category.keys(), key=lambda c: c.lower())):
        category_files = grouped_files_by_category[category]
        if idx == 0:
            ws = wb.active
            ws.title = category
        else:
            ws = wb.create_sheet(category)

        ws.append(header)
        ws.append(['', '', '', '', '', ''])
        ws.append(['', '', '', '', '', ''])

        for grouped_files in category_files:
            for resource_path, entries in sorted(grouped_files.items(), key=lambda x: x[1][0][3] if x[1] else ''):
                for entry in entries:
                    ws.append(entry)
                ws.append(['', '', '', '', '', ''])

        ws.freeze_panes = 'B3'

        for row in range(1, 2):
            for cell in ws[row]:
                cell.fill = blue_fill
                cell.font = white_font_bold

        for row in range(2, 3):
            for cell in ws[row]:
                cell.fill = white_blue_fill
                cell.font = white_font

        # adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except Exception as e:
                    print(f"Error processing cell value: {str(e)}")
            adjusted_width = (max_length + (2 if column_letter != 'A' else 4))
            ws.column_dimensions[column_letter].width = adjusted_width

    wb.save(excel_filename)

    print(f"Exported file list to {excel_filename}.")


def main(working_directory=None):
    # Allow overriding the working directory via function parameter or CLI argument
    # Priority: function arg > CLI arg > CWD
    if working_directory is None:
        working_directory = os.getcwd()

    # Normalize and validate directory
    working_directory = os.path.abspath(working_directory)
    if not os.path.isdir(working_directory):
        print(f"Error: Provided directory does not exist or is not a directory: {working_directory}")
        raise SystemExit(1)

    file_entries = find_files(working_directory)
    grouped_files = group_files_by_resource_path(file_entries)
    grouped_files_by_category = group_files_by_category(grouped_files)

    csv_path = os.path.join(working_directory, g_csv_filename)
    excel_path = os.path.join(working_directory, g_excel_filename)

    export_to_csv(grouped_files_by_category, csv_path)
    export_to_excel(grouped_files_by_category, excel_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Export image and audio assets to CSV and Excel grouped by categories.')

    # Optional flag -d/--directory and optional positional for backward compatibility
    parser.add_argument(
        '-d', '--directory',
        dest='directory',
        default=None,
        help=f'Root directory to scan if no arguments is set uses ({g_default_path})'
    )
    parser.add_argument(
        'directory_positional',
        nargs='?',
        default=None,
        help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    # Resolve directory: flag takes precedence over positional; fallback to default_path when none provided
    directory = args.directory if args.directory is not None else args.directory_positional
    if directory is None or str(directory).strip() == '':
        directory = g_default_path
    # Normalize default to be relative to this script's location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(directory):
        directory = os.path.abspath(os.path.join(script_dir, directory))

    print(f"Working directory: {directory}")
    main(directory)
