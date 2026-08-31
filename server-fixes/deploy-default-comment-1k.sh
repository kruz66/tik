#!/bin/bash
set -euo pipefail
HOST="${1:-root@98.142.250.176}"
KEY="${2:-$HOME/.ssh/mekesh_deploy}"
scp -i "$KEY" -o StrictHostKeyChecking=no \
  server-fixes/static/clipper/js/panel.js \
  server-fixes/templates/clipper/panel.html \
  "$HOST:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" bash -s <<'REMOTE'
set -e
cp /tmp/panel.js /root/tiktok-cliper/static/clipper/js/panel.js
cp /tmp/panel.html /root/tiktok-cliper/templates/clipper/panel.html
cd /root/tiktok-cliper && ./venv/bin/python manage.py collectstatic --noinput
# also copy into staticfiles directly if collectstatic uses Manifest
cp /tmp/panel.js /root/tiktok-cliper/staticfiles/clipper/js/panel.js 2>/dev/null || true
systemctl restart tiktok-cliper.service
REMOTE
