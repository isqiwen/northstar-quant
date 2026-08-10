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
