#!/bin/bash
set -e

echo "=== Starting Tailscale on Render ==="

# Start tailscaled in userspace networking mode
/usr/sbin/tailscaled \
  --state=/var/lib/tailscale/tailscaled.state \
  --socket=/var/run/tailscale/tailscaled.sock \
  --tun=userspace-networking \
  --outbound-http-proxy-listen=localhost:1055 \
  --socks5-server=localhost:1055 &

# Wait for tailscaled socket to appear
until /usr/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock status >/dev/null 2>&1 || [ "$RETRIES" -gt 20 ]; do
  sleep 0.5
  RETRIES=$((RETRIES+1))
done

# Connect to Tailscale if AUTHKEY provided
if [ -n "$TAILSCALE_AUTHKEY" ]; then
  echo "Authenticating Tailscale..."
  /usr/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock up \
    --authkey="${TAILSCALE_AUTHKEY}" \
    --hostname="${TAILSCALE_HOSTNAME:-render-weighbridge}" \
    --accept-routes
  echo "Tailscale connected successfully!"
  /usr/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock status
else
  echo "WARNING: TAILSCALE_AUTHKEY not set. Tailscale will not connect."
fi

# Export proxy environment variables so Python httpx/requests automatically routes to Tailscale IPs
export HTTP_PROXY="http://localhost:1055"
export HTTPS_PROXY="http://localhost:1055"
export ALL_PROXY="socks5://localhost:1055"
export NO_PROXY="localhost,127.0.0.1"

# Set default Edge URL to Home PC Tailscale IP if not overridden
export EDGE_URL="${EDGE_URL:-http://100.101.78.57:8000}"

echo "=== Starting Weighbridge Cloud App on Port ${PORT:-10000} ==="
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
