#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT_DIR}/scripts/deploy/lib/common.sh"

# macOS 的 bsdtar 默认会生成 AppleDouble 扩展属性文件，部署制品不需要这些元数据。
export COPYFILE_DISABLE=1

ARTIFACT_DIR="${ARTIFACT_DIR:-${ROOT_DIR}/dist}"
REVISION="${REVISION:-$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)}"
STAMP="${STAMP:-$(date -u +%Y%m%d%H%M%S)}"
ARTIFACT_NAME="${ARTIFACT_NAME:-northstar-quant-${REVISION}-${STAMP}.tar.gz}"
ARTIFACT_PATH="${ARTIFACT_PATH:-${ARTIFACT_DIR}/${ARTIFACT_NAME}}"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/northstar-artifact.XXXXXX")"
BUNDLE_DIR="${TEMP_ROOT}/bundle"

cleanup() {
  rm -rf "${TEMP_ROOT}"
}
trap cleanup EXIT

required_paths=(
  pyproject.toml
  uv.lock
  alembic.ini
  alembic
  configs
  src
  templates
)

for required_path in "${required_paths[@]}"; do
  if [ ! -e "${ROOT_DIR}/${required_path}" ]; then
    deploy_fail "构建制品缺少必需路径：${required_path}"
  fi
done

deploy_log "收集运行所需文件"
mkdir -p "${BUNDLE_DIR}"
tar -C "${ROOT_DIR}" \
  --exclude='configs/app.local.yaml' \
  -cf - "${required_paths[@]}" | tar -C "${BUNDLE_DIR}" -xf -

cat > "${BUNDLE_DIR}/DEPLOY_ARTIFACT_META.txt" <<EOF
revision=${REVISION}
built_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

mkdir -p "${ARTIFACT_DIR}"
deploy_log "生成部署制品 ${ARTIFACT_PATH}"
tar -C "${BUNDLE_DIR}" -czf "${ARTIFACT_PATH}" .

printf "%s\n" "${ARTIFACT_PATH}"
