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
echo "  1. Generate a long-lived OAuth token (works with Max subscription)."
echo "       sudo -iu edx"
echo "       claude setup-token"
echo "       # → browser opens, sign in with Claude.ai account,"
echo "       # → terminal prints a sk-claude-... token (valid ~1 year)"
echo "       exit"
echo "  2. Paste the token into /opt/edx/.env.evolve as"
echo "     CLAUDE_CODE_OAUTH_TOKEN=…  (NO Anthropic API key needed for evolve)."
echo "       sudo cp deploy/env.evolve.example /opt/edx/.env.evolve"
echo "       sudo chown edx:edx /opt/edx/.env.evolve"
echo "       sudo chmod 600 /opt/edx/.env.evolve"
echo "       sudo \$EDITOR /opt/edx/.env.evolve   # paste the token"
echo "  3. Capture canary baseline:"
echo "       sudo -iu edx /opt/edx/.venv/bin/edx evolve canary capture"
echo "  4. Install systemd units:"
echo "       sudo cp deploy/systemd/edx-evolve.{service,timer} /etc/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now edx-evolve.timer"
echo "  5. Wait 24h on DRY-RUN (EDX_EVOLVE_AGENT_ENABLED=0)."
echo "     Inspect evolution/runs/<N>/ — when satisfied, flip to =1 and"
echo "     restart the timer."
