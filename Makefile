# Guest (deployed LXC) targets run against /opt/gamtube; dev targets run here.
DEPLOY_DIR ?= /opt/gamtube
VENV       := $(DEPLOY_DIR)/.venv

.PHONY: up status logs bot-logs update-ytdlp versions dev dev-bot migrate test

## pull this branch and redeploy, keeping every existing .env value
up:
	git pull --ff-only
	sudo bash deploy.sh --keep
	@$(MAKE) --no-print-directory versions

## service state, installed versions, next yt-dlp upgrade
status:
	systemctl status gamtube --no-pager || true
	systemctl status gamtube-bot --no-pager || true
	@$(MAKE) --no-print-directory versions
	systemctl list-timers gamtube-update-ytdlp.timer --no-pager

versions:
	@$(VENV)/bin/python -c 'import yt_dlp, fastapi, sqlalchemy, alembic, discord; print("yt-dlp", yt_dlp.version.__version__, "| fastapi", fastapi.__version__, "| sqlalchemy", sqlalchemy.__version__, "| alembic", alembic.__version__, "| discord.py", discord.__version__)'

logs:
	tail -f $(DEPLOY_DIR)/logs/app.log

bot-logs:
	tail -f $(DEPLOY_DIR)/logs/bot.log

## run the yt-dlp upgrade now instead of waiting for the timer
update-ytdlp:
	sudo systemctl start gamtube-update-ytdlp.service
	journalctl -u gamtube-update-ytdlp --no-pager -n 20

dev:
	python3 run.py

dev-bot:
	python3 -m bot

migrate:
	alembic upgrade head

## pure-function and deploy-sandbox checks; no venv or token needed
test:
	python3 tests/test_pure.py
	bash tests/test_deploy.sh
