FROM python:3.11-slim

# Tailscale (userspace mode) provides the SOCKS5 proxy that opera_client uses to
# reach the opera-pms-api service over the tailnet. curl/ca-certificates are
# needed for the Tailscale install script and TLS to its control plane.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates iptables iproute2 \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8000
CMD ["/start.sh"]
