#!/usr/bin/env bash
# Freshness watchdog for process-safety-monitor.
#
# The 2-hourly scan died on 2026-06-19 and nobody noticed until 2026-08-14 — cron
# was active, the containers were up, and the dashboard rendered a perfectly normal
# page built from an 8-week-old snapshot. Nothing on this box asserts that the data
# is actually current. This does.
#
# Reads meta.last_updated straight from Postgres (the same value /api/events serves),
# compares it to now, and records a verdict. Exit 0 = fresh, 1 = stale, 2 = can't tell.
#
# Installed via /etc/cron.d/psm-freshness. The verdict is surfaced at SSH login by
# /etc/update-motd.d/99-psm-freshness, because this box has no MTA and PSM's SMTP
# credentials are empty — there is no channel that can email anyone.

set -uo pipefail
cd /opt/process-safety-monitor || exit 2

# 2-hourly cadence, so 6h = three consecutive missed runs. Tunable without editing cron.
THRESHOLD_HOURS="${PSM_STALE_AFTER_HOURS:-6}"

LOG_DIR=/opt/process-safety-monitor/logs
STATUS_FILE="$LOG_DIR/freshness.status"
LOG="$LOG_DIR/freshness.log"
mkdir -p "$LOG_DIR"

now_iso="$(date -u +'%Y-%m-%d %H:%M:%S')"

record() {
    # $1 = verdict line. Overwrites the status file (current state) and appends to the log (history).
    printf '%s\n' "$1" > "$STATUS_FILE"
    printf '%s  %s\n' "$now_iso" "$1" >> "$LOG"
}

last_updated="$(docker exec psm-postgres-1 psql -U psm -d psm -tAc \
    "select value from meta where key='last_updated'" 2>/dev/null | tr -d '[:space:]')"

if [ -z "$last_updated" ]; then
    record "UNKNOWN: cannot read meta.last_updated (is psm-postgres-1 up?)"
    exit 2
fi

last_epoch="$(date -d "$last_updated" +%s 2>/dev/null)"
if [ -z "$last_epoch" ]; then
    record "UNKNOWN: unparseable last_updated value '$last_updated'"
    exit 2
fi

age_hours=$(( ( $(date -u +%s) - last_epoch ) / 3600 ))

if [ "$age_hours" -gt "$THRESHOLD_HOURS" ]; then
    record "STALE: PSM data is ${age_hours}h old (threshold ${THRESHOLD_HOURS}h, last scan ${last_updated}). The 2-hourly scan is not running — check $LOG_DIR/scan.log and $LOG_DIR/cron.log."
    exit 1
fi

record "OK: PSM data is ${age_hours}h old (threshold ${THRESHOLD_HOURS}h, last scan ${last_updated})."
exit 0
