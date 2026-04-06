#!/bin/bash
# Flavor: docker-node
# Adds Portainer agent and configures docker group for current user
set -e

echo "[flavor] docker-node"

# Add current user to docker group
if [ -n "${SUDO_USER:-}" ] && id "$SUDO_USER" >/dev/null 2>&1; then
    usermod -aG docker "$SUDO_USER"
    echo "Added $SUDO_USER to docker group. Log out and back in for it to take effect."
fi

# Portainer agent
if docker ps --format '{{.Names}}' | grep -Fxq portainer_agent; then
    echo "portainer_agent is already running."
elif docker ps -a --format '{{.Names}}' | grep -Fxq portainer_agent; then
    docker start portainer_agent >/dev/null
    echo "Started existing portainer_agent container."
else
    docker run -d \
        --name portainer_agent \
        --restart always \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /var/lib/docker/volumes:/var/lib/docker/volumes \
        -p 9001:9001 \
        portainer/agent:latest
fi

echo "[flavor] docker-node complete"
