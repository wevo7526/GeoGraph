#!/usr/bin/env bash
# Every endpoint a page actually calls, timed, with a shape check.
# API 200s are not a working product, but a slow or empty endpoint IS a broken
# page — this is the closest thing to clicking the six tabs from a shell.
# Usage: scripts/surface_check.sh [region] [dyad] ; GEOGRAPH_BASE overrides the
# target. Every number here is a p95 question, so run it WHILE the convergence
# loop is writing (/api/jobs shows what is in flight) — an idle system hides
# exactly the latency a reader would meet.
B="${GEOGRAPH_BASE:-https://geograph.up.railway.app}"
R="${1:-mena}"
DY="${2:-dyad:cow-2--cow-630}"

check () {  # url  jq-ish python expression  label
  local url="$1" expr="$2" label="$3"
  local out code time body
  body=$(curl -s -m 45 -w '\n%{http_code} %{time_total}' "$B$url")
  code=$(printf '%s' "$body" | tail -1 | cut -d' ' -f1)
  time=$(printf '%s' "$body" | tail -1 | cut -d' ' -f2)
  payload=$(printf '%s' "$body" | sed '$d')
  summary=$(printf '%s' "$payload" | python -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception as e: print('UNPARSEABLE'); raise SystemExit
try: print($expr)
except Exception as e: print('shape?', type(e).__name__)
" 2>/dev/null)
  printf '  %-6s %6ss  %-34s %s\n' "$code" "$time" "$label" "$summary"
}

echo "== the front door"
check "/api/health" "d['graph']" "health"
check "/api/stats" "f\"{d['nodes']['Event']} events, {d['edges']['AFFECTED']} affected\"" "stats"
check "/api/packs" "f\"{len(d.get('packs', d.get('rows', d)))} packs\"" "packs"
echo "== explorer"
check "/api/actors?region=$R&limit=50" "f\"{len(d.get('rows',d))} actors\"" "actors"
check "/api/events?region=$R&limit=50" "f\"{len(d['rows'])} events\"" "events"
check "/api/relations?limit=200" "f\"{len(d['rows'])} relations\"" "relations"
check "/api/regimes" "f\"{len(d)} segmentations\"" "regimes"
echo "== relationships"
check "/api/panel/dyads?region=$R" "f\"{len(d.get('rows',d))} dyads\"" "panel dyads"
check "/api/dyads?region=$R" "f\"{len(d.get('rows',d))} dyads\"" "dyads"
check "/api/impact/dyad/$DY?limit=20" "f\"{d['total']} measured events\"" "dyad timeline"
check "/api/impact/coverage?region=$R" "f\"{len(d['dyads'])} dyads covered\"" "impact coverage"
check "/api/precedent?dyad=$DY&region=$R" "f\"{len(d['episodes'])} comparable episodes\"" "precedent"
echo "== game theory"
check "/api/games/region?region=$R" "d.get('note','')[:40] or f\"{d['dyads_solved']} pairs, v{d.get('payload_version')}\"" "region map"
check "/api/games/dyad?dyad=$DY&region=$R" "d.get('note','')[:40] or f\"{d['dyad_name']}\"" "dyad game"
check "/api/games/explore?dyad=$DY&region=$R" "f\"{d['paths_enumerated']} courses\"" "explore"
echo "== markets + forecasts"
check "/api/forecasts?region=$R" "f\"{len(d['rows'])} forecasts\"" "forecasts"
check "/api/forecasts/calibration?region=$R" "('pending' if d.get('pending') else f\"brier {d.get('brier')}\")" "scoreboard"
check "/api/trading/backtest?region=$R" "f\"{d['summary']['quarters_traded']} quarters traded\"" "paper book"
check "/api/events/coverage?region=$R" "f\"{len(d.get('rows',d))} rows\"" "events coverage"
echo "== case studies"
check "/api/case-studies" "f\"{len(d['rows'])} studies\"" "list"
check "/api/case-studies/twelve-day-war" "f\"{d['status']}, {d['measured']} measurements\"" "twelve-day war"
check "/api/case-studies/dynamic?dyad=$DY" "f\"{d['measured']} measurements\"" "dynamic study"
echo "== the loop"
check "/api/jobs" "' '.join(f\"{j['name']}={j['runs']}/{j['failures']}\" for j in d['jobs'])" "jobs runs/fails"
