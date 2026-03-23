#!/usr/bin/bash

# Get the directory where this bash script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Full path to the Python script in the same directory
PYTHON_SCRIPT="$SCRIPT_DIR/export_env_variables.py"

# Run Python script and capture output
CMD="$(python3 "$PYTHON_SCRIPT")"

echo "Export environment variables:"
echo "$CMD"

# Execute the command
eval "$CMD"
