#!/usr/bin/env bash
set -euo pipefail
cd /opt/wechat-agent
source .venv/bin/activate
wechat-agent run --articles 1 >> /var/log/wechat-agent.log 2>&1
