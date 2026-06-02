#!/usr/bin/env sh
# Entrypoint for the agent-b Railway service.
#
# If TS_AUTHKEY is set, brings up Tailscale in userspace-networking mode with a
# SOCKS5 proxy on localhost:${SOCKS_PORT}. opera_client routes ONLY its OPERA
# API calls through that proxy (set OPERA_API_PROXY=socks5h://localhost:PORT),
# reaching the opera-pms-api service at OPERA_API_BASE_URL over the tailnet. All
# other upstreams (Salesforce/NetSuite/Pardot) connect directly, unproxied.
#
# Using socks5h:// means hostname resolution happens at the tailscaled end, so
# the service's MagicDNS name (e.g. tvrspms.<tailnet>.ts.net) resolves over the
# tailnet even with --accept-dns=false.
#
# If TS_AUTHKEY is not set (local dev with a direct route to the API, or no OPERA
# integration), skips Tailscale entirely.

set -e

SOCKS_PORT="${TS_SOCKS_PORT:-1055}"

if [ -n "${TS_AUTHKEY}" ]; then
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

    echo "[start.sh] Tailnet up. opera_client reaches OPERA_API_BASE_URL via SOCKS5"
    echo "           (ensure OPERA_API_PROXY=socks5h://localhost:${SOCKS_PORT})."
else
    echo "[start.sh] TS_AUTHKEY not set — skipping Tailscale. OPERA tools expect a"
    echo "           direct route to OPERA_API_BASE_URL (and no OPERA_API_PROXY)."
fi

# Persist the tool-usage analytics JSONL to the attached Railway volume so it
# survives redeploys (the default .cache path is ephemeral on Railway). Railway
# injects RAILWAY_VOLUME_MOUNT_PATH for the mounted volume; an explicit
# TOOL_USAGE_LOG always wins. usage_report.py / request_analyzer.py read this file.
if [ -z "${TOOL_USAGE_LOG}" ] && [ -n "${RAILWAY_VOLUME_MOUNT_PATH}" ]; then
    export TOOL_USAGE_LOG="${RAILWAY_VOLUME_MOUNT_PATH}/tool_usage.jsonl"
fi
if [ -n "${TOOL_USAGE_LOG}" ]; then
    mkdir -p "$(dirname "${TOOL_USAGE_LOG}")"
    echo "[start.sh] Tool-usage analytics -> ${TOOL_USAGE_LOG}"
else
    echo "[start.sh] TOOL_USAGE_LOG unset and no volume mounted — usage JSONL is"
    echo "           ephemeral (.cache). Attach a Railway volume to persist it."
fi

echo "[start.sh] Launching MCP server"
exec python -m src
