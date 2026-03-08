
Summary of what’s in place:

### 1. **Mustache template** – `scripts/templates/nordic_rush_plugin.cpp.mustache`

- **Includes**: `{{#includes}}` … `{{/includes}}` – generates one `#include` line per entry (all headers used for IModule registration).
- **Registrations**: `{{#registration_groups}}` with `{{namespace}}` and `{{#registrations}}` – each registration outputs `EGT_REGISTER_CLASS({{interface}}, {{class_name}});` (so interface and namespace come from the data; namespace is per group).
- **Export macro**: `{{export_guard}}` – used in `#if !defined({{export_guard}})` and `#define {{export_guard}} ...`.
- **extern "C"**: `{{{extern_c_block}}}` – unescaped block that each integration sets (Playground vs Hat differ here).

### 2. **Template data**

- **`includes`**: list of include paths, e.g. `"<Egt/ModuleName/ModuleName.h>"`.
- **`registration_groups`**: list of `{ "namespace": "Egt::Integration::Playground", "registrations": [ { "interface": "IModule", "class_name": "PlaygroundIntegration" }, ... ] }`.
- **`export_guard`**: e.g. `EGT_PLAYGROUND_NORDICRUSH_EXPORT` or `EGT_HAT_GAME_EXPORT`.
- **`extern_c_block`**: full `extern "C" { ... }` snippet as a single string (integration-specific).

### 3. **Docs and example**

- **`scripts/templates/README.md`** – describes the variables and has copy-paste examples for:
  - **Playground**: `IPlaygroundGameApi` / `IPlaygroundKernelApi` / `PlaygroundGame`, `create_object`/`destroy_object`, `create_game`/`destroy_game`.
  - **HAT**: anonymous namespace with `HatGame`, `games_` vector, `Egt::HatGameApi::IGame*` / `IFramework*`, single `create_object`/`destroy_object`.
- **`scripts/templates/nordic_rush_plugin_playground.json`** – full example payload for Playground (includes, registration_groups, export_guard, extern_c_block with `\n` in the string).

To render the template (e.g. with Python and `pystache` or `chevron`):

```bash
pip install pystache
python -c "
import json, pystache
with open('scripts/templates/nordic_rush_plugin_playground.json') as f:
    data = json.load(f)
with open('scripts/templates/nordic_rush_plugin.cpp.mustache') as f:
    print(pystache.render(f.read(), data))
"
```



Summary of what was implemented:

### 1. **New method: `generate_plugin_cpp_from_merge`** (on `ModulesConfigs`)

It runs the full pipeline:

1. **Module names** – `get_modules_names(merge_input_path, merged_path=merged_path)` from the merged config.
2. **Load ModulesInfo** – `ModulesInfo(source_dir, output_dir)` then `load(modules_info_path)` so names resolve to `Module` (namespace, include_file).
3. **Mustache data** – For each name, resolve one `Module`, normalize include to `"<...>"`, default interface `"IModule"`. Build `includes` (sorted set) and `registration_groups` (consecutive same namespace merged, order kept).
4. **Generate** – Call `generate_plugin_cpp(cpp_output_dir, template_data, output_filename)`.

Parameters: `merge_input_path`, `merged_path`, `modules_info_path`, `cpp_output_dir`, `export_guard`, `extern_c_block`, optional `cpp_filename`, optional `default_interface` (default `"IModule"`).

### 2. **CLI and `main()`**

When **`--merge FILE`** is used:

- Config is merged (step 1).
- Module names are printed.
- If **`--generate-plugin-cpp`** is set:
  - **`--modules-info FILE`** – path to `modules_info.json` (required).
  - **`--export-guard MACRO`** – e.g. `EGT_PLAYGROUND_NORDICRUSH_EXPORT` (required).
  - **`--extern-c-file FILE`** – path to a file whose contents are the full `extern "C" { ... }` block (required).
  - **`--plugin-cpp-filename NAME`** – output .cpp name (default: `<merge-stem>.cpp`).
  - The generated .cpp is written under the merge **`--output-dir`** (or next to the merged JSON if no `--output-dir`).

### 3. **Import of `ModulesInfo`**

`ModulesInfo` and `Module` are imported from `modules_info`. If the first import fails (e.g. wrong cwd), the script prepends `scripts/tools` to `sys.path` and retries so it works when run from the repo root.

### Example

```bash
# 1) Produce modules_info.json (e.g. via modules_info.py)
# 2) Create a file with the extern "C" block, e.g. playground_extern_c.txt
# 3) Run merge + generate:

python scripts/tools/modules_cmakegen.py \
  --source-dir /path/to/configs \
  --output-dir /path/to/out \
  --merge playground_nordic_rush.json \
  --generate-plugin-cpp \
  --modules-info /path/to/modules_info.json \
  --export-guard EGT_PLAYGROUND_NORDICRUSH_EXPORT \
  --extern-c-file /path/to/playground_extern_c.txt
```

Result: merged config and module names as before, plus a generated `playground_nordic_rush.cpp` in `--output-dir` (or `--plugin-cpp-filename` if set).
