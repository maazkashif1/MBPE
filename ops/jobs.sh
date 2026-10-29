#!/bin/sh
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
UV=${UV:-$(command -v uv)}
MODEL=${MODEL:-data/models/phase6/f4c4365501a9272b}
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
JOBS="live ingest rss liquidations dashboard"
INGEST_HOUR=$(date -j -f "%Y-%m-%d %H:%M %z" "$(date +%Y-%m-%d) 02:30 +0000" +%-H)
INGEST_MINUTE=$(date -j -f "%Y-%m-%d %H:%M %z" "$(date +%Y-%m-%d) 02:30 +0000" +%-M)

usage() {
  echo "usage: ops/jobs.sh install|reload|stop|status|diff|logs [job ...]"
  echo "jobs: $JOBS (default: all)"
  exit 1
}

render() {
  sed -e "s#__REPO__#$REPO#g" -e "s#__UV__#$UV#g" -e "s#__MODEL__#$MODEL#g" \
    -e "s#__INGEST_HOUR__#$INGEST_HOUR#" -e "s#__INGEST_MINUTE__#$INGEST_MINUTE#" \
    "$REPO/ops/launchd/com.mbpe.$1.plist"
}

loaded() { launchctl print "$DOMAIN/com.mbpe.$1" >/dev/null 2>&1; }

running() { launchctl print "$DOMAIN/com.mbpe.$1" 2>/dev/null | grep -q "state = running"; }

unload() {
  loaded "$1" || return 0
  case $1 in live | ingest)
    while running "$1"; do echo "waiting for the running $1 job to finish"; sleep 5; done ;;
  esac
  launchctl bootout "$DOMAIN/com.mbpe.$1" || true
  n=0
  while loaded "$1"; do
    n=$((n + 1))
    [ $n -gt 60 ] && { echo "com.mbpe.$1 did not unload" >&2; exit 1; }
    sleep 0.5
  done
}

install_job() {
  mkdir -p "$AGENTS" "$REPO/data/logs"
  render "$1" > "$AGENTS/com.mbpe.$1.plist"
  plutil -lint -s "$AGENTS/com.mbpe.$1.plist"
  unload "$1"
  launchctl bootstrap "$DOMAIN" "$AGENTS/com.mbpe.$1.plist"
  echo "loaded com.mbpe.$1"
}

status() {
  printf "%-26s %-8s %-6s %s\n" job loaded pid "last exit"
  for j in $JOBS; do
    if loaded "$j"; then
      info=$(launchctl print "$DOMAIN/com.mbpe.$j")
      pid=$(echo "$info" | awk '/^\tpid = /{print $3}')
      code=$(echo "$info" | awk -F'= ' '/last exit code/{print $2; exit}')
      printf "%-26s %-8s %-6s %s\n" "com.mbpe.$j" yes "${pid:--}" "${code:--}"
    else
      printf "%-26s %-8s %-6s %s\n" "com.mbpe.$j" no - -
    fi
  done
}

[ $# -ge 1 ] || usage
cmd=$1
shift
[ $# -gt 0 ] && JOBS="$*"
for j in $JOBS; do [ -f "$REPO/ops/launchd/com.mbpe.$j.plist" ] || { echo "unknown job: $j" >&2; usage; }; done

case $cmd in
  install | reload) for j in $JOBS; do install_job "$j"; done; status ;;
  stop) for j in $JOBS; do unload "$j"; echo "stopped com.mbpe.$j"; done ;;
  status) status ;;
  diff) for j in $JOBS; do render "$j" | diff -u "$AGENTS/com.mbpe.$j.plist" - || true; done ;;
  logs) for j in $JOBS; do
          f=$(render "$j" | sed -n 's#.*<key>StandardOutPath</key><string>\(.*\)</string>.*#\1#p')
          echo "== $f"; tail -n 20 "$f" 2>/dev/null || echo "(no log yet)"
        done ;;
  *) usage ;;
esac
