#!/usr/bin/env sh
# Entrypoint for the agent-b Railway service.
#
# If TS_AUTHKEY is set, brings up Tailscale in userspace-networking mode and
# forwards local TCP 1521 through Tailscale's SOCKS5 proxy to the on-prem OPERA
# Oracle box (OPERA_TS_HOST). The MCP server then connects to 127.0.0.1:1521.
#
# If TS_AUTHKEY is not set (e.g. local dev with a direct route to OPERA, or no
# OPERA integration at all), skips Tailscale entirely and runs the server with
# whatever OPERA_DB_HOST the operator provided.

set -e

SOCKS_PORT="${TS_SOCKS_PORT:-1055}"
OPERA_LOCAL_PORT="${OPERA_LOCAL_PORT:-1521}"
OPERA_REMOTE_PORT="${OPERA_REMOTE_PORT:-1521}"

if [ -n "${TS_AUTHKEY}" ]; then
    if [ -z "${OPERA_TS_HOST}" ]; then
        echo "ERROR: TS_AUTHKEY is set but OPERA_TS_HOST is missing." >&2
        echo "       Set OPERA_TS_HOST to the OPERA box's tailnet IP or MagicDNS name." >&2
        exit 1
    fi

    echo "[start.sh] Starting tailscaled (userspace-networking, SOCKS5 on :${SOCKS_PORT})"
    /usr/sbin/tailscaled \
        --tun=userspace-networking \
        --socks5-server="localhost:${SOCKS_PORT}" \
        --state=mem: \
        > /var/log/tailscaled.log 2>&1 &

    # Wait for the daemon socket to be ready
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if tailscale --socket=/var/run/tailscale/tailscaled.sock status >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    echo "[start.sh] Bringing tailnet up"
    tailscale up \
        --authkey="${TS_AUTHKEY}" \
        --hostname="agent-b-${RAILWAY_REPLICA_ID:-$(hostname)}" \
        --accept-dns=false

    echo "[start.sh] Forwarding 127.0.0.1:${OPERA_LOCAL_PORT} -> ${OPERA_TS_HOST}:${OPERA_REMOTE_PORT} via SOCKS5"
    socat \
        "TCP-LISTEN:${OPERA_LOCAL_PORT},fork,reuseaddr,bind=127.0.0.1" \
        "SOCKS5:localhost:${OPERA_TS_HOST}:${OPERA_REMOTE_PORT},socksport=${SOCKS_PORT}" \
        > /var/log/socat.log 2>&1 &

    # Override OPERA_DB_HOST/PORT so opera_client connects through the local forwarder.
    # The operator should leave these unset in Railway when TS_AUTHKEY is in use.
    export OPERA_DB_HOST="127.0.0.1"
    export OPERA_DB_PORT="${OPERA_LOCAL_PORT}"

    # Give socat a moment to bind before the MCP server's first OPERA query.
    sleep 1
else
    echo "[start.sh] TS_AUTHKEY not set — skipping Tailscale. OPERA tools will use OPERA_DB_HOST as-is."
fi

echo "[start.sh] Launching MCP server"
exec python -m src
