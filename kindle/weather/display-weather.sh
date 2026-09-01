#!/bin/sh

cd "$(dirname "$0")"
rm weather-script-output.png

stop framework

lipc-set-prop -i com.lab126.powerd preventScreenSaver 1
lipc-set-prop com.lab126.pillow disableEnablePillow disable

if wget http://54.213.36.230/weather-script-output.png; then
	eips -f -g weather-script-output.png
else
	sleep 60
	if wget http://54.213.36.230/weather-script-output.png; then
	eips -f -g weather-script.output.png
	else
	eips -f -g weather-image-error.png
	fi

fi
