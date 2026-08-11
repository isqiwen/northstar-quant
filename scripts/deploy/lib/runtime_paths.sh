#!/usr/bin/env bash

# 运行时可写目录仅接受常见的数据卷和服务目录。这样既允许把大文件放到独立磁盘，
# 又避免部署脚本意外修改系统目录的权限或所有者。
deploy_normalize_runtime_dir() {
  local name="$1"
  local requested_path="$2"
  local normalized_path

  if [ -z "${requested_path}" ]; then
    deploy_fail "${name} 不能为空。"
  fi
  case "${requested_path}" in
    /*)
      ;;
    *)
      deploy_fail "${name} 必须是 Linux 绝对路径。"
      ;;
  esac
  case "/${requested_path}/" in
    *"/../"*|*"/./"*)
      deploy_fail "${name} 不能包含 . 或 .. 路径段。"
      ;;
  esac

  normalized_path="$(realpath -m -- "${requested_path}")"
  case "${normalized_path}" in
    /srv/*|/var/lib/*|/var/cache/*|/var/log/*|/mnt/*|/data/*)
      ;;
    *)
      deploy_fail "${name} 必须位于 /srv、/var/lib、/var/cache、/var/log、/mnt 或 /data 下。"
      ;;
  esac
  case "${normalized_path}" in
    *[!A-Za-z0-9/._-]*)
      deploy_fail "${name} 只能包含字母、数字、点、下划线、连字符和斜杠。"
      ;;
  esac

  printf -v "${name}" "%s" "${normalized_path}"
}

deploy_assert_runtime_dir_is_not_release_path() {
  local name="$1"
  local runtime_dir="$2"
  local app_root="$3"

  case "${runtime_dir}" in
    "${app_root}"|"${app_root}/releases"|"${app_root}/releases/"*|\
      "${app_root}/current"|"${app_root}/current/"*)
      deploy_fail "${name} 不能位于应用根目录、current 或 releases 下。"
      ;;
  esac
}

# 将部署配置转换为唯一、已校验的运行时目录。SHARED_DIR 仍只保存 .env、版本共享
# Python 和 uv 缓存；业务数据可以独立挂载到其他磁盘。
deploy_configure_runtime_paths() {
  local app_root="$1"

  SHARED_DIR="${app_root}/shared"

  RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR:-${SHARED_DIR}/storage}"
  deploy_normalize_runtime_dir "RUNTIME_STORAGE_DIR" "${RUNTIME_STORAGE_DIR}"

  RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR:-${RUNTIME_STORAGE_DIR}/downloads}"
  RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR:-${SHARED_DIR}/reports}"
  RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR:-${SHARED_DIR}/logs}"
  RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR:-${SHARED_DIR}/cache}"
  RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR:-${SHARED_DIR}/matplotlib}"

  deploy_normalize_runtime_dir "RUNTIME_DOWNLOADS_DIR" "${RUNTIME_DOWNLOADS_DIR}"
  deploy_normalize_runtime_dir "RUNTIME_REPORTS_DIR" "${RUNTIME_REPORTS_DIR}"
  deploy_normalize_runtime_dir "RUNTIME_LOG_DIR" "${RUNTIME_LOG_DIR}"
  deploy_normalize_runtime_dir "RUNTIME_CACHE_DIR" "${RUNTIME_CACHE_DIR}"
  deploy_normalize_runtime_dir "RUNTIME_MATPLOTLIB_DIR" "${RUNTIME_MATPLOTLIB_DIR}"

  for runtime_path_name in \
    RUNTIME_STORAGE_DIR \
    RUNTIME_DOWNLOADS_DIR \
    RUNTIME_REPORTS_DIR \
    RUNTIME_LOG_DIR \
    RUNTIME_CACHE_DIR \
    RUNTIME_MATPLOTLIB_DIR; do
    deploy_assert_runtime_dir_is_not_release_path \
      "${runtime_path_name}" \
      "${!runtime_path_name}" \
      "${app_root}"
  done
}

# 运行时路径只在部署端的 deploy.env 中维护。部署时将已经过校验的业务
# 输出路径写入待发布版本的配置文件，应用进程不再依赖 systemd 或发布前命令注入同名
# NORTHSTAR_*_DIR 环境变量。调用方必须先执行 deploy_configure_runtime_paths。
deploy_render_runtime_config() {
  cat <<EOF
# 此文件由 scripts/deploy 自动生成，请勿手工编辑。
# 修改运行时输出目录请编辑部署机对应的 deploy.env 后重新发布。
runtime:
  storage_dir: "${RUNTIME_STORAGE_DIR}"
  downloads_dir: "${RUNTIME_DOWNLOADS_DIR}"
  reports_dir: "${RUNTIME_REPORTS_DIR}"
  log_dir: "${RUNTIME_LOG_DIR}"
EOF
}

# 原子写入待发布版本的 app.local.yaml。配置内容不包含密钥，但仍按服务用户私有文件
# 写入，避免发布中途让健康检查读取到半截 YAML。发布失败时随 stage 一并清理，旧版本
# 的配置不会变化。
deploy_write_runtime_config() {
  local config_file="$1"
  local service_user="$2"
  local config_dir
  local source_temp=""
  local target_temp=""

  config_dir="$(dirname -- "${config_file}")"
  source_temp="$(mktemp "${TMPDIR:-/tmp}/northstar-runtime-config.XXXXXX")" || \
    deploy_fail "无法创建运行时配置临时文件。"

  if ! deploy_render_runtime_config > "${source_temp}"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法生成运行时目录配置。"
  fi

  if ! deploy_as_root install -d -o "${service_user}" -g "${service_user}" -m 0750 "${config_dir}"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法创建运行时配置目录：${config_dir}"
  fi

  if ! target_temp="$(deploy_as_root mktemp "${config_dir}/.app.local.yaml.XXXXXX")"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法创建运行时配置目标临时文件。"
  fi

  if ! deploy_as_root install -m 0600 -o "${service_user}" -g "${service_user}" \
    "${source_temp}" "${target_temp}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    rm -f -- "${source_temp}"
    deploy_fail "无法写入运行时配置临时文件。"
  fi

  if ! deploy_as_root mv -Tf "${target_temp}" "${config_file}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    rm -f -- "${source_temp}"
    deploy_fail "无法原子更新运行时配置：${config_file}"
  fi

  rm -f -- "${source_temp}"
}
