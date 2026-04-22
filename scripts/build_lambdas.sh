#!/usr/bin/env bash
set -euo pipefail

build_lambda() {
  local name=$1
  local dir="lambdas/${name}"

  echo "▶  Building ${name}..."

  rm -rf "${dir}/dist/package"
  mkdir -p "${dir}/dist/package"

  # Pin exact versions from lockfile
  (cd "${dir}" && uv export --frozen --no-dev --no-hashes -o dist/requirements.txt)

  # uv pip is uv's built-in pip — no pip installation needed in the venv.
  # --python-platform linux  → download Linux wheels even on Windows
  # --only-binary :all:      → never compile from source
  uv pip install \
    -r "${dir}/dist/requirements.txt" \
    --target "${dir}/dist/package" \
    --python-platform linux \
    --only-binary :all: \
    --quiet

  cp "${dir}/handler.py" "${dir}/dist/package/handler.py"

  local size
  size=$(du -sh "${dir}/dist/package" 2>/dev/null | cut -f1)
  echo "   ✓ ${name} built (${size})"
}

build_lambda ingestion
build_lambda transform
build_lambda dashboard

echo ""
echo "✓ All Lambdas built — run: cd terraform && terraform apply"