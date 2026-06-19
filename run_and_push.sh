#!/usr/bin/env bash
# Linux port of run_and_push.ps1 — runs the 2-hourly scan in a container,
# then commits + pushes docs/data/events.json (→ GitHub Pages) if it changed.
# Installed on the box via /etc/cron.d/psm (runs as the deploy user).
set -uo pipefail

cd /opt/process-safety-monitor || exit 1
mkdir -p logs
STAMP="$(date -u +'%Y-%m-%d %H:%M:%S')"
{
  echo ""
  echo "===== $STAMP ====="
} >> logs/run.log

# Scan: code + data dir are the mounted repo; deps live in the image.
docker run --rm --env-file .env -v "$PWD:/app" -w /app psm:latest python -u main.py >> logs/run.log 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
  git add docs/data/events.json
  if ! git diff --cached --quiet; then
    git commit -m "Update events data" >> logs/run.log 2>&1
    git push >> logs/run.log 2>&1
    rc=$?
  else
    echo "no event changes" >> logs/run.log
  fi
fi

echo "===== exit $rc =====" >> logs/run.log
exit "$rc"
