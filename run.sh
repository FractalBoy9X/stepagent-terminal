#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$project_dir/.venv/bin/python" ]; then
    exec "$project_dir/.venv/bin/python" "$project_dir/run.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$project_dir/run.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$project_dir/run.py" "$@"
fi
echo 'Python 3.10+ is required. Install Python, then run this launcher again.' >&2
exit 127
