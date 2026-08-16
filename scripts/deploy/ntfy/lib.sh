#!/usr/bin/env bash

# 私有 ntfy 部署参数的共享校验。调用方必须先 source deploy/lib/common.sh，
# 以便统一使用 deploy_fail 和 deploy_assert_bool。

ntfy_normalize_public_host() {
  local variable_name="$1"
  local requested_host="$2"
  local normalized_host

  normalized_host="${requested_host,,}"
  if [ -z "${normalized_host}" ] || [ "${#normalized_host}" -gt 253 ] ||
    [[ ! "${normalized_host}" =~ ^[a-z0-9][a-z0-9.-]*[a-z0-9]$ ]] ||
    [[ "${normalized_host}" != *.* ]] || [[ "${normalized_host}" == *..* ]] ||
    [[ "${normalized_host}" == *.-* ]] || [[ "${normalized_host}" == *-.* ]]; then
    deploy_fail "${variable_name} 必须是用于公开 HTTPS 服务的合法 FQDN，不能包含协议、端口、路径或通配符。"
  fi

  printf -v "${variable_name}" "%s" "${normalized_host}"
}

ntfy_validate_acme_email() {
  local value="$1"

  if [ -z "${value}" ] ||
    [[ ! "${value}" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
    [[ "${value}" == *[\"\'\;\{\}\[\]\\]* ]]; then
    deploy_fail "NTFY_ACME_EMAIL 必须是用于 ACME 证书通知的安全邮箱地址。"
  fi
}

ntfy_validate_image() {
  local variable_name="$1"
  local image="$2"

  case "${variable_name}" in
    NTFY_IMAGE)
      if [[ ! "${image}" =~ ^binwiederhier/ntfy:v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        deploy_fail "NTFY_IMAGE 只能使用 binwiederhier/ntfy 的明确 vX.Y.Z 标签，不能使用 latest。"
      fi
      ;;
    NTFY_CADDY_IMAGE)
      if [[ ! "${image}" =~ ^caddy:[0-9]+\.[0-9]+\.[0-9]+-alpine$ ]]; then
        deploy_fail "NTFY_CADDY_IMAGE 只能使用 caddy 的明确 X.Y.Z-alpine 标签，不能使用 latest。"
      fi
      ;;
    *)
      deploy_fail "未知 ntfy 镜像字段：${variable_name}。"
      ;;
  esac
}

ntfy_normalize_path() {
  local variable_name="$1"
  local requested_path="$2"
  local path_kind="$3"
  local normalized_path

  if [ -z "${requested_path}" ]; then
    deploy_fail "${variable_name} 不能为空。"
  fi
  case "${requested_path}" in
    /*)
      ;;
    *)
      deploy_fail "${variable_name} 必须是 Linux 绝对路径。"
      ;;
  esac
  case "/${requested_path}/" in
    *"/../"*|*"/./"*)
      deploy_fail "${variable_name} 不能包含 . 或 .. 路径段。"
      ;;
  esac

  normalized_path="$(realpath -m -- "${requested_path}")"
  case "${path_kind}" in
    config)
      case "${normalized_path}" in
        /etc/*)
          ;;
        *)
          deploy_fail "${variable_name} 必须位于 /etc 下。"
          ;;
      esac
      ;;
    data)
      case "${normalized_path}" in
        /var/lib/*|/srv/*|/mnt/*|/data/*)
          ;;
        *)
          deploy_fail "${variable_name} 必须位于 /var/lib、/srv、/mnt 或 /data 下。"
          ;;
      esac
      case "/${normalized_path}/" in
        *"/releases/"*|*"/current/"*)
          deploy_fail "${variable_name} 不能位于 releases 或 current 路径段；ntfy 持久化状态不得属于可切换或可回收的应用版本目录。"
          ;;
      esac
      ;;
    *)
      deploy_fail "未知 ntfy 路径类型：${path_kind}。"
      ;;
  esac
  case "${normalized_path}" in
    *[!A-Za-z0-9/._-]*)
      deploy_fail "${variable_name} 只能包含字母、数字、点、下划线、连字符和斜杠。"
      ;;
  esac

  printf -v "${variable_name}" "%s" "${normalized_path}"
}

ntfy_validate_cache_duration() {
  local value="$1"

  if [[ ! "${value}" =~ ^[1-9][0-9]*(s|m|h)$ ]]; then
    deploy_fail "NTFY_CACHE_DURATION 必须是正整数加 s、m 或 h，例如 24h。"
  fi
}

ntfy_validate_topic() {
  local value="$1"

  if [[ ! "${value}" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
    deploy_fail "NORTHSTAR_NTFY_TOPIC 只能使用 1-64 位字母、数字、下划线或连字符。"
  fi
}

ntfy_validate_token() {
  local value="$1"

  if [[ ! "${value}" =~ ^tk_[A-Za-z0-9]{29}$ ]]; then
    deploy_fail "NORTHSTAR_NTFY_TOKEN 必须是 ntfy 生成的 32 位 tk_ 访问令牌。"
  fi
}

ntfy_validate_username() {
  local variable_name="$1"
  local value="$2"

  if [[ ! "${value}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$ ]]; then
    deploy_fail "${variable_name} 只能使用 3-64 位字母、数字、下划线或连字符，且必须以字母或数字开头。"
  fi
}

ntfy_validate_password() {
  local variable_name="$1"
  local value="$2"

  if [ "${#value}" -lt 16 ] || [ "${#value}" -gt 256 ] || [[ "${value}" =~ [[:cntrl:]] ]]; then
    deploy_fail "${variable_name} 必须为 16-256 位且不含控制字符的密码。"
  fi
}

ntfy_validate_deployment_config() {
  NTFY_DEPLOY_ENABLED="${NTFY_DEPLOY_ENABLED:-0}"
  NTFY_PUBLIC_HOST="${NTFY_PUBLIC_HOST:-}"
  NTFY_ACME_EMAIL="${NTFY_ACME_EMAIL:-}"
  NTFY_IMAGE="${NTFY_IMAGE:-binwiederhier/ntfy:v2.27.0}"
  NTFY_CADDY_IMAGE="${NTFY_CADDY_IMAGE:-caddy:2.10.2-alpine}"
  NTFY_CONFIG_DIR="${NTFY_CONFIG_DIR:-/etc/northstar-ntfy}"
  NTFY_DATA_DIR="${NTFY_DATA_DIR:-/var/lib/northstar-ntfy}"
  NTFY_CACHE_DURATION="${NTFY_CACHE_DURATION:-24h}"

  deploy_assert_bool "NTFY_DEPLOY_ENABLED" "${NTFY_DEPLOY_ENABLED}"
  if [ "${NTFY_DEPLOY_ENABLED}" = "0" ]; then
    return 0
  fi

  ntfy_normalize_public_host "NTFY_PUBLIC_HOST" "${NTFY_PUBLIC_HOST}"
  ntfy_validate_acme_email "${NTFY_ACME_EMAIL}"
  ntfy_validate_image "NTFY_IMAGE" "${NTFY_IMAGE}"
  ntfy_validate_image "NTFY_CADDY_IMAGE" "${NTFY_CADDY_IMAGE}"
  ntfy_normalize_path "NTFY_CONFIG_DIR" "${NTFY_CONFIG_DIR}" "config"
  ntfy_normalize_path "NTFY_DATA_DIR" "${NTFY_DATA_DIR}" "data"
  ntfy_validate_cache_duration "${NTFY_CACHE_DURATION}"
}
