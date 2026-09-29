#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, pydantic" 2>/dev/null; then
    echo "Ставлю зависимости..."
    .venv/bin/python -m pip install -r requirements.txt
fi

echo "Запускаю ProjectControl..."
exec .venv/bin/python run.py "$@"
