#!/usr/bin/env bash
set -euo pipefail

echo "▶ Building Lambda packages..."
bash scripts/build_lambdas.sh

echo "▶ Deploying infrastructure..."
cd terraform && terraform apply