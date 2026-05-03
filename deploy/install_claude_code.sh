#!/usr/bin/env bash
# Patch 44: install Node.js 20+ and the @anthropic-ai/claude-code CLI on a
# Debian/Ubuntu VPS for the self-evolve loop. Idempotent — re-running the
# script is safe.
#
# Run this as root once per host:
#   sudo bash deploy/install_claude_code.sh
#
# The companion files are
#   - deploy/env.evolve.example  → /opt/edx/.env.evolve
#   - deploy/systemd/edx-evolve.service / .timer
# and are installed by the operator separately.

set -euo pipefail

NODE_TARGET_MAJOR=20

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

current_node_major() {
  if ! command -v node >/dev/null; then
    echo 0
    return
  fi
  node -v | sed 's/^v//' | cut -d. -f1
}

if [[ "$(current_node_major)" -lt "${NODE_TARGET_MAJOR}" ]]; then
  echo "Installing Node.js ${NODE_TARGET_MAJOR}.x via NodeSource…"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_TARGET_MAJOR}.x" | bash -
  apt-get install -y nodejs
else
  echo "Node.js $(node -v) already installed."
fi

if ! command -v claude >/dev/null; then
  echo "Installing @anthropic-ai/claude-code globally…"
  npm install -g @anthropic-ai/claude-code
else
  echo "claude $(claude --version 2>/dev/null || echo 'unknown') already installed."
fi

echo
echo "Done."
echo
echo "Next steps:"
echo "  1. As the edx user (sudo -iu edx) run 'claude /login' once interactively"
echo "     to drop a refresh token into ~/.claude. OR set CLAUDE_CODE_OAUTH_TOKEN"
echo "     in /opt/edx/.env.evolve directly."
echo "  2. Copy deploy/env.evolve.example to /opt/edx/.env.evolve and fill in:"
echo "       sudo cp deploy/env.evolve.example /opt/edx/.env.evolve"
echo "       sudo chown edx:edx /opt/edx/.env.evolve"
echo "       sudo chmod 600 /opt/edx/.env.evolve"
echo "  3. Capture canary baseline:"
echo "       sudo -iu edx /opt/edx/.venv/bin/edx evolve canary capture"
echo "  4. Install systemd units:"
echo "       sudo cp deploy/systemd/edx-evolve.{service,timer} /etc/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now edx-evolve.timer"
echo "  5. Wait 24h on dry-run (EDX_EVOLVE_AGENT_ENABLED=0), then flip to 1."
