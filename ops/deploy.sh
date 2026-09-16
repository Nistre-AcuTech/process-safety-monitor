#!/usr/bin/env bash
# Deploy process-safety-monitor by FILE-SYNC.
#
# Run from a workstation on the tailnet:  ops/deploy.sh
#
# ## Why file-sync and not `git pull` on the box
#
# The box runs a hand-managed working tree at /opt/process-safety-monitor: HEAD is
# months behind, ops/ was untracked for a long time, and ~20 tracked files show as
# modified. A `git pull` there would either refuse or clobber code that is live and
# not in git. Same call the Directory made (ops/deploy-directory.sh in acutech-tools):
# sync the files we changed, never run git on the box.
#
# ## What it does, in order
#
#   1. Refuses if the local tree is dirty or the tests don't pass — shipping something
#      that isn't committed is how the box drifted in the first place.
#   2. Backs up the box's copy of every file it is about to overwrite.
#   3. Syncs the scanner + web app source.
#   4. Rebuilds+restarts the web service, then runs ONE scan in the foreground so a broken deploy fails
#      here, loudly, instead of silently at the next 2-hourly cron.
#   5. Verifies the API serves and reports how fresh the data now is.
#
# Every step is echoed and any failure stops the script.
set -euo pipefail

BOX="${BOX:-deploy@100.111.201.112}"
REMOTE="${REMOTE:-/opt/process-safety-monitor}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- preflight
say "Preflight"
cd "$REPO"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "REFUSING: local tree has uncommitted changes." >&2
  echo "Commit them first — deploying something that isn't in git is how the box drifted." >&2
  git status --short >&2
  exit 1
fi

if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  echo "  running tests..."
  python -m pytest tests/ -q || {
    echo "REFUSING: tests fail. Set SKIP_TESTS=1 only if you know why." >&2
    exit 1
  }
fi

echo "  local HEAD: $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
ssh "$BOX" 'echo "  box reachable: $(hostname)"'

# ------------------------------------------------------------- backup first
# Cheap insurance: the box's copies are the only record of whatever drifted into them.
say "Back up the files we are about to overwrite"
ssh "$BOX" "
  set -e
  cd $REMOTE
  mkdir -p backups
  tar czf backups/pre-deploy-$STAMP.tgz \
      --ignore-failed-read \
      *.py ops/ docs/index.html 2>/dev/null || true
  ls -lh backups/pre-deploy-$STAMP.tgz
"

# -------------------------------------------------------------------- sync
# Explicit file list rather than the whole tree: the box holds a live .env, a logs/
# directory and a populated docs/data/ that must not be touched.
#
# rsync is preferred but is NOT present in Git Bash on Windows, which is where this
# actually gets run from — the first real run died here with "rsync: command not
# found" after it had already taken the backup. tar-over-ssh needs only tar and ssh,
# both of which Git Bash has, and transfers the same explicit file set.
say "Sync scanner + web app source"
cd "$REPO"
SYNC_PATHS=(
  *.py
  tests
  ops/scan.sh ops/docker-compose.yml ops/freshness_check.sh ops/deploy.sh
  requirements.txt Dockerfile
  docs/index.html
)

if command -v rsync >/dev/null 2>&1; then
  echo "  using rsync"
  rsync -az --relative \
    --exclude '__pycache__/' --exclude '*.pyc' \
    "${SYNC_PATHS[@]}" "$BOX:$REMOTE/"
else
  echo "  rsync not available — using tar over ssh"
  tar czf - --exclude='__pycache__' --exclude='*.pyc' "${SYNC_PATHS[@]}" \
    | ssh "$BOX" "tar xzf - -C $REMOTE"
fi
echo "  synced: ${#SYNC_PATHS[@]} path specs"

# ops/*.sh must stay LF and executable — a CRLF shebang is what killed the scanner
# for 8 weeks in Jun-Aug 2026. .gitattributes guards the checkout; this guards the box.
say "Normalise line endings + exec bits on ops/"
ssh "$BOX" "
  set -e
  cd $REMOTE/ops
  for f in *.sh; do
    sed -i 's/\r$//' \"\$f\"
    chmod +x \"\$f\"
  done
  head -c 20 scan.sh | od -c | head -2
  ls -l *.sh
"

# ------------------------------------------------------------------- apply
# The compose SERVICE is "web"; the container it produces is named psm-web. Passing the
# container name to `compose up` fails with "no such service: psm-web".
say "Rebuild + restart the web service"
ssh "$BOX" "cd $REMOTE && docker compose --env-file .env -f ops/docker-compose.yml up -d --build web"

# The real test. The scanner is a one-shot container under the 'scan' profile; running
# it here means an import error or a bad feed shows up now, not silently in 2 hours.
#
# NB: scan.sh's own exit status is useless — it has no `set -e` and its last command is
# an echo, so it returns 0 even when the scanner container fails. That is the same class
# of silent success that hid the 8-week outage. So read the verdict it writes into the
# log instead of trusting its exit code.
say "Run one scan in the foreground (this is the deploy's real verification)"
ssh "$BOX" "
  set -e
  cd $REMOTE
  bash ops/scan.sh
  verdict=\$(grep -a '^===== exit ' logs/scan.log | tail -1)
  echo \"  scan verdict: \$verdict\"
  [ \"\$verdict\" = '===== exit 0 =====' ] || exit 1
" || {
  echo >&2
  echo "DEPLOY FAILED: the scan did not exit 0." >&2
  echo "Check:    ssh $BOX 'tail -40 $REMOTE/logs/scan.log'" >&2
  echo "Rollback: ssh $BOX 'cd $REMOTE && tar xzf backups/pre-deploy-$STAMP.tgz && \\" >&2
  echo "            docker compose --env-file .env -f ops/docker-compose.yml up -d --build web'" >&2
  exit 1
}

# ------------------------------------------------------------------ verify
say "Verify"
ssh "$BOX" "
  cd $REMOTE
  echo '  --- last 15 scan log lines ---'
  tail -15 logs/scan.log
  echo '  --- freshness ---'
  bash ops/freshness_check.sh || true
  cat logs/freshness.status
  echo '  --- HazardEx rows now in the DB ---'
  docker exec psm-postgres-1 psql -U psm -d psm -tAc \
    \"select count(*) from events where source = 'HazardEx'\"
"

echo
echo "Done. Dashboard: https://monitor.acutechsoftware.com (behind Authentik)."
echo "Rollback: ssh $BOX 'cd $REMOTE && tar xzf backups/pre-deploy-$STAMP.tgz && docker compose --env-file .env -f ops/docker-compose.yml up -d --build web'"
