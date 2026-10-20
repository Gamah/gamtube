#!/usr/bin/env bash
# Upgrade yt-dlp inside the gamtube venv and restart the service only if the
# version actually moved. Installed and driven by gamtube-update-ytdlp.timer.
set -euo pipefail

DEPLOY_DIR="${1:-/opt/gamtube}"
RUN_USER="${2:-gamtube}"
VENV="$DEPLOY_DIR/.venv"

version() { "$VENV/bin/python" -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo none; }

before="$(version)"
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet --upgrade 'yt-dlp[default,curl-cffi]'
after="$(version)"

if [ "$before" = "$after" ]; then
  echo "yt-dlp $after already current"
  exit 0
fi

echo "yt-dlp $before -> $after; restarting gamtube"
systemctl restart gamtube
