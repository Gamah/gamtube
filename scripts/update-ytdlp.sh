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

# Both processes import yt-dlp as a library, so neither picks up a new version
# without a restart. Skip the bot when it isn't enabled.
echo "yt-dlp $before -> $after; restarting services"
systemctl restart gamtube
if systemctl is-enabled --quiet gamtube-bot 2>/dev/null; then
  systemctl restart gamtube-bot
fi
