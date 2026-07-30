print_docker_install_help() {
  case "$(uname -s)" in
    Darwin)
      cat >&2 <<'EOF'

错误：未找到 docker。请安装并启动 Docker Desktop：

  brew install --cask docker
  open -a Docker

首次启动时请在 Docker Desktop 界面中完成许可确认和初始化。等待启动完成后验证：

  docker --version
  docker compose version
  docker info

然后重新运行：

  scripts/setup_dev.sh
EOF
      ;;
    Linux)
      cat >&2 <<'EOF'

错误：未找到 docker。开发机可使用 Docker 官方便捷安装脚本安装 Docker Engine：

  curl -fsSL https://get.docker.com -o /tmp/northstar-get-docker.sh
  sudo sh /tmp/northstar-get-docker.sh
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"

注销并重新登录，使 docker 用户组生效，然后验证：

  docker --version
  docker compose version
  docker info

最后重新运行：

  scripts/setup_dev.sh

生产服务器请按照 https://docs.docker.com/engine/install/ 配置正式软件源和版本。
EOF
      ;;
  esac

  exit 1
}

print_compose_install_help() {
  case "$(uname -s)" in
    Darwin)
      fail "当前 Docker 未提供 Compose v2。请升级 Docker Desktop，再运行 docker compose version 验证。"
      ;;
    Linux)
      cat >&2 <<'EOF'

错误：当前 Docker 未提供 Compose v2。

Debian / Ubuntu：

  sudo apt-get update
  sudo apt-get install docker-compose-plugin

Fedora / RHEL / CentOS：

  sudo dnf install docker-compose-plugin

安装后运行 docker compose version 验证，再重新执行 scripts/setup_dev.sh。
EOF
      exit 1
      ;;
  esac
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    print_docker_install_help
  fi

  if ! docker compose version >/dev/null 2>&1; then
    print_compose_install_help
  fi

  if docker info >/dev/null 2>&1; then
    return 0
  fi

  case "$(uname -s)" in
    Darwin)
      fail "Docker daemon 尚未运行。请执行 open -a Docker，等待启动完成，再运行 docker info 验证。"
      ;;
    Linux)
      fail "Docker daemon 不可用。请执行 sudo systemctl enable --now docker；若提示权限不足，请把当前用户加入 docker 组后重新登录。"
      ;;
  esac
}
