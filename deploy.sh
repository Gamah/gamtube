#!/usr/bin/env bash
# deploy.sh — bootstrap gamtube on a fresh Ubuntu 24.04 LXC container
# Usage: sudo bash deploy.sh [--domain example.com]
set -euo pipefail

GAMTUBE_DIR="$(cd "$(dirname "$0")" && pwd)"
GAMTUBE_USER="${GAMTUBE_USER:-gamtube}"
DOMAIN="localhost"
PORT=8000

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --domain)  DOMAIN="$2";  shift 2 ;;
    --port)    PORT="$2";    shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

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

DEPLOY_DIR="/opt/gamtube"
VENV="$DEPLOY_DIR/.venv"
MEDIA_DIR="/var/lib/gamtube/media"
DATA_DIR="/var/lib/gamtube"

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
BASE_URL="http://$DOMAIN${PORT:+:$PORT}"
[[ "$PORT" == "80" || "$PORT" == "443" ]] && BASE_URL="http://$DOMAIN"

cat > "$DEPLOY_DIR/.env" <<EOF
DATABASE_URL=sqlite:///$DATA_DIR/gamtube.db
MEDIA_ROOT=$MEDIA_DIR
MEDIA_BASE_URL=$BASE_URL/media
STORAGE_BACKEND=local
BASE_URL=$BASE_URL
TEMP_DIR=/tmp
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
systemctl enable --now gamtube

echo ""
echo "==> Done."
echo "    Service : systemctl status gamtube"
echo "    Logs    : tail -f $DEPLOY_DIR/logs/app.log"
echo "    Submit  : curl -X POST $BASE_URL/submit -H 'Content-Type: application/json' -d '{\"url\":\"https://www.youtube.com/watch?v=jNQXAC9IVRw\"}'"
