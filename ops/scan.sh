#!/usr/bin/env bash
# 2-hourly scan → Postgres (no git). Installed on the box via /etc/cron.d/psm.
set -uo pipefail
cd /opt/process-safety-monitor || exit 1
mkdir -p logs
echo "===== $(date -u +'%Y-%m-%d %H:%M:%S') =====" >> logs/scan.log
docker compose --env-file .env -f ops/docker-compose.yml --profile scan run --rm scanner >> logs/scan.log 2>&1
echo "===== exit $? =====" >> logs/scan.log
