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

# 运行时路径只在部署端的 deploy.env 中维护。每次发布均以完整、非秘密的
# configs/app.example.yaml 为基底，生成该 release 私有的活动 configs/app.yaml。
# 应用进程不依赖 systemd 或发布前命令注入同名 NORTHSTAR_*_DIR 环境变量；调用方必须先
# 执行 deploy_configure_runtime_paths。
deploy_render_active_app_config() {
  local template_file="$1"

  if [ ! -f "${template_file}" ]; then
    printf "完整应用配置模板不存在：%s\n" "${template_file}" >&2
    return 1
  fi

  # 路径值已由 deploy_configure_runtime_paths 规范化为安全字符集，因此可安全作为 awk
  # 变量写入双引号 YAML 标量。仅替换 runtime 的四个直属字段，其余内容按模板原样保留。
  awk \
    -v storage_dir="${RUNTIME_STORAGE_DIR}" \
    -v downloads_dir="${RUNTIME_DOWNLOADS_DIR}" \
    -v reports_dir="${RUNTIME_REPORTS_DIR}" \
    -v log_dir="${RUNTIME_LOG_DIR}" '
      /^[[:space:]]*runtime:[[:space:]]*(#.*)?$/ {
        runtime_count += 1
        in_runtime = 1
        print
        next
      }
      in_runtime && /^[^[:space:]#]/ {
        in_runtime = 0
      }
      in_runtime && /^[[:space:]]+storage_dir:[[:space:]]*/ {
        storage_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "storage_dir: \"" storage_dir "\""
        next
      }
      in_runtime && /^[[:space:]]+downloads_dir:[[:space:]]*/ {
        downloads_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "downloads_dir: \"" downloads_dir "\""
        next
      }
      in_runtime && /^[[:space:]]+reports_dir:[[:space:]]*/ {
        reports_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "reports_dir: \"" reports_dir "\""
        next
      }
      in_runtime && /^[[:space:]]+log_dir:[[:space:]]*/ {
        log_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "log_dir: \"" log_dir "\""
        next
      }
      { print }
      END {
        if (runtime_count != 1 || storage_count != 1 || downloads_count != 1 || \
            reports_count != 1 || log_count != 1) {
          print "完整应用配置模板的 runtime 段必须恰好包含 storage_dir、downloads_dir、reports_dir 和 log_dir。" > "/dev/stderr"
          exit 1
        }
      }
    ' "${template_file}"
}

# 原子写入待发布版本的完整活动 app.yaml。配置内容不包含密钥，但仍按服务用户私有文件
# 写入，避免发布中途让迁移或健康检查读取到半截 YAML。发布失败时它会随 stage 一并清理，
# 已发布版本的配置不会变化。
deploy_write_active_app_config() {
  local template_file="$1"
  local config_file="$2"
  local service_user="$3"
  local config_dir
  local source_temp=""
  local target_temp=""

  config_dir="$(dirname -- "${config_file}")"
  source_temp="$(mktemp "${TMPDIR:-/tmp}/northstar-app-config.XXXXXX")" || \
    deploy_fail "无法创建活动应用配置临时文件。"

  if ! deploy_render_active_app_config "${template_file}" > "${source_temp}"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法从完整模板生成活动应用配置。"
  fi

  if ! deploy_as_root install -d -o "${service_user}" -g "${service_user}" -m 0750 "${config_dir}"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法创建活动应用配置目录：${config_dir}"
  fi

  if ! target_temp="$(deploy_as_root mktemp "${config_dir}/.app.yaml.XXXXXX")"; then
    rm -f -- "${source_temp}"
    deploy_fail "无法创建活动应用配置目标临时文件。"
  fi

  if ! deploy_as_root install -m 0600 -o "${service_user}" -g "${service_user}" \
    "${source_temp}" "${target_temp}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    rm -f -- "${source_temp}"
    deploy_fail "无法写入活动应用配置临时文件。"
  fi

  if ! deploy_as_root mv -Tf "${target_temp}" "${config_file}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    rm -f -- "${source_temp}"
    deploy_fail "无法原子更新活动应用配置：${config_file}"
  fi

  rm -f -- "${source_temp}"
}
