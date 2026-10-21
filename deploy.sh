#!/usr/bin/env bash
# deploy.sh — bootstrap gamtube on a fresh Ubuntu 24.04 LXC container
#
# One service, gamtube (uvicorn web.main:app). The Discord bot runs inside it
# when DISCORD_BOT_TOKEN is set — it has to, because the links it posts are
# served from that process's memory.
# Usage: sudo bash deploy.sh [--domain example.com] [--port 8000] [--data-dir DIR] [--keep]
#
# Interactive by default: every setting is prompted with the value from the
# existing /opt/gamtube/.env as its default, so pressing enter keeps it.
#   --keep  skip all prompts entirely; reuse every existing value verbatim
# On a re-run, nothing is ever silently reset — a setting only changes if you
# type a new value or pass a flag.
set -euo pipefail

GAMTUBE_DIR="$(cd "$(dirname "$0")" && pwd)"
GAMTUBE_USER="${GAMTUBE_USER:-gamtube}"
DOMAIN="localhost"
DOMAIN_EXPLICIT=false
PORT=8000
PORT_EXPLICIT=false
DATA_DIR=""
KEEP=false
DEPLOY_DIR="/opt/gamtube"
TRANSCODE_ENABLED="false"

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --domain)   DOMAIN="$2"; DOMAIN_EXPLICIT=true; shift 2 ;;
    --port)     PORT="$2"; PORT_EXPLICIT=true; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --keep)     KEEP=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# --- read a key out of the existing .env, empty if absent ---
_env_get() {
  [[ -f "$DEPLOY_DIR/.env" ]] || return 0
  grep -E "^$1=" "$DEPLOY_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true
}

# --- existing deployment values; these are the defaults for every prompt ---
EX_BASE_URL="$(_env_get BASE_URL)"
EX_MEDIA_ROOT="$(_env_get MEDIA_ROOT)"
EX_DATA_DIR="${EX_MEDIA_ROOT%/media}"
EX_ADMIN_PASSWORD="$(_env_get ADMIN_PASSWORD)"
EX_TRANSCODE="$(_env_get TRANSCODE_ENABLED)"
EX_TTL="$(_env_get VIDEO_TTL_HOURS)"
EX_TEMP_DIR="$(_env_get TEMP_DIR)"

# --- bot values; only the token is prompted for, the rest carry through ---
EX_DISCORD_TOKEN="$(_env_get DISCORD_BOT_TOKEN)"
EX_CHANNEL_OPT_IN="$(_env_get CHANNEL_OPT_IN)"
EX_OPT_IN_PERMISSION="$(_env_get OPT_IN_PERMISSION)"
EX_DISCORD_GUILDS="$(_env_get DISCORD_GUILDS)"
EX_DISCORD_CHANNELS="$(_env_get DISCORD_CHANNELS)"
EX_EMBED_WAIT="$(_env_get EMBED_WAIT_SECONDS)"
EX_MAX_ITEM_MB="$(_env_get MAX_ITEM_MB)"
EX_MAX_MEMORY_MB="$(_env_get MAX_MEMORY_MB)"
EX_LINK_TTL="$(_env_get LINK_TTL_SECONDS)"
EX_FORCE_MP4="$(_env_get FORCE_MP4)"
EX_BOT_DEBUG="$(_env_get BOT_DEBUG)"
EX_HUMAN_NOTICE="$(_env_get HUMAN_NOTICE)"
EX_CRAWLER_UAS="$(_env_get CRAWLER_USER_AGENTS)"
EX_MEDIA_UAS="$(_env_get MEDIA_USER_AGENTS)"
EX_MAX_LINKS="$(_env_get MAX_LINKS_PER_MESSAGE)"
EX_SHRINK_ENABLED="$(_env_get SHRINK_ENABLED)"
EX_SHRINK_CRF="$(_env_get SHRINK_CRF)"
EX_SHRINK_HEIGHT="$(_env_get SHRINK_MAX_HEIGHT)"
EX_WATCH_DOMAINS="$(_env_get WATCH_DOMAINS)"
EX_BOT_TEMP_DIR="$(_env_get BOT_TEMP_DIR)"

# --- listen port lives in the systemd unit, not .env ---
if [[ "$PORT_EXPLICIT" != "true" ]]; then
  EX_PORT="$(grep -oE '\-\-port [0-9]+' /etc/systemd/system/gamtube.service 2>/dev/null | tail -n1 | grep -oE '[0-9]+' || true)"
  [[ -n "$EX_PORT" ]] && PORT="$EX_PORT"
fi

# --- base URL: --domain/--port win; else prompt; else keep existing ---
if [[ "$DOMAIN_EXPLICIT" == "true" || "$PORT_EXPLICIT" == "true" ]]; then
  BASE_URL="http://$DOMAIN"
  if [[ "$PORT_EXPLICIT" == "true" && "$PORT" != "80" && "$PORT" != "443" ]]; then
    BASE_URL="http://$DOMAIN:$PORT"
  fi
else
  BASE_URL="${EX_BASE_URL:-http://localhost:8000}"
fi

# --- defaults for everything else ---
DATA_DIR="${DATA_DIR:-${EX_DATA_DIR:-/var/lib/gamtube}}"
ADMIN_PASSWORD="$EX_ADMIN_PASSWORD"
TRANSCODE_ENABLED="${EX_TRANSCODE:-false}"
VIDEO_TTL_HOURS="${EX_TTL:-24}"
TEMP_DIR="${EX_TEMP_DIR:-/tmp}"
DISCORD_BOT_TOKEN="$EX_DISCORD_TOKEN"
CHANNEL_OPT_IN="${EX_CHANNEL_OPT_IN:-true}"
OPT_IN_PERMISSION="${EX_OPT_IN_PERMISSION:-embed_links}"
DISCORD_GUILDS="$EX_DISCORD_GUILDS"
DISCORD_CHANNELS="$EX_DISCORD_CHANNELS"
EMBED_WAIT_SECONDS="${EX_EMBED_WAIT:-3.0}"
MAX_ITEM_MB="${EX_MAX_ITEM_MB:-100}"
MAX_MEMORY_MB="${EX_MAX_MEMORY_MB:-512}"
LINK_TTL_SECONDS="${EX_LINK_TTL:-300}"
FORCE_MP4="${EX_FORCE_MP4:-true}"
BOT_DEBUG="${EX_BOT_DEBUG:-false}"
HUMAN_NOTICE="${EX_HUMAN_NOTICE:-true}"
CRAWLER_USER_AGENTS="$EX_CRAWLER_UAS"
MEDIA_USER_AGENTS="$EX_MEDIA_UAS"
MAX_LINKS_PER_MESSAGE="${EX_MAX_LINKS:-3}"
SHRINK_ENABLED="${EX_SHRINK_ENABLED:-true}"
SHRINK_CRF="${EX_SHRINK_CRF:-30}"
SHRINK_MAX_HEIGHT="${EX_SHRINK_HEIGHT:-720}"
WATCH_DOMAINS="$EX_WATCH_DOMAINS"
BOT_TEMP_DIR="$EX_BOT_TEMP_DIR"

# --- prompt for each value; blank keeps the shown default ---
if [[ "$KEEP" != "true" ]]; then
  if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    echo "==> No existing deployment found; press enter to accept each default."
  else
    echo "==> Existing deployment found; press enter to keep each current value."
  fi

  if [[ "$DOMAIN_EXPLICIT" != "true" && "$PORT_EXPLICIT" != "true" ]]; then
    read -rp "Public base URL, including scheme [$BASE_URL]: " _input
    BASE_URL="${_input:-$BASE_URL}"
  fi
  BASE_URL="${BASE_URL%/}"

  read -rp "Data directory [$DATA_DIR]: " _input
  DATA_DIR="${_input:-$DATA_DIR}"

  if [[ -n "$ADMIN_PASSWORD" ]]; then
    read -rsp "Admin password for /manage [blank = keep existing]: " _input
    echo
    ADMIN_PASSWORD="${_input:-$ADMIN_PASSWORD}"
  else
    read -rsp "Admin password for /manage (blank = panel disabled): " _input
    echo
    ADMIN_PASSWORD="$_input"
  fi

  _transcode_hint="y/N"
  [[ "$TRANSCODE_ENABLED" == "true" ]] && _transcode_hint="Y/n"
  read -rp "Re-encode downloads to H.264 MP4? Slower but guarantees browser compatibility [$_transcode_hint]: " _input
  if [[ -n "$_input" ]]; then
    if [[ "$_input" =~ ^[Yy] ]]; then TRANSCODE_ENABLED="true"; else TRANSCODE_ENABLED="false"; fi
  fi

  read -rp "Hours until a video expires, 0 = never [$VIDEO_TTL_HOURS]: " _input
  VIDEO_TTL_HOURS="${_input:-$VIDEO_TTL_HOURS}"

  if [[ -n "$DISCORD_BOT_TOKEN" ]]; then
    read -rsp "Discord bot token [blank = keep existing, '-' = disable bot]: " _input
    echo
    case "$_input" in
      "") ;;
      -)  DISCORD_BOT_TOKEN="" ;;
      *)  DISCORD_BOT_TOKEN="$_input" ;;
    esac
  else
    read -rsp "Discord bot token (blank = bot disabled): " _input
    echo
    DISCORD_BOT_TOKEN="$_input"
  fi
fi

if [[ ! "$VIDEO_TTL_HOURS" =~ ^[0-9]+$ ]]; then
  echo "Error: VIDEO_TTL_HOURS must be a whole number, got '$VIDEO_TTL_HOURS'"
  exit 1
fi
if [[ ! "$BASE_URL" =~ ^https?:// ]]; then
  echo "Error: base URL must start with http:// or https://, got '$BASE_URL'"
  exit 1
fi
if [[ "$KEEP" == "true" && ! -f "$DEPLOY_DIR/.env" ]]; then
  echo "Error: --keep requires an existing deployment at $DEPLOY_DIR/.env"
  exit 1
fi

echo "==> Installing system dependencies"
apt-get update -qq
apt-get install -y -qq \
  python3.12 python3.12-venv python3.12-dev \
  ffmpeg \
  git \
  curl

# --- create service user ---
if ! id "$GAMTUBE_USER" &>/dev/null; then
  echo "==> Creating user $GAMTUBE_USER"
  useradd --system --shell /usr/sbin/nologin --home-dir /var/lib/gamtube \
    --create-home "$GAMTUBE_USER"
fi

VENV="$DEPLOY_DIR/.venv"
MEDIA_DIR="$DATA_DIR/media"

echo "==> Copying application to $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"
rsync -a --delete \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='media' \
  --exclude='*.db' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$GAMTUBE_DIR/" "$DEPLOY_DIR/"

echo "==> Creating Python venv"
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# --upgrade matters on a re-run: without it pip leaves an already-satisfied
# floor alone and the deploy never moves yt-dlp forward.
"$VENV/bin/pip" install --quiet --upgrade -r "$DEPLOY_DIR/requirements.txt"

echo "==> Writing .env (BASE_URL=$BASE_URL)"
cat > "$DEPLOY_DIR/.env" <<EOF
DATABASE_URL=sqlite:///$DATA_DIR/gamtube.db
MEDIA_ROOT=$MEDIA_DIR
MEDIA_BASE_URL=$BASE_URL/media
STORAGE_BACKEND=local
BASE_URL=$BASE_URL
TEMP_DIR=$TEMP_DIR
VIDEO_TTL_HOURS=$VIDEO_TTL_HOURS
TRANSCODE_ENABLED=$TRANSCODE_ENABLED
ADMIN_PASSWORD=$ADMIN_PASSWORD
DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN
CHANNEL_OPT_IN=$CHANNEL_OPT_IN
OPT_IN_PERMISSION=$OPT_IN_PERMISSION
DISCORD_GUILDS=$DISCORD_GUILDS
DISCORD_CHANNELS=$DISCORD_CHANNELS
EMBED_WAIT_SECONDS=$EMBED_WAIT_SECONDS
MAX_ITEM_MB=$MAX_ITEM_MB
MAX_MEMORY_MB=$MAX_MEMORY_MB
LINK_TTL_SECONDS=$LINK_TTL_SECONDS
FORCE_MP4=$FORCE_MP4
BOT_DEBUG=$BOT_DEBUG
HUMAN_NOTICE=$HUMAN_NOTICE
CRAWLER_USER_AGENTS=$CRAWLER_USER_AGENTS
MEDIA_USER_AGENTS=$MEDIA_USER_AGENTS
MAX_LINKS_PER_MESSAGE=$MAX_LINKS_PER_MESSAGE
SHRINK_ENABLED=$SHRINK_ENABLED
SHRINK_CRF=$SHRINK_CRF
SHRINK_MAX_HEIGHT=$SHRINK_MAX_HEIGHT
WATCH_DOMAINS=$WATCH_DOMAINS
BOT_TEMP_DIR=$BOT_TEMP_DIR
EOF

echo "==> Creating media and log directories"
mkdir -p "$MEDIA_DIR"
mkdir -p "$DEPLOY_DIR/logs"

echo "==> Fixing permissions"
chown -R "$GAMTUBE_USER:$GAMTUBE_USER" "$DEPLOY_DIR" "$DATA_DIR"

echo "==> Running migrations"
cd "$DEPLOY_DIR"
sudo -u "$GAMTUBE_USER" "$VENV/bin/alembic" upgrade head

echo "==> Installing systemd service"
cat > /etc/systemd/system/gamtube.service <<EOF
[Unit]
Description=gamtube video re-hosting
After=network.target

[Service]
Type=simple
User=$GAMTUBE_USER
WorkingDirectory=$DEPLOY_DIR
EnvironmentFile=$DEPLOY_DIR/.env
ExecStart=$VENV/bin/uvicorn web.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=append:$DEPLOY_DIR/logs/app.log
StandardError=append:$DEPLOY_DIR/logs/app.log

[Install]
WantedBy=multi-user.target
EOF

echo "==> Installing yt-dlp auto-update timer"
cat > /etc/systemd/system/gamtube-update-ytdlp.service <<EOF
[Unit]
Description=Upgrade yt-dlp for gamtube
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $DEPLOY_DIR/scripts/update-ytdlp.sh $DEPLOY_DIR $GAMTUBE_USER
EOF

cat > /etc/systemd/system/gamtube-update-ytdlp.timer <<EOF
[Unit]
Description=Daily yt-dlp upgrade for gamtube

[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable gamtube
systemctl restart gamtube
systemctl enable --now gamtube-update-ytdlp.timer

# An older install may have had the bot as its own unit; it lives in the web
# process now, so retire the leftover rather than leaving it crash-looping.
if [[ -f /etc/systemd/system/gamtube-bot.service ]]; then
  echo "==> Removing the obsolete gamtube-bot service"
  systemctl disable --now gamtube-bot 2>/dev/null || true
  rm -f /etc/systemd/system/gamtube-bot.service
  systemctl daemon-reload
fi

echo ""
echo "==> Done."
echo "    Service : systemctl status gamtube"
echo "    Logs    : tail -f $DEPLOY_DIR/logs/app.log"
if [[ -n "$DISCORD_BOT_TOKEN" ]]; then
  echo "    Bot     : running inside gamtube; grep gamtube.bot in the log"
else
  echo "    Bot     : disabled (no DISCORD_BOT_TOKEN)"
fi
echo "    yt-dlp  : systemctl list-timers gamtube-update-ytdlp.timer"
echo "    Submit  : curl -X POST $BASE_URL/submit -H 'Content-Type: application/json' -d '{\"url\":\"https://www.youtube.com/watch?v=jNQXAC9IVRw\"}'"
