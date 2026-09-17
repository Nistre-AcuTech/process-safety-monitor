#!/usr/bin/env bash
# 2-hourly scan → Postgres (no git). Installed on the box via /etc/cron.d/psm.
set -uo pipefail
cd /opt/process-safety-monitor || exit 1
mkdir -p logs
echo "===== $(date -u +'%Y-%m-%d %H:%M:%S') =====" >> logs/scan.log
docker compose --env-file .env -f ops/docker-compose.yml --profile scan run --rm scanner >> logs/scan.log 2>&1
rc=$?
echo "===== exit $rc =====" >> logs/scan.log

# Propagate the scanner's status. Until 2026-09-16 this script ended on the echo
# above, so it ALWAYS returned 0 — a failed scan was indistinguishable from a
# successful one to anything that shelled out to it, including the 2-hourly cron
# entry in /etc/cron.d/psm. That is the same silent-success shape that let the
# scanner stay dead for 8 weeks in Jun-Aug 2026.
exit $rc
