#!/bin/sh
# Stop everything this app starts and check its ports are free. The serving bridge
# cancels and deletes its platform agent on SIGTERM, which takes a few platform
# calls: give it up to a minute. npm starts the bridge and replayer with
# `--app=larco-serving`, which is what is matched (other examples' bridges and
# replayers share the script names).
HERE=$(cd "$(dirname "$0")/.." && pwd)
pkill -TERM -f "serving-bridge.js --app=larco-serving" 2>/dev/null
i=0
while pgrep -f "serving-bridge.js --app=larco-serving" >/dev/null 2>&1; do
	i=$((i + 1))
	[ $i -gt 60 ] && { echo "the bridge is still cleaning up after 60 s; see its log, or run python fit/cleanup.py"; break; }
	sleep 1
done
pkill -f "replayer.js --app=larco-serving" 2>/dev/null
pkill -f "mosquitto -c $HERE/mosquitto/mosquitto.conf" 2>/dev/null
pkill -f "vite dev --port 5182" 2>/dev/null
sleep 1
busy=0
for p in 1892 9010 5182; do
	if lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then
		echo "port $p still held by: $(lsof -nP -iTCP:$p -sTCP:LISTEN | awk 'NR==2{print $1" (pid "$2")"}')"
		busy=1
	fi
done
[ $busy -eq 0 ] && echo "all ports free (1892 · 9010 · 5182)"
[ -f "$HERE/../fit/out/app_serving_state.json" ] && echo "fit/out/app_serving_state.json is still there: run python fit/cleanup.py --yes"
exit 0
