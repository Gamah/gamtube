#!/usr/bin/env bash
# Upgrade yt-dlp inside the gamtube venv and restart the services only if the
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

# yt-dlp is imported as a library by the web app and by the bot inside it, so
# nothing picks up a new version without this restart.
echo "yt-dlp $before -> $after; restarting gamtube"
systemctl restart gamtube
