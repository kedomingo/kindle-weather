#!/bin/sh
# Runs on the jailbroken Kindle. Add to the Kindle's crontab, e.g. every 15 min:
#   */15 * * * * /mnt/us/weather/display-weather.sh
#
# Point this at the Orange Pi on your LAN. A static lease or a .local name is
# worth setting up so this URL never changes.
HOST="http://192.168.1.50:8080"
IMAGE="weather-script-output.png"

cd "$(dirname "$0")" || exit 1
rm -f "$IMAGE"

stop framework

lipc-set-prop -i com.lab126.powerd preventScreenSaver 1
lipc-set-prop com.lab126.pillow disableEnablePillow disable

fetch() {
	wget -q -O "$IMAGE" "$HOST/$IMAGE" && [ -s "$IMAGE" ]
}

if fetch; then
	eips -f -g "$IMAGE"
else
	# The Pi may be mid-refresh or the wifi may still be associating.
	sleep 60
	if fetch; then
		eips -f -g "$IMAGE"
	else
		eips -f -g weather-image-error.png
	fi
fi
