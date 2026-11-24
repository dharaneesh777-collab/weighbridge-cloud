#!/bin/bash

echo "=== Starting Weighbridge Cloud on Render ==="

# Start tailscaled in userspace networking mode with in-memory state
/usr/sbin/tailscaled \
  --state=mem: \
  --tun=userspace-networking \
  --outbound-http-proxy-listen=localhost:1055 \
  --socks5-server=localhost:1055 >/var/log/tailscaled.log 2>&1 &

# Asynchronously authenticate Tailscale so Uvicorn is never blocked
if [ -n "$TAILSCALE_AUTHKEY" ]; then
  (
    echo "Connecting to Tailscale..."
    for i in $(seq 1 30); do
      if /usr/bin/tailscale status >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
    /usr/bin/tailscale up \
      --authkey="${TAILSCALE_AUTHKEY}" \
      --hostname="${TAILSCALE_HOSTNAME:-render-weighbridge}" \
      --accept-routes || true
    echo "Tailscale up completed."
  ) &
fi

# Export proxy environment variables so Python httpx/requests automatically routes to Tailscale IPs
export HTTP_PROXY="http://localhost:1055"
export HTTPS_PROXY="http://localhost:1055"
export ALL_PROXY="socks5://localhost:1055"
export NO_PROXY="localhost,127.0.0.1"

# Set default Edge URL to Home PC Tailscale IP if not overridden
export EDGE_URL="${EDGE_URL:-http://100.101.78.57:8000}"

echo "=== Launching Uvicorn on Port ${PORT:-10000} ==="
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
