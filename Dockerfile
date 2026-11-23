# Use Python slim base image
FROM python:3.11-slim

# Install system dependencies, curl, ca-certificates, and iptables
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    iptables \
    && rm -rf /var/lib/apt/lists/*

# Install Tailscale
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create directory for tailscale state
RUN mkdir -p /var/run/tailscale /var/lib/tailscale /var/log/tailscale

# Ensure start script is executable
RUN chmod +x /app/start.sh

# Expose Render default port
EXPOSE 10000

# Set entrypoint
CMD ["/app/start.sh"]
