#!/usr/bin/env bash
#
# One-time provisioning for a fresh AWS Lightsail Ubuntu 22.04/24.04 instance.
# Installs Docker Engine + the compose plugin. Run once as a sudo-capable user:
#
#   bash deploy/lightsail/provision.sh
#
set -euo pipefail

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker and the compose plugin are already installed."
  exit 0
fi

echo "==> Installing Docker Engine + compose plugin"
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# shellcheck disable=SC1091
. /etc/os-release
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Let the current user run docker without sudo (re-login required to take effect).
sudo usermod -aG docker "$USER" || true

echo
echo "==> Done. Log out and back in (or run 'newgrp docker') so group membership applies."
echo "    Next: copy backend/.env.prod.example to backend/.env.prod, fill in secrets, then run:"
echo "    bash deploy/lightsail/deploy.sh"
