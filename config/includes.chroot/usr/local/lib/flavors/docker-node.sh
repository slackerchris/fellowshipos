#!/bin/bash
# Flavor: docker-node
# Adds Portainer agent and configures docker group for current user
set -e

echo "[flavor] docker-node"

# Add current user to docker group
if [ -n "$SUDO_USER" ]; then
    usermod -aG docker "$SUDO_USER"
    echo "Added $SUDO_USER to docker group. Log out and back in for it to take effect."
fi

# Portainer agent
docker run -d \
    --name portainer_agent \
    --restart always \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -p 9001:9001 \
    portainer/agent:latest

echo "[flavor] docker-node complete"
