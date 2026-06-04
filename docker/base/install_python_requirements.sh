#!/bin/sh
set -eu

python /opt/docker-base/resolve_requirements.py /app/pyproject.toml "${INSTALL_EXTRAS:-}" > /tmp/requirements.txt
pip install --upgrade pip
pip install --no-cache-dir -r /tmp/requirements.txt

if [ "${INSTALL_PLAYWRIGHT:-0}" = "1" ]; then
  playwright install chromium --with-deps
fi
