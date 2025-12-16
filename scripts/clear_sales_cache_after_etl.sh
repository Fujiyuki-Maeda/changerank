#!/usr/bin/env bash
# Usage: ./clear_sales_cache_after_etl.sh /path/to/venv /path/to/project
# Example: ./clear_sales_cache_after_etl.sh /home/user/.venv /home/user/project

VENV_PATH="$1"
PROJECT_PATH="$2"

if [ -z "$VENV_PATH" ] || [ -z "$PROJECT_PATH" ]; then
  echo "Usage: $0 /path/to/venv /path/to/project"
  exit 1
fi

# Activate virtualenv
source "$VENV_PATH/bin/activate"
cd "$PROJECT_PATH" || exit 2

# Run management command to clear registered sales caches
python manage.py clear_sales_cache

# Deactivate
deactivate || true

exit 0
