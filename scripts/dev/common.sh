log() {
  printf "\n%s\n" "$1"
}

fail() {
  printf "\n错误：%s\n" "$1" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  local message="$2"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    fail "${message}"
  fi
}

require_supported_os() {
  case "$(uname -s)" in
    Darwin|Linux)
      ;;
    *)
      fail "开发环境只支持 macOS 和 Linux。"
      ;;
  esac
}
