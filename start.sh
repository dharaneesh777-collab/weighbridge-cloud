#!/bin/bash

echo "=== Starting Weighbridge Cloud on Render ==="

# Ensure directories exist
mkdir -p /var/lib/tailscale /var/run/tailscale /var/log

STATE_FILE="/var/lib/tailscale/tailscaled.state"

# 1. Restore persistent Tailscale state if available in environment
if [ -n "$TS_STATE_B64" ]; then
  echo "Restoring persistent Tailscale session from TS_STATE_B64..."
  echo "$TS_STATE_B64" | base64 -d > "$STATE_FILE"
  chmod 600 "$STATE_FILE"
  echo "Persistent state restored ($(wc -c <"$STATE_FILE") bytes)."
fi

# 2. Start tailscaled pointing to the persistent state file
/usr/sbin/tailscaled \
  --state="$STATE_FILE" \
  --tun=userspace-networking \
  --outbound-http-proxy-listen=localhost:1055 \
  --socks5-server=localhost:1055 >/var/log/tailscaled.log 2>&1 &

# 3. Handle connection without blocking Uvicorn
(
  echo "Waiting for tailscaled socket..."
  for i in $(seq 1 20); do
    STATUS_OUT=$(/usr/bin/tailscale status 2>&1 || true)
    if echo "$STATUS_OUT" | grep -qv "failed to connect"; then
      break
    fi
    sleep 0.5
  done

  # If state file was restored, allow tailscaled to resume the existing session
  if [ -s "$STATE_FILE" ]; then
    echo "State file present. Waiting for existing session to reconnect..."
    RECONNECTED=0
    for i in $(seq 1 15); do
      STATUS_NOW=$(/usr/bin/tailscale status 2>&1 || true)
      if echo "$STATUS_NOW" | grep -qv "Logged out" && echo "$STATUS_NOW" | grep -qv "failed to connect"; then
        echo "Persistent session successfully re-established as existing node."
        RECONNECTED=1
        break
      fi
      sleep 1
    done

    # Only if reconnect failed after 15s do we try tailscale up without --reset
    if [ "$RECONNECTED" -eq 0 ]; then
      echo "Session did not resume automatically; running tailscale up to refresh..."
      if [ -n "$TAILSCALE_AUTHKEY" ]; then
        /usr/bin/tailscale up \
          --authkey="${TAILSCALE_AUTHKEY}" \
          --hostname="${TAILSCALE_HOSTNAME:-render-weighbridge}" \
          --accept-routes || true
      fi
    fi
  else
    # First time boot (no state file yet): authenticate and initialize state
    echo "No state file found. Performing initial authentication..."
    if [ -n "$TAILSCALE_AUTHKEY" ]; then
      /usr/bin/tailscale up \
        --authkey="${TAILSCALE_AUTHKEY}" \
        --hostname="${TAILSCALE_HOSTNAME:-render-weighbridge}" \
        --accept-routes \
        --reset || true
      echo "Initial authentication complete."
    fi
  fi

  # Auto-save state to Render env vars so subsequent boots reuse this exact session
  if [ -f "$STATE_FILE" ] && [ -n "$RENDER_API_KEY" ] && [ -n "$RENDER_SERVICE_ID" ]; then
    CURRENT_B64=$(base64 -w 0 "$STATE_FILE" 2>/dev/null || true)
    if [ -n "$CURRENT_B64" ] && [ "$CURRENT_B64" != "$TS_STATE_B64" ]; then
      echo "Syncing updated Tailscale state to Render environment..."
      python3 -c "
import os, urllib.request, json
key = os.getenv('RENDER_API_KEY')
srv = os.getenv('RENDER_SERVICE_ID')
b64 = '''$CURRENT_B64'''
if key and srv and b64:
    url = f'https://api.render.com/v1/services/{srv}/env-vars/TS_STATE_B64'
    req = urllib.request.Request(
        url,
        data=json.dumps({'value': b64}).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        method='PUT'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print('Render env TS_STATE_B64 updated:', resp.status)
    except Exception as e:
        print('Render env sync error:', e)
" 2>/dev/null || true
    fi
  fi
) &

# Export proxy environment variables so Python httpx/requests automatically routes to Tailscale IPs
export HTTP_PROXY="http://localhost:1055"
export HTTPS_PROXY="http://localhost:1055"
export ALL_PROXY="socks5://localhost:1055"
export NO_PROXY="localhost,127.0.0.1"

# Set default Edge URL to Home PC Tailscale IP if not overridden
export EDGE_URL="${EDGE_URL:-http://100.101.78.57:8000}"

echo "=== Launching Uvicorn on Port ${PORT:-10000} ==="
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
