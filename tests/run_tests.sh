#!/usr/bin/env bash
# Offline test run for daily-brief using synthetic .ics fixtures.
# Usage: bash tests/run_tests.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/tests/fixtures"
CFG="$(mktemp)"
trap 'rm -f "$CFG"' EXIT

# Build a test config pointing at local fixture files (no network needed).
cat > "$CFG" <<EOF
timezone: America/New_York
location: "Maplewood, NJ"
calendars:
  - name: "Family events"
    kid: Family
    ical_url: file://$FIX/family.ics
    primary: true
  - name: "Emma soccer"
    kid: Emma
    sport: soccer
    ical_url: file://$FIX/kid1.ics
  - name: "Jake water polo"
    kid: Jake
    sport: water polo
    ical_url: file://$FIX/kid2.ics
venue_shortcuts:
  - match: "Riverside Field"
    short: "Riverside"
tomorrow_keywords: [tournament, flight, appointment, surgery, holiday, travel]
EOF

OUT="$(python3 "$ROOT/brief.py" --config "$CFG" --date 2026-10-12 --no-weather)"
echo "$OUT"
echo "=================== ASSERTIONS ==================="

pass=0; fail=0
check() { # check <description> <grep-args...>
  local desc="$1"; shift
  if echo "$OUT" | grep -q "$@"; then pass=$((pass+1)); echo "PASS: $desc";
  else fail=$((fail+1)); echo "FAIL: $desc"; fi
}

# 1. Dedupe: the same Soccer Game on two calendars shows exactly once,
#    keeping the primary (family) calendar's copy.
[ "$(echo "$OUT" | grep -c 'Soccer Game @ Riverside')" = "1" ] \
  && { pass=$((pass+1)); echo "PASS: Soccer Game deduped to one entry"; } \
  || { fail=$((fail+1)); echo "FAIL: Soccer Game deduped to one entry"; }
check "family copy's location kept (map link)" \
  'query=Riverside%20Field%2C%20100%20Park%20Ave'

# 2. Sorted by start time.
times="$(echo "$OUT" | grep -oE '^[0-9]{1,2}:[0-9]{2} (AM|PM)' | tr '\n' ' ')"
[ "$times" = "9:00 AM 3:00 PM 5:00 PM 6:30 PM 7:00 PM 7:30 PM 10:00 AM " ] \
  && { pass=$((pass+1)); echo "PASS: events sorted ($times)"; } \
  || { fail=$((fail+1)); echo "FAIL: events sorted (got: $times)"; }

# 3. Recurring events expand.
check "recurring Weekly Team Call expanded" "Weekly Team Call"

# 4. Same-kid overlap flagged (Emma's Team Dinner vs Film Session).
check "conflict flagged" 'CONFLICT: Emma'"'"'s "Team Dinner" overlaps "Film Session"'

# 5. Back-to-back at different locations flagged.
check "handoff note present" "handoff"

# 6. Tomorrow section only for keyword matches.
check "tomorrow header" "Tomorrow — Tuesday, October 13"
check "dentist appointment in Tomorrow section" "Dentist appointment"

# 7. Offline weather fallback.
check "weather unavailable fallback" "Weather unavailable"

echo "---------------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" = "0" ]
