#!/bin/bash

export PYTHONPATH=/app/src

HEADLESS=$(echo "${APP__PLAYWRIGHT__HEADLESS:-true}" | tr '[:upper:]' '[:lower:]')

if [[ "$HEADLESS" == "true" || "$HEADLESS" == "1" || "$HEADLESS" == "yes" ]]; then
    echo "Headless mode detected - skipping Xvfb/VNC startup."
else
    export DISPLAY=:99

    # Clean up stale lock files from previous runs
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

    # Start Xvfb (virtual X server)
    echo "Starting Xvfb..."
    Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
    XVFB_PID=$!

    # Wait for Xvfb to be ready
    echo "Waiting for Xvfb to be ready..."
    for i in {1..30}; do
        if xdpyinfo -display :99 >/dev/null 2>&1; then
            echo "Xvfb is ready!"
            break
        fi
        if [ $i -eq 30 ]; then
            echo "ERROR: Xvfb failed to start after 30 attempts"
            exit 1
        fi
        sleep 1
    done

    # Start window manager
    echo "Starting Fluxbox..."
    fluxbox >/dev/null 2>&1 &
    sleep 1

    # Start VNC server
    echo "Starting x11vnc..."
    x11vnc -display :99 -forever -shared -rfbport 5900 -nopw >/dev/null 2>&1 &
    sleep 1

    # Start noVNC (web interface for VNC)
    echo "Starting noVNC..."
    websockify --web /usr/share/novnc 6080 localhost:5900 &
    sleep 1

    echo "VNC available on port 5900"
    echo "noVNC web interface available at http://localhost:6080/vnc.html"
    echo "DISPLAY=$DISPLAY"
fi

echo "Starting emulation worker..."
uv run taskiq worker app.tiq:broker -w 1 --max-fails 1
