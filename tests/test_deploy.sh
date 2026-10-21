#!/usr/bin/env bash
# Run deploy.sh --keep against a sandbox with every privileged command stubbed,
# then assert on the .env and unit files it produced.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(mktemp -d)/dtest"
mkdir -p "$ROOT"/{deploy,systemd,stub,data}
trap 'rm -rf "$(dirname "$ROOT")"' EXIT
cp -r "$SRC"/deploy.sh "$SRC"/requirements.txt "$SRC"/scripts "$ROOT/"
mkdir -p "$ROOT/web" "$ROOT/bot" "$ROOT/core"

# point the script at the sandbox
sed -i \
  -e "s#^DEPLOY_DIR=\"/opt/gamtube\"#DEPLOY_DIR=\"$ROOT/deploy\"#" \
  -e "s#/etc/systemd/system#$ROOT/systemd#g" \
  "$ROOT/deploy.sh"

# stub the privileged / heavy commands
for cmd in apt-get useradd rsync chown sudo systemctl python3.12; do
  printf '#!/bin/sh\nexit 0\n' > "$ROOT/stub/$cmd"
  chmod +x "$ROOT/stub/$cmd"
done
# id: pretend the service user exists so useradd is skipped
printf '#!/bin/sh\nexit 0\n' > "$ROOT/stub/id"; chmod +x "$ROOT/stub/id"
# rsync is stubbed, so lay down the venv the script expects to use
mkdir -p "$ROOT/deploy/.venv/bin"
for b in pip alembic python uvicorn; do
  printf '#!/bin/sh\nexit 0\n' > "$ROOT/deploy/.venv/bin/$b"
  chmod +x "$ROOT/deploy/.venv/bin/$b"
done

# seed a prior deployment: old-style .env with no bot keys at all
cat > "$ROOT/deploy/.env" <<EOF
DATABASE_URL=sqlite:///$ROOT/data/gamtube.db
MEDIA_ROOT=$ROOT/data/media
MEDIA_BASE_URL=https://tube.example.com/media
STORAGE_BACKEND=local
BASE_URL=https://tube.example.com
TEMP_DIR=/tmp
VIDEO_TTL_HOURS=48
TRANSCODE_ENABLED=true
ADMIN_PASSWORD=sekrit
EOF
# and a prior unit, so the port is read back from it
mkdir -p "$ROOT/systemd"
printf 'ExecStart=/x/uvicorn app.main:app --host 0.0.0.0 --port 8123\n' \
  > "$ROOT/systemd/gamtube.service"
# a leftover from the old layout, which this deploy must retire
printf '[Service]\nExecStart=/x/python -m bot\n' \
  > "$ROOT/systemd/gamtube-bot.service"

PATH="$ROOT/stub:$PATH" bash "$ROOT/deploy.sh" --keep > "$ROOT/out.log" 2>&1 || {
  echo "FAIL: deploy.sh exited $?"; cat "$ROOT/out.log"; exit 1; }

env_file="$ROOT/deploy/.env"
fails=0
check() { # check <label> <pattern> <file>
  if grep -qE -- "$2" "$3"; then :; else echo " - FAIL $1 (no match for: $2)"; fails=$((fails+1)); fi
}

echo "== existing web values preserved =="
check "BASE_URL kept"        '^BASE_URL=https://tube\.example\.com$' "$env_file"
check "TTL kept"             '^VIDEO_TTL_HOURS=48$'                  "$env_file"
check "transcode kept"       '^TRANSCODE_ENABLED=true$'              "$env_file"
check "admin password kept"  '^ADMIN_PASSWORD=sekrit$'               "$env_file"

echo "== bot keys added with defaults, token left empty =="
check "token empty"          '^DISCORD_BOT_TOKEN=$'                  "$env_file"
check "embed wait default"   '^EMBED_WAIT_SECONDS=3\.0$'             "$env_file"
check "link ttl default"     '^LINK_TTL_SECONDS=300$'               "$env_file"
check "item cap default"     '^MAX_ITEM_MB=100$'                    "$env_file"
check "memory cap default"   '^MAX_MEMORY_MB=512$'                  "$env_file"
check "force mp4 default"    '^FORCE_MP4=true$'                     "$env_file"
check "max links default"    '^MAX_LINKS_PER_MESSAGE=3$'             "$env_file"
check "shrink default"       '^SHRINK_ENABLED=true$'                 "$env_file"
check "shrink crf default"   '^SHRINK_CRF=30$'                       "$env_file"
check "shrink height"        '^SHRINK_MAX_HEIGHT=720$'               "$env_file"
check "opt-in on by default" '^CHANNEL_OPT_IN=true$'                 "$env_file"
check "opt-in permission"    '^OPT_IN_PERMISSION=embed_links$'      "$env_file"

echo "== units =="
check "web runs web.main"    'uvicorn web\.main:app'       "$ROOT/systemd/gamtube.service"
check "port read from unit"  '--port 8123'                 "$ROOT/systemd/gamtube.service"
if [ -f "$ROOT/systemd/gamtube-bot.service" ]; then
  echo " - FAIL a separate bot unit was written; the bot runs inside gamtube"
  fails=$((fails+1))
fi
if grep -q 'app\.main' "$ROOT/systemd/gamtube.service"; then
  echo " - FAIL unit still references app.main"; fails=$((fails+1)); fi
check "bot reported disabled" 'Bot     : disabled'         "$ROOT/out.log"
check "obsolete unit retired" 'Removing the obsolete'     "$ROOT/out.log"
if [ -f "$ROOT/systemd/gamtube-bot.service" ]; then
  echo " - FAIL leftover gamtube-bot.service was not removed"; fails=$((fails+1)); fi

echo "== second run with a token set =="
sed -i 's/^DISCORD_BOT_TOKEN=$/DISCORD_BOT_TOKEN=tok123/' "$env_file"
PATH="$ROOT/stub:$PATH" bash "$ROOT/deploy.sh" --keep > "$ROOT/out2.log" 2>&1 || {
  echo "FAIL: second run exited $?"; cat "$ROOT/out2.log"; exit 1; }
check "token preserved"      '^DISCORD_BOT_TOKEN=tok123$'  "$env_file"
check "bot reported enabled" 'Bot     : running inside gamtube'  "$ROOT/out2.log"

# every key the bot reads must be parseable as KEY=value for systemd
if grep -vE '^[A-Z0-9_]+=' "$env_file" | grep -q .; then
  echo " - FAIL .env has a line systemd cannot parse"; fails=$((fails+1)); fi

[ "$fails" -eq 0 ] && echo "deploy.sh sandbox tests pass" || { echo "$fails failure(s)"; exit 1; }
