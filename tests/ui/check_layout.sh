#!/usr/bin/env bash
# Visual regression check for the story column after the map closes (needs the gstack
# `browse` tool and a running server). Usage: tests/ui/check_layout.sh [base_url]
set -euo pipefail
BASE="${1:-http://localhost:8047}"
B="${BROWSE:-$HOME/.claude/skills/gstack/browse/dist/browse}"
HERE="$(cd "$(dirname "$0")" && pwd)"
"$B" viewport 1280x800 >/dev/null
"$B" goto "$BASE" >/dev/null
"$B" eval "$HERE/to_map.js" >/dev/null
for _ in $(seq 1 40); do sleep 2; "$B" js "!document.querySelector('#map').hidden" | grep -q true && break; done
"$B" click "#map-close" >/dev/null
sleep 1
# The story column must stay centered: same left edge as main, and narrower than the viewport.
"$B" js "(() => { const t = document.querySelector('#text').getBoundingClientRect(); const s = getComputedStyle(document.querySelector('#scene')); return (s.position !== 'fixed' && t.width < 700 && Math.abs(t.left + t.width / 2 - innerWidth / 2) < 40) ? 'LAYOUT OK' : 'LAYOUT BROKEN ' + JSON.stringify([s.position, t.left, t.width]); })()"
