deploy_ssh_options() {
  if [ "${SSH_CONTROL}" = "1" ]; then
    printf "%s\n" \
      -o ControlMaster=auto \
      -o ControlPersist=10m \
      -o "ControlPath=${SSH_CONTROL_PATH}"
  fi
}

deploy_ssh() {
  local options=()
  local option

  while IFS= read -r option; do
    options+=("${option}")
  done < <(deploy_ssh_options)
  ssh "${options[@]}" "$@"
}

deploy_scp() {
  local options=()
  local option

  while IFS= read -r option; do
    options+=("${option}")
  done < <(deploy_ssh_options)
  scp "${options[@]}" "$@"
}

deploy_start_ssh_control() {
  if [ "${SSH_CONTROL}" = "1" ]; then
    deploy_ssh -Nf "${DEPLOY_HOST}"
  fi
}

deploy_close_ssh_control() {
  if [ "${SSH_CONTROL}" = "1" ]; then
    deploy_ssh -O exit "${DEPLOY_HOST}" >/dev/null 2>&1 || true
    rm -f "${SSH_CONTROL_PATH}" || true
  fi
}
