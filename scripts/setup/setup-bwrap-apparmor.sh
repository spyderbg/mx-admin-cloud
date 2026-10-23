#!/usr/bin/env bash
# Ubuntu 24.04 Worker prerequisite: install the DISTRIBUTION-PROVIDED
# bwrap-userns-restrict AppArmor profile only when it is necessary and safe.
# No global sysctl changes. No automatic package installation. Never weakens AppArmor:
# no complain mode, no unconfined profile, no local overrides, no disabled restriction.
set -Eeuo pipefail

SCRIPT_NAME="${0##*/}"
SOURCE_PROFILE=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
TARGET_PROFILE=/etc/apparmor.d/bwrap-userns-restrict
LOCAL_OVERRIDES=(/etc/apparmor.d/local/bwrap-userns-restrict /etc/apparmor.d/local/unpriv_bwrap)
PROFILE_NAMES=(bwrap unpriv_bwrap)                         # the profiles that file defines
MARKER_DIR=/var/lib/mx-worker-bootstrap
MARKER_FILE="$MARKER_DIR/bwrap-userns-restrict.sha256"    # receipt: a completed, verified installation
PENDING_FILE="$MARKER_DIR/bwrap-userns-restrict.pending"  # receipt: an installation not (yet) completed
BWRAP=/usr/bin/bwrap
SYSCTL_KEY=kernel.apparmor_restrict_unprivileged_userns
SYSTEM_DIRS=(/bin /sbin /lib /lib32 /lib64 /libx32)        # as the coordinator's sandbox.py

# Exit codes: 0 success / PASS; 1 error, unsupported or refused; 2 --check: sandbox FAIL; 64 usage.
usage() {
  cat <<USAGE
Usage: $SCRIPT_NAME --check | --apply | --rollback

  --check      Read-only host diagnostics and an unprivileged bubblewrap preflight with the
               namespaces and mounts of the Worker sandbox. No sudo, no changes.
  --apply      Install and load the distro-provided AppArmor profile if needed.
               Refuses existing, conflicting or locally overridden profiles; requires sudo.
               Undoes its own partial installation if any step fails.
  --rollback   Unload and remove ONLY a profile installed by this script, after checking
               its hash against this script's receipt. Safe to repeat.

Supported installation target: Ubuntu 24.04, /usr/bin/bwrap.
Do not execute the entire script with sudo; it uses sudo only where required.
USAGE
}

log() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
fail() {  # fail "summary" ["detail line" ...]
  printf 'ERROR: %s\n' "$1" >&2
  shift
  local line
  for line in "$@"; do printf '  %s\n' "$line" >&2; done
  exit 1
}

[[ $# -eq 1 ]] || { usage >&2; exit 64; }
case "$1" in
  --check|--apply|--rollback) MODE="$1" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 64 ;;
esac

# The bwrap test must always run unprivileged, from the actual terminal/session.
[[ "$EUID" -ne 0 ]] || fail "Run as a normal user, not with sudo."
[[ -f /etc/os-release ]] || fail "Cannot identify operating system."
# shellcheck source=/dev/null
. /etc/os-release
restriction="$(sysctl -n "$SYSCTL_KEY" 2>/dev/null || true)"

sha256() { sha256sum -- "$1" | cut -d' ' -f1; }
have() {  # an executable command: bash's `command -v` also reports a non-executable file
  local path
  path="$(command -v "$1" 2>/dev/null)" && [[ -x "$path" ]]
}
exists() { [[ -e "$1" || -L "$1" ]]; }

read_receipt() {  # the sha256 recorded in a receipt file (one 64-hex line)
  local value
  [[ -f "$1" && ! -L "$1" ]] || return 1
  value="$(tr -d '[:space:]' < "$1")" || return 1
  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$value"
}

# -- bubblewrap probes ----------------------------------------------------------------------

sandbox_base() {  # the root of the coordinator's sandbox (sandbox.py SandboxPlan.argv)
  BASE=(--die-with-parent --new-session --unshare-all --ro-bind /usr /usr)
  local dir
  for dir in "${SYSTEM_DIRS[@]}"; do
    if [[ -L "$dir" ]]; then BASE+=(--symlink "$(readlink "$dir")" "$dir")
    elif [[ -d "$dir" ]]; then BASE+=(--ro-bind "$dir" "$dir"); fi
  done
  BASE+=(--ro-bind /etc /etc --proc /proc --dev /dev --tmpfs /tmp --dir /var --tmpfs /var/tmp)
}

PROBE_ERROR=""
bwrap_works() {
  # No sudo: a root test would not validate the real unprivileged restriction. Like the Worker
  # sandbox: all namespaces (user, pid, net, ipc, uts, cgroup), a read-only root, /proc, /dev,
  # tmpfs /tmp, a writable bind (a role root) and a cleared environment. Writes only to its own
  # temporary directory.
  local work err rc=0
  work="$(mktemp -d)" && err="$(mktemp)" || { PROBE_ERROR="mktemp failed"; return 1; }
  sandbox_base
  timeout 20s "$BWRAP" "${BASE[@]}" --bind "$work" /work --remount-ro / --chdir /work --clearenv \
    --setenv PATH /usr/bin:/bin /bin/sh -c 'test -r /proc/self/stat && : > /tmp/t && : > /work/ok' \
    >/dev/null 2>"$err" || rc=$?
  [[ $rc -eq 0 && ! -f "$work/ok" ]] && rc=1
  PROBE_ERROR="$(head -c 300 "$err" | tr '\n' ' ')"
  rm -rf -- "$work" "$err"
  return "$rc"
}

nested_bwrap_works() {  # informational: a sandbox inside ours (Codex under the bwrap backend)
  sandbox_base
  timeout 20s "$BWRAP" "${BASE[@]}" "$BWRAP" --ro-bind /usr /usr --unshare-user /usr/bin/true >/dev/null 2>&1
}

shell_context() {  # the AppArmor label of this shell, which the bwrap result depends on
  { cat /proc/self/attr/apparmor/current 2>/dev/null || cat /proc/self/attr/current 2>/dev/null \
      || echo unknown; } | tr -d '\0\n'
}

# -- AppArmor policy -------------------------------------------------------------------------

other_profiles() {
  # Profile files other than ours that define or attach to bwrap. If in doubt, fail closed.
  # Includes profile definitions named bwrap without '/usr/bin/bwrap' on the line.
  find /etc/apparmor.d -maxdepth 2 -type f ! -path "$TARGET_PROFILE" \
    ! -path '/etc/apparmor.d/disable/*' -print0 2>/dev/null \
    | xargs -0 -r grep -lE '(^[[:space:]]*profile[[:space:]]+[^#]*bwrap|/usr/bin/bwrap)' 2>/dev/null \
    || true
}

local_overrides() {  # local include files with rules would change the distribution policy
  local file
  for file in "${LOCAL_OVERRIDES[@]}"; do
    if [[ -L "$file" ]] || { [[ -f "$file" ]] && grep -qvE '^[[:space:]]*(#.*)?$' "$file"; }; then
      printf '%s\n' "$file"
    fi
  done
}

bwrap_profile_names() {  # from aa-status output: loaded PROFILE names containing "bwrap"
  # Only the "N profiles are in <mode> mode." sections: process lines such as
  # "/usr/bin/bwrap (1234) unprivileged_userns" are running sandboxes, not policy.
  awk '/^[^[:space:]]/ { in_profiles = ($0 ~ / profiles are in /); next }
       in_profiles && $1 ~ /bwrap/ { print $1 }' | sort -u
}

LOADED=""
read_loaded() {
  # Loaded bwrap profiles. An unprivileged aa-status cannot read the profile set (it exits 4),
  # so this needs sudo. Returns 1 when the set could not be read.
  local out rc=0
  out="$(sudo aa-status 2>&1)" || rc=$?
  [[ $rc -eq 0 || $rc -eq 2 ]] || { LOADED="aa-status exit $rc: $(printf '%s' "$out" | head -n 2 | tr '\n' ' ')"; return 1; }
  LOADED="$(printf '%s\n' "$out" | bwrap_profile_names)"
}

our_profiles_loaded() {  # 0 loaded (any of ours), 1 not loaded, 2 cannot tell
  read_loaded || return 2
  local name
  for name in "${PROFILE_NAMES[@]}"; do
    grep -qxF -- "$name" <<<"$LOADED" && return 0
  done
  return 1
}

source_status() {  # "ok", or why the distribution profile cannot be trusted
  [[ -f "$SOURCE_PROFILE" && ! -L "$SOURCE_PROFILE" ]] || { echo "missing"; return; }
  local owner expected info
  owner="$(dpkg-query -S "$SOURCE_PROFILE" 2>/dev/null | head -n 1 | cut -d: -f1)" || owner=""
  [[ -n "$owner" ]] || { echo "not owned by an installed package"; return; }
  expected=""
  for info in "/var/lib/dpkg/info/$owner.md5sums" /var/lib/dpkg/info/"$owner":*.md5sums; do
    [[ -f "$info" ]] || continue
    expected="$(awk -v p="${SOURCE_PROFILE#/}" '$2 == p { print $1; exit }' "$info")"
    [[ -n "$expected" ]] && break
  done
  [[ -n "$expected" ]] || { echo "no package checksum for it (package $owner)"; return; }
  [[ "$(md5sum < "$SOURCE_PROFILE" | cut -d' ' -f1)" == "$expected" ]] \
    || { echo "modified since package $owner installed it"; return; }
  echo ok
}

receipt_status() {  # describes the script-owned installation state (read-only)
  local expected
  if exists "$MARKER_FILE"; then
    expected="$(read_receipt "$MARKER_FILE")" || { echo "receipt unreadable or malformed ($MARKER_FILE)"; return; }
    if [[ -f "$TARGET_PROFILE" && ! -L "$TARGET_PROFILE" && "$(sha256 "$TARGET_PROFILE")" == "$expected" ]]; then
      echo "installed by this script (receipt matches)"
    else
      echo "receipt present, but $TARGET_PROFILE is missing or changed"
    fi
  elif exists "$PENDING_FILE"; then
    echo "an installation by this script did not finish: run --rollback"
  elif exists "$TARGET_PROFILE"; then
    echo "present, not installed by this script (vendor- or operator-managed)"
  else
    echo "not installed"
  fi
}

# -- modes -----------------------------------------------------------------------------------

require_supported_host() {
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] ||
    fail "Installation is supported only on Ubuntu 24.04 (found ${PRETTY_NAME:-unknown})."
  have timeout || fail "GNU timeout is required."
  have bwrap || fail "Install the bubblewrap package first."
  [[ "$(readlink -f "$(command -v bwrap)")" == "$BWRAP" ]] ||
    fail "Expected the distro's $BWRAP. Review this host manually."
  [[ "$restriction" == 1 ]] ||
    fail "Expected $SYSCTL_KEY=1 (got ${restriction:-missing}); review host security before continuing." \
         "This procedure never changes that setting."
}

run_check() {
  require_supported_host
  local label candidates overrides out loaded_rc=0 status=0
  log "Binary: $BWRAP; userns restriction: $restriction"
  if ! have aa-status; then log "AppArmor: aa-status unavailable (install the 'apparmor' package)"
  elif aa-status --enabled 2>/dev/null; then log "AppArmor: enabled"; else log "AppArmor: NOT enabled"; fi
  label="$(shell_context)"
  log "This shell's AppArmor context: $label"
  [[ "$label" == unconfined ]] || warn "This shell is confined ('$label'). The result applies to this context only:" \
    "run --check from the terminal or service that will run the Worker."
  log "Distro profile: $(source_status)"
  log "Configured profile: $(receipt_status)"
  overrides="$(local_overrides)"
  log "Local overrides of the profile: ${overrides:-none}"
  log "Other candidate profile files:"
  candidates="$(other_profiles)"
  if [[ -z "$candidates" ]]; then log "  (none detected)"; else printf '%s\n' "$candidates" | sed 's/^/  /'; fi
  out="$(aa-status 2>/dev/null)" || loaded_rc=$?
  if [[ $loaded_rc -eq 0 || $loaded_rc -eq 2 ]]; then
    log "Loaded bwrap profiles: $(printf '%s\n' "$out" | bwrap_profile_names | tr '\n' ' ')"
  else
    log "Loaded profiles: not readable without privileges (check with: sudo aa-status | grep -i bwrap)"
  fi
  if bwrap_works; then
    log "PASS: the Worker sandbox (all namespaces, read-only root, /proc, /dev, tmpfs, writable bind) starts from this session."
  else
    log "FAIL: the Worker sandbox cannot start from this session: ${PROBE_ERROR:-no error output}"
    status=2
  fi
  if nested_bwrap_works; then
    log "Nested bubblewrap: works."
  else
    log "Nested bubblewrap: denied. Expected with bwrap-userns-restrict: Codex's own sandbox cannot start inside the" \
        "coordinator's, so the optional execution_backend: bwrap cannot run Codex. native-cli (the default) and the gates are unaffected."
  fi
  log "Definitive test: python scripts/feature.py sandbox-check --apply (project venv, from the Worker's context)."
  exit "$status"
}

INSTALL_HASH=""
abort_install() {  # undo this script's own partial installation, then exit 1
  trap - INT TERM HUP
  printf 'ERROR: %s\n' "$1" >&2
  log "Undoing this script's partial installation..." >&2
  local undone=1 state=0
  if exists "$TARGET_PROFILE"; then
    if [[ -f "$TARGET_PROFILE" && ! -L "$TARGET_PROFILE" && "$(sha256 "$TARGET_PROFILE")" == "$INSTALL_HASH" ]]; then
      our_profiles_loaded || state=$?
      if [[ $state -eq 0 ]]; then
        sudo apparmor_parser -R "$TARGET_PROFILE" >/dev/null 2>&1 || true
        state=0; our_profiles_loaded || state=$?
      fi
      if [[ $state -eq 1 ]]; then sudo rm -f -- "$TARGET_PROFILE" || undone=0; else undone=0; fi
    else
      undone=0  # not the file this run installed: never touch it
    fi
  fi
  if [[ $undone -eq 1 ]] && ! exists "$TARGET_PROFILE"; then
    sudo rm -f -- "$PENDING_FILE" || true
    log "Partial installation undone; the host is as before." >&2
  else
    log "Could not fully undo it. The receipt $PENDING_FILE is kept;" \
        "review $TARGET_PROFILE, then run: $SCRIPT_NAME --rollback" >&2
  fi
  exit 1
}

run_apply() {
  require_supported_host
  aa-status --enabled 2>/dev/null || fail "AppArmor is not enabled; this procedure does not apply."
  if bwrap_works; then
    log "Already working: no change required."
    log "Definitive test: python scripts/feature.py sandbox-check --apply (project venv, from the Worker's context)."
    exit 0
  fi
  log "The Worker sandbox cannot start: ${PROBE_ERROR:-no error output}"
  ! exists "$PENDING_FILE" ||
    fail "An earlier installation by this script did not finish ($PENDING_FILE)." "Run $SCRIPT_NAME --rollback first."
  if exists "$TARGET_PROFILE"; then
    if exists "$MARKER_FILE"; then
      fail "$TARGET_PROFILE is installed by this script, but the sandbox still fails." \
           "See Troubleshooting in docs_agents/coordination/worker-host-setup.md; remove it with --rollback."
    fi
    fail "$TARGET_PROFILE already exists. Refusing to overwrite. Inspect it manually."
  fi
  local status found=()
  status="$(source_status)"
  [[ "$status" == ok ]] || fail "Distribution profile $SOURCE_PROFILE: $status." \
    "Install the 'apparmor-profiles' package, or inspect this Ubuntu release. Refusing an unverified source."
  mapfile -t found < <(local_overrides)
  [[ ${#found[@]} -eq 0 ]] || fail "Local overrides would change the distribution policy:" "${found[@]}" "Review manually."
  mapfile -t found < <(other_profiles)
  [[ ${#found[@]} -eq 0 ]] || fail "Potential conflicting profile files detected:" "${found[@]}" "Review manually."
  have sudo || fail "sudo is required for host modifications."
  have apparmor_parser || [[ -x /usr/sbin/apparmor_parser ]] ||
    fail "apparmor_parser is unavailable; install the 'apparmor' package."
  log "Authenticating with sudo (nothing has been changed yet)..."
  sudo -v || fail "sudo authentication failed; nothing changed."
  read_loaded || fail "Cannot read the loaded AppArmor profiles: $LOADED"
  mapfile -t found < <(printf '%s' "$LOADED")
  [[ ${#found[@]} -eq 0 ]] || fail "Potential conflicting loaded profiles detected:" "${found[@]}" "Review manually."

  log "Checking distribution profile syntax..."
  sudo apparmor_parser -Q "$SOURCE_PROFILE" || fail "Profile syntax invalid; no changes made."

  # From here on, a failure undoes this script's own steps (abort_install).
  INSTALL_HASH="$(sha256 "$SOURCE_PROFILE")"
  trap 'abort_install "interrupted"' INT TERM HUP
  log "Installing distribution profile (requires sudo)..."
  sudo install -d -m 0755 -- "$MARKER_DIR" || abort_install "Could not create $MARKER_DIR."
  printf '%s\n' "$INSTALL_HASH" | sudo tee "$PENDING_FILE" >/dev/null || abort_install "Could not write $PENDING_FILE."
  sudo install -m 0644 -- "$SOURCE_PROFILE" "$TARGET_PROFILE" || abort_install "Could not install $TARGET_PROFILE."
  [[ "$(sha256 "$TARGET_PROFILE")" == "$INSTALL_HASH" ]] || abort_install "The installed copy differs from the source."
  sudo apparmor_parser -r "$TARGET_PROFILE" || abort_install "The parser rejected the profile."
  bwrap_works || abort_install "Profile loaded, but the Worker sandbox still fails: ${PROBE_ERROR:-no error output}"
  # Record ownership: the pending receipt becomes the installation receipt (one rename).
  sudo mv -f -- "$PENDING_FILE" "$MARKER_FILE" || abort_install "Could not record the installation receipt."
  trap - INT TERM HUP
  log "PASS: the Worker sandbox starts. Script-owned installation recorded in $MARKER_FILE."
  log "Global AppArmor userns restriction: $(sysctl -n "$SYSCTL_KEY" 2>/dev/null || echo unknown) (unchanged)."
  log "Definitive test: python scripts/feature.py sandbox-check --apply (project venv, from the Worker's context)."
}

run_rollback() {
  local receipt="" kind="" expected state=0
  if exists "$MARKER_FILE"; then receipt="$MARKER_FILE"; kind=installed
  elif exists "$PENDING_FILE"; then receipt="$PENDING_FILE"; kind=pending; fi
  if [[ -z "$kind" ]]; then
    exists "$TARGET_PROFILE" && fail "No script-owned installation receipt, but $TARGET_PROFILE exists." \
      "Refusing to remove a potentially vendor-managed profile. Review manually."
    log "Nothing to roll back: no script-owned installation and no $TARGET_PROFILE."
    exit 0
  fi
  expected="$(read_receipt "$receipt")" || fail "Receipt $receipt is unreadable or malformed. Review manually."
  have sudo || fail "sudo is required for host modifications."
  have apparmor_parser || [[ -x /usr/sbin/apparmor_parser ]] ||
    fail "apparmor_parser is unavailable; install the 'apparmor' package."

  if ! exists "$TARGET_PROFILE"; then
    # The file is gone: unload what is still loaded from it (an identical source), then the receipt.
    sudo -v || fail "sudo authentication failed; nothing changed."
    our_profiles_loaded || state=$?
    if [[ $state -eq 0 ]]; then
      [[ -f "$SOURCE_PROFILE" && "$(sha256 "$SOURCE_PROFILE")" == "$expected" ]] ||
        fail "$TARGET_PROFILE is missing, but its profiles are still loaded and no identical source exists." \
             "Reboot (the file is gone, so it will not load again) or unload manually; then remove $receipt."
      sudo apparmor_parser -R "$SOURCE_PROFILE" || fail "Could not unload the profiles; the receipt is kept."
    elif [[ $state -eq 2 ]]; then
      fail "Cannot read the loaded AppArmor profiles: $LOADED" "Nothing changed."
    fi
    sudo rm -f -- "$receipt" || fail "Could not remove $receipt."
    log "Rolled back: $TARGET_PROFILE was already gone; its profiles are not loaded; receipt removed ($kind)."
    exit 0
  fi
  [[ -f "$TARGET_PROFILE" && ! -L "$TARGET_PROFILE" ]] || fail "$TARGET_PROFILE is not a regular file. Review manually."
  [[ "$(sha256 "$TARGET_PROFILE")" == "$expected" ]] ||
    fail "$TARGET_PROFILE changed since this script installed it. Refusing rollback." "Review it manually."
  sudo -v || fail "sudo authentication failed; nothing changed."
  our_profiles_loaded || state=$?
  [[ $state -ne 2 ]] || fail "Cannot read the loaded AppArmor profiles: $LOADED" "Nothing changed."
  if [[ $state -eq 0 ]]; then
    sudo apparmor_parser -R "$TARGET_PROFILE" || fail "Could not unload profile; nothing removed."
  fi
  sudo rm -f -- "$TARGET_PROFILE" || fail "Could not remove $TARGET_PROFILE; the receipt is kept for another --rollback."
  sudo rm -f -- "$receipt" || fail "Profile removed, but could not remove $receipt; run --rollback again."
  log "Rolled back script-installed profile ($kind). Global restriction is still ${restriction:-unknown}."
}

log "Host: ${PRETTY_NAME:-unknown}; mode: ${MODE#--}"
case "$MODE" in
  --check) run_check ;;
  --apply) run_apply ;;
  --rollback) run_rollback ;;
esac
