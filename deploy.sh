#!/usr/bin/env bash
# deploy.sh — bootstrap gamtube on a fresh Ubuntu 24.04 LXC container
# Usage: sudo bash deploy.sh [--domain example.com] [--keep]
#   --keep  skip all prompts; reuse data-dir and password from existing .env
set -euo pipefail

GAMTUBE_DIR="$(cd "$(dirname "$0")" && pwd)"
GAMTUBE_USER="${GAMTUBE_USER:-gamtube}"
DOMAIN="localhost"
PORT=8000
PORT_EXPLICIT=false
DATA_DIR=""
KEEP=false
DEPLOY_DIR="/opt/gamtube"
TRANSCODE_ENABLED="false"

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --domain)   DOMAIN="$2";   shift 2 ;;
    --port)     PORT="$2"; PORT_EXPLICIT=true; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --keep)     KEEP=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# --- --keep: pull existing values, skip prompts ---
if [[ "$KEEP" == "true" ]]; then
  if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    echo "Error: --keep requires an existing deployment at $DEPLOY_DIR/.env"
    exit 1
  fi
  if [[ -z "$DATA_DIR" ]]; then
    _existing_media="$(grep -E '^MEDIA_ROOT=' "$DEPLOY_DIR/.env" | cut -d= -f2-)"
    DATA_DIR="${_existing_media%/media}"
    [[ -z "$DATA_DIR" ]] && { echo "Error: cannot read DATA_DIR from existing .env"; exit 1; }
  fi
  ADMIN_PASSWORD="$(grep -E '^ADMIN_PASSWORD=' "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
  TRANSCODE_ENABLED="$(grep -E '^TRANSCODE_ENABLED=' "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2- || echo "false")"
else
  # --- prompt for data dir if not supplied ---
  if [[ -z "$DATA_DIR" ]]; then
    read -rp "Data directory [/var/lib/gamtube]: " _data_input
    DATA_DIR="${_data_input:-/var/lib/gamtube}"
  fi

  # --- prompt for admin password ---
  _existing_admin_pw=""
  if [[ -f "$DEPLOY_DIR/.env" ]]; then
    _existing_admin_pw="$(grep -E '^ADMIN_PASSWORD=' "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
  fi
  if [[ -n "$_existing_admin_pw" ]]; then
    read -rsp "Admin password for /manage [leave blank to keep existing]: " _admin_pw_input
    echo
    ADMIN_PASSWORD="${_admin_pw_input:-$_existing_admin_pw}"
  else
    read -rsp "Admin password for /manage (blank = panel disabled): " _admin_pw_input
    echo
    ADMIN_PASSWORD="$_admin_pw_input"
  fi

  read -rp "Re-encode downloads to H.264 MP4? Slower but guarantees browser compatibility [y/N]: " _transcode_input
  if [[ "$_transcode_input" =~ ^[Yy] ]]; then
    TRANSCODE_ENABLED="true"
  else
    TRANSCODE_ENABLED="false"
  fi
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
"$VENV/bin/pip" install --quiet -r "$DEPLOY_DIR/requirements.txt"

echo "==> Writing .env"
BASE_URL="http://$DOMAIN"
if [[ "$PORT_EXPLICIT" == "true" && "$PORT" != "80" && "$PORT" != "443" ]]; then
  BASE_URL="http://$DOMAIN:$PORT"
fi

cat > "$DEPLOY_DIR/.env" <<EOF
DATABASE_URL=sqlite:///$DATA_DIR/gamtube.db
MEDIA_ROOT=$MEDIA_DIR
MEDIA_BASE_URL=$BASE_URL/media
STORAGE_BACKEND=local
BASE_URL=$BASE_URL
TEMP_DIR=/tmp
TRANSCODE_ENABLED=$TRANSCODE_ENABLED
ADMIN_PASSWORD=$ADMIN_PASSWORD
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
ExecStart=$VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=append:$DEPLOY_DIR/logs/app.log
StandardError=append:$DEPLOY_DIR/logs/app.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable gamtube
systemctl restart gamtube

echo ""
echo "==> Done."
echo "    Service : systemctl status gamtube"
echo "    Logs    : tail -f $DEPLOY_DIR/logs/app.log"
echo "    Submit  : curl -X POST $BASE_URL/submit -H 'Content-Type: application/json' -d '{\"url\":\"https://www.youtube.com/watch?v=jNQXAC9IVRw\"}'"
