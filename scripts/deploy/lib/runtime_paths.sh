#!/usr/bin/env bash

# 服务只能写入受 root 控制父目录下的直属叶子目录。不接受嵌套的「可写目录之下的可写目录」：否则无法对 systemd
# ReadWritePaths 和部署侧的权限复核作出稳定判断。外部数据盘只能使用专属根下的一级叶子。
deploy_runtime_canonical_parent_for_leaf() {
  local runtime_dir="$1"
  local parent_dir

  parent_dir="$(dirname -- "${runtime_dir}")"
  case "${parent_dir}" in
    /var/lib/northstar|/var/cache/northstar|/var/log/northstar|\
      /mnt/northstar-quant|/data/northstar-quant)
      printf '%s\n' "${parent_dir}"
      ;;
    *)
      return 1
      ;;
  esac
}

deploy_assert_runtime_path_components_are_not_symlinks() {
  local path="$1"
  local path_without_root
  local component
  local current_path=""
  local -a path_components=()

  case "${path}" in
    /*)
      ;;
    *)
      printf 'runtime path must be absolute: %s\n' "${path}" >&2
      return 1
      ;;
  esac

  path_without_root="${path#/}"
  local IFS="/"
  read -r -a path_components <<< "${path_without_root}"
  for component in "${path_components[@]}"; do
    [ -n "${component}" ] || continue
    current_path="${current_path}/${component}"
    if deploy_as_root test -L "${current_path}"; then
      printf 'runtime path contains a symbolic-link component: %s\n' "${current_path}" >&2
      return 1
    fi
  done
  return 0
}

deploy_assert_runtime_parent_ancestor_directory() {
  local parent_dir="$1"
  local ancestor_dir
  local ancestor_metadata
  local ancestor_mode
  local ancestor_group_mode
  local ancestor_other_mode

  ancestor_dir="$(dirname -- "${parent_dir}")"
  if ! deploy_assert_runtime_path_components_are_not_symlinks "${ancestor_dir}" ||
    ! deploy_as_root test -d "${ancestor_dir}" ||
    deploy_as_root test -L "${ancestor_dir}" ||
    ! ancestor_metadata="$(deploy_as_root stat -c '%u:%a' -- "${ancestor_dir}")"; then
    return 1
  fi
  if [ "${ancestor_metadata%%:*}" != "0" ]; then
    printf 'runtime parent ancestor must be root-owned: %s\n' "${ancestor_dir}" >&2
    return 1
  fi
  ancestor_mode="${ancestor_metadata#*:}"
  ancestor_group_mode="${ancestor_mode: -2:1}"
  ancestor_other_mode="${ancestor_mode: -1}"
  case "${ancestor_group_mode}:${ancestor_other_mode}" in
    2:*|3:*|6:*|7:*|*:2|*:3|*:6|*:7)
      printf 'runtime parent ancestor must not be group/other writable: %s\n' \
        "${ancestor_dir}" >&2
      return 1
      ;;
  esac
  return 0
}

deploy_assert_runtime_parent_directory() {
  local parent_dir="$1"
  local service_user="$2"
  local service_group_id
  local parent_metadata

  case "${parent_dir}" in
    /var/lib/northstar|/var/cache/northstar|/var/log/northstar|\
      /mnt/northstar-quant|/data/northstar-quant)
      ;;
    *)
      printf 'runtime parent is not a canonical Northstar parent: %s\n' "${parent_dir}" >&2
      return 1
      ;;
  esac

  if ! deploy_assert_runtime_path_components_are_not_symlinks "${parent_dir}" ||
    ! deploy_as_root test -d "${parent_dir}" ||
    deploy_as_root test -L "${parent_dir}" ||
    ! service_group_id="$(id -g "${service_user}")" ||
    ! parent_metadata="$(deploy_as_root stat -c '%u:%g:%a' -- "${parent_dir}")"; then
    return 1
  fi
  if [ "${parent_metadata}" != "0:${service_group_id}:750" ]; then
    printf 'runtime parent must be root:%s 0750: %s\n' \
      "${service_user}" "${parent_dir}" >&2
    return 1
  fi

  deploy_assert_runtime_parent_ancestor_directory "${parent_dir}"
}

deploy_prepare_runtime_parent_directory() {
  local parent_dir="$1"
  local service_user="$2"

  case "${parent_dir}" in
    /var/lib/northstar|/var/cache/northstar|/var/log/northstar|\
      /mnt/northstar-quant|/data/northstar-quant)
      ;;
    *)
      printf 'runtime parent is not a canonical Northstar parent: %s\n' "${parent_dir}" >&2
      return 1
      ;;
  esac

  if deploy_as_root test -e "${parent_dir}" || deploy_as_root test -L "${parent_dir}"; then
    deploy_assert_runtime_parent_directory "${parent_dir}" "${service_user}"
    return
  fi

  # mkdir without -p is deliberate: it atomically creates only the final
  # canonical parent and never follows an existing final symlink.  A racing
  # creator makes mkdir fail; we fail closed instead of changing its object.
  if ! deploy_assert_runtime_parent_ancestor_directory "${parent_dir}" ||
    ! deploy_as_root mkdir -m 0750 -- "${parent_dir}"; then
    printf 'unable to create canonical runtime parent safely: %s\n' "${parent_dir}" >&2
    return 1
  fi
  if ! deploy_as_root chown "root:${service_user}" -- "${parent_dir}" ||
    ! deploy_as_root chmod 0750 -- "${parent_dir}"; then
    printf 'unable to secure newly created runtime parent: %s\n' "${parent_dir}" >&2
    return 1
  fi
  deploy_assert_runtime_parent_directory "${parent_dir}" "${service_user}"
}

deploy_assert_runtime_leaf_directory() {
  local runtime_dir="$1"
  local service_user="$2"
  local normalized_dir
  local parent_dir
  local service_user_id
  local service_group_id
  local leaf_metadata

  normalized_dir="$(realpath -m -- "${runtime_dir}")" || return 1
  if [ "${runtime_dir}" != "${normalized_dir}" ] ||
    ! parent_dir="$(deploy_runtime_canonical_parent_for_leaf "${runtime_dir}")" ||
    ! deploy_assert_runtime_path_components_are_not_symlinks "${runtime_dir}" ||
    ! deploy_assert_runtime_parent_directory "${parent_dir}" "${service_user}" ||
    ! deploy_as_root test -d "${runtime_dir}" ||
    deploy_as_root test -L "${runtime_dir}" ||
    ! service_user_id="$(id -u "${service_user}")" ||
    ! service_group_id="$(id -g "${service_user}")" ||
    ! leaf_metadata="$(deploy_as_root stat -c '%u:%g:%a' -- "${runtime_dir}")"; then
    return 1
  fi
  if [ "${leaf_metadata}" != "${service_user_id}:${service_group_id}:750" ]; then
    printf 'runtime leaf must be %s:%s 0750: %s\n' \
      "${service_user}" "${service_user}" "${runtime_dir}" >&2
    return 1
  fi
}

deploy_prepare_runtime_leaf_directory() {
  local runtime_dir="$1"
  local service_user="$2"
  local parent_dir

  if ! parent_dir="$(deploy_runtime_canonical_parent_for_leaf "${runtime_dir}")" ||
    [ "${runtime_dir}" != "$(realpath -m -- "${runtime_dir}")" ]; then
    printf 'runtime leaf must be a direct canonical child: %s\n' "${runtime_dir}" >&2
    return 1
  fi
  if ! deploy_prepare_runtime_parent_directory "${parent_dir}" "${service_user}"; then
    return 1
  fi

  if deploy_as_root test -e "${runtime_dir}" || deploy_as_root test -L "${runtime_dir}"; then
    # Existing service-writable leaves are never chmod/chown repaired.  Their
    # ownership, mode, type and non-symlink status are a fail-closed contract.
    deploy_assert_runtime_leaf_directory "${runtime_dir}" "${service_user}"
    return
  fi

  if ! deploy_as_root mkdir -m 0750 -- "${runtime_dir}"; then
    printf 'unable to create runtime leaf safely: %s\n' "${runtime_dir}" >&2
    return 1
  fi
  if ! deploy_as_root chown "${service_user}:${service_user}" -- "${runtime_dir}" ||
    ! deploy_as_root chmod 0750 -- "${runtime_dir}"; then
    printf 'unable to secure newly created runtime leaf: %s\n' "${runtime_dir}" >&2
    return 1
  fi
  deploy_assert_runtime_leaf_directory "${runtime_dir}" "${service_user}"
}

# 运行时可写目录只能是受控父目录的直属叶子。这既允许把大文件放到独立磁盘，
# 又避免部署脚本把任意嵌套数据目录变成系统服务可写区域。
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
  if [ "${requested_path}" != "${normalized_path}" ]; then
    deploy_fail "${name} 必须是规范路径，且不得经过符号链接、重复斜杠或其他可规范化路径段。"
  fi
  case "${normalized_path}" in
    *[!A-Za-z0-9/._-]*)
      deploy_fail "${name} 只能包含字母、数字、点、下划线、连字符和斜杠。"
      ;;
  esac
  if ! deploy_runtime_canonical_parent_for_leaf "${normalized_path}" >/dev/null; then
    deploy_fail "${name} 必须是 /var/lib/northstar、/var/cache/northstar、/var/log/northstar、/mnt/northstar-quant 或 /data/northstar-quant 下的直属叶子目录。"
  fi

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

deploy_assert_runtime_paths_do_not_overlap() {
  local first_name
  local second_name
  local first_path
  local second_path

  for first_name in "$@"; do
    first_path="${!first_name}"
    for second_name in "$@"; do
      if [ "${first_name}" = "${second_name}" ]; then
        continue
      fi
      second_path="${!second_name}"
      case "${first_path}" in
        "${second_path}"|"${second_path}/"*)
          deploy_fail "${first_name} 不得与 ${second_name} 重叠或嵌套。"
          ;;
      esac
    done
  done
}

deploy_assert_runtime_paths_do_not_overlap_reserved_leaves() {
  local cache_dir="$1"
  shift
  local runtime_path_name
  local runtime_path
  local reserved_leaf

  # These are independently managed direct leaves: Dashboard HOME is a
  # systemd ReadWritePaths target, venv-build is deployment-user scratch, and
  # uv-cache is a service cache target.  A configurable business path must
  # never claim any of them.
  for runtime_path_name in "$@"; do
    runtime_path="${!runtime_path_name}"
    for reserved_leaf in \
      "${cache_dir}/dashboard" \
      "${cache_dir}/venv-build" \
      "${cache_dir}/uv-cache"; do
      if [ "${runtime_path}" = "${reserved_leaf}" ]; then
        deploy_fail "${runtime_path_name} 不得占用受系统管理的运行时叶子：${reserved_leaf}"
      fi
    done
  done
}

# 将部署配置转换为唯一、已校验的运行时目录。代码 release 永远不可作为
# 运行时写入目标；业务数据可以独立挂载到其他磁盘。
deploy_configure_runtime_paths() {
  local app_root="$1"
  local state_dir="$2"
  local cache_dir="$3"
  local log_dir="$4"

  RUNTIME_STORAGE_DIR="${RUNTIME_STORAGE_DIR:-${state_dir}/storage}"
  RUNTIME_DOWNLOADS_DIR="${RUNTIME_DOWNLOADS_DIR:-${state_dir}/downloads}"
  RUNTIME_REPORTS_DIR="${RUNTIME_REPORTS_DIR:-${state_dir}/reports}"
  RUNTIME_LOG_DIR="${RUNTIME_LOG_DIR:-${log_dir}/app}"
  RUNTIME_CACHE_DIR="${RUNTIME_CACHE_DIR:-${cache_dir}/runtime}"
  RUNTIME_MATPLOTLIB_DIR="${RUNTIME_MATPLOTLIB_DIR:-${cache_dir}/matplotlib}"

  deploy_normalize_runtime_dir "RUNTIME_STORAGE_DIR" "${RUNTIME_STORAGE_DIR}"
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

  deploy_assert_runtime_paths_do_not_overlap \
    RUNTIME_STORAGE_DIR \
    RUNTIME_DOWNLOADS_DIR \
    RUNTIME_REPORTS_DIR \
    RUNTIME_LOG_DIR \
    RUNTIME_CACHE_DIR \
    RUNTIME_MATPLOTLIB_DIR

  deploy_assert_runtime_paths_do_not_overlap_reserved_leaves \
    "${cache_dir}" \
    RUNTIME_STORAGE_DIR \
    RUNTIME_DOWNLOADS_DIR \
    RUNTIME_REPORTS_DIR \
    RUNTIME_LOG_DIR \
    RUNTIME_CACHE_DIR \
    RUNTIME_MATPLOTLIB_DIR
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
  local deploy_state_metadata

  # This helper is a release-installer operation.  Rendering into an inherited
  # TMPDIR would let a deployment-user-controlled directory race a root
  # redirection, so the staging file must live only in the verified root
  # deployment-state directory.
  if [ "${EUID}" -ne 0 ] || [ -z "${DEPLOY_STATE_DIR:-}" ] ||
    ! deploy_state_metadata="$(deploy_as_root stat -c '%u:%g:%a' -- "${DEPLOY_STATE_DIR}")" ||
    [ "${deploy_state_metadata}" != "0:0:700" ] ||
    deploy_as_root test -L "${DEPLOY_STATE_DIR}"; then
    deploy_fail "活动应用配置只能在受管的 root deploy-state 目录中渲染。"
  fi
  config_dir="$(dirname -- "${config_file}")"
  source_temp="$(deploy_as_root mktemp "${DEPLOY_STATE_DIR}/.northstar-app-config.XXXXXX")" || \
    deploy_fail "无法创建活动应用配置临时文件。"

  if ! deploy_render_active_app_config "${template_file}" > "${source_temp}"; then
    deploy_as_root rm -f -- "${source_temp}" || true
    deploy_fail "无法从完整模板生成活动应用配置。"
  fi

  if ! deploy_as_root install -d -o root -g "${service_user}" -m 0750 "${config_dir}"; then
    deploy_as_root rm -f -- "${source_temp}" || true
    deploy_fail "无法创建活动应用配置目录：${config_dir}"
  fi

  if ! target_temp="$(deploy_as_root mktemp "${config_dir}/.app.yaml.XXXXXX")"; then
    deploy_as_root rm -f -- "${source_temp}" || true
    deploy_fail "无法创建活动应用配置目标临时文件。"
  fi

  if ! deploy_as_root install -m 0640 -o root -g "${service_user}" \
    "${source_temp}" "${target_temp}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    deploy_as_root rm -f -- "${source_temp}" || true
    deploy_fail "无法写入活动应用配置临时文件。"
  fi

  if ! deploy_as_root mv -Tf "${target_temp}" "${config_file}"; then
    deploy_as_root rm -f -- "${target_temp}" || true
    deploy_as_root rm -f -- "${source_temp}" || true
    deploy_fail "无法原子更新活动应用配置：${config_file}"
  fi

  deploy_as_root rm -f -- "${source_temp}"
}
