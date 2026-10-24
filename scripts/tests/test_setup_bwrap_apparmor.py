"""scripts/setup/setup-bwrap-apparmor.sh on an isolated fake Ubuntu 24.04 host.

The unmodified script runs inside a bubblewrap sandbox that shows it a fake host: /etc/apparmor.d,
/var/lib and the distribution profile are temporary fixtures, and every command that could change or
inspect the real system (sudo, apparmor_parser, aa-status, dpkg-query, sysctl, bwrap) is a mock, both
first on PATH and bind-mounted over the real binary. The real /etc and /var/lib are not visible, and
no_new_privs blocks any setuid binary, so nothing here can modify the real host.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from feature_coordinator.sandbox import SandboxUnavailableError, check_bwrap

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup" / "setup-bwrap-apparmor.sh"
SOURCE = "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
TARGET = "/etc/apparmor.d/bwrap-userns-restrict"
MARKER = "/var/lib/mx-worker-bootstrap/bwrap-userns-restrict.sha256"
PENDING = "/var/lib/mx-worker-bootstrap/bwrap-userns-restrict.pending"

PROFILE = """\
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {
  allow all,
  allow userns,
  allow px /** -> bwrap//&unpriv_bwrap,
  include if exists <local/bwrap-userns-restrict>
}

profile unpriv_bwrap flags=(attach_disconnected) {
  allow all,
  audit deny capability,
  include if exists <local/unpriv_bwrap>
}
"""

UBUNTU_2404 = 'NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nPRETTY_NAME="Ubuntu 24.04.5 LTS"\n'

# -- mocks (bash, inside the sandbox; state under /mock) ---------------------------------------

MOCKS = {
    "sudo": r"""#!/bin/bash
echo "sudo $*" >> /mock/calls.log
[[ "$(cat /mock/sudo 2>/dev/null)" == denied ]] && { echo "sudo: 3 incorrect password attempts" >&2; exit 1; }
[[ "${1:-}" == -v ]] && exit 0
if [[ -s /mock/sudo-fail ]] && [[ "$*" == *"$(cat /mock/sudo-fail)"* ]]; then
  echo "sudo mock: forced failure of: $*" >&2; exit 1
fi
SUDO_MOCK=1 exec "$@"
""",
    "apparmor_parser": r"""#!/bin/bash
echo "apparmor_parser sudo=${SUDO_MOCK:-0} $*" >> /mock/calls.log
[[ "${SUDO_MOCK:-0}" == 1 ]] || { echo "apparmor_parser: Access denied. You need policy admin privileges" >&2; exit 1; }
op="$1"; file="$2"
names() { sed -nE 's/^[[:space:]]*profile[[:space:]]+([^[:space:]]+).*/\1/p' "$file"; }
touch /mock/loaded
case "$op" in
  -Q) [[ "$(cat /mock/parse 2>/dev/null)" != fail ]] || { echo "AppArmor parser error at line 4: syntax error" >&2; exit 1; } ;;
  -r) [[ "$(cat /mock/load 2>/dev/null)" != fail ]] || { echo 'apparmor_parser: Unable to replace "bwrap".' >&2; exit 1; }
      { cat /mock/loaded; names; } | sort -u > /mock/loaded.new && mv /mock/loaded.new /mock/loaded ;;
  -R) [[ "$(cat /mock/unload 2>/dev/null)" != fail ]] || { echo 'apparmor_parser: Unable to remove "bwrap".' >&2; exit 1; }
      grep -vxF -f <(names) /mock/loaded > /mock/loaded.new || true; mv /mock/loaded.new /mock/loaded ;;
  *) echo "apparmor_parser mock: unexpected $*" >&2; exit 2 ;;
esac
""",
    "aa-status": r"""#!/bin/bash
echo "aa-status sudo=${SUDO_MOCK:-0} $*" >> /mock/calls.log
if [[ "$(cat /mock/apparmor 2>/dev/null)" == disabled ]]; then echo "apparmor module is not loaded."; exit 1; fi
[[ "${1:-}" == --enabled ]] && exit 0
[[ "${SUDO_MOCK:-0}" == 1 ]] || { echo "You do not have enough privilege to read the profile set."; exit 4; }
mapfile -t loaded < <(cat /mock/loaded /mock/foreign 2>/dev/null | sort -u)
echo "apparmor module is loaded."
echo "$(( ${#loaded[@]} + 1 )) profiles are loaded."
echo "$(( ${#loaded[@]} + 1 )) profiles are in enforce mode."
echo "   /usr/bin/man"
for name in "${loaded[@]}"; do echo "   $name"; done
echo "0 profiles are in complain mode."
echo "2 processes have profiles defined."
echo "2 processes are in enforce mode."
echo "   /usr/bin/bwrap (4242) unprivileged_userns"
echo "   /usr/sbin/cupsd (77) /usr/sbin/cupsd"
""",
    "dpkg-query": r"""#!/bin/bash
echo "dpkg-query $*" >> /mock/calls.log
if [[ "${1:-}" == -S && "$(cat /mock/dpkg 2>/dev/null)" != unowned ]]; then echo "apparmor-profiles: $2"; exit 0; fi
echo "dpkg-query: no path found matching pattern $2" >&2; exit 1
""",
    "sysctl": r"""#!/bin/bash
echo "sysctl $*" >> /mock/calls.log
if [[ $# -eq 2 && "$1" == -n && "$2" == kernel.apparmor_restrict_unprivileged_userns ]]; then
  value="$(cat /mock/sysctl 2>/dev/null)"
  [[ -n "$value" ]] && { echo "$value"; exit 0; }
  echo "sysctl: cannot stat /proc/sys/kernel/apparmor_restrict_unprivileged_userns" >&2; exit 255
fi
echo "WRITE $*" >> /mock/sysctl-writes; echo "sysctl mock: refused $*" >&2; exit 1
""",
    "bwrap": r"""#!/bin/bash
echo "bwrap $*" >> /mock/calls.log
mode="$(cat /mock/bwrap 2>/dev/null || echo broken)"; works=0
case "$mode" in
  works) works=1 ;;
  works-when-loaded) grep -qx bwrap /mock/loaded 2>/dev/null && works=1 ;;
esac
nested=0; for arg in "$@"; do [[ "$arg" == /usr/bin/bwrap ]] && nested=1; done
if [[ $works -eq 1 && $nested -eq 1 ]]; then
  [[ "$(cat /mock/nested 2>/dev/null)" == works ]] && exit 0
  echo "bwrap: No permissions to create new namespace, likely because the kernel does not allow non-privileged user namespaces." >&2
  exit 1
fi
if [[ $works -eq 1 ]]; then  # emulate the probe's writable bind (--bind SRC /work)
  prev2=""; prev=""
  for arg in "$@"; do [[ "$prev2" == --bind && "$arg" == /work ]] && : > "$prev/ok"; prev2="$prev"; prev="$arg"; done
  exit 0
fi
echo "bwrap: setting up uid map: Permission denied" >&2; exit 1
""",
}
REAL_PATHS = {"sudo": "/usr/bin/sudo", "apparmor_parser": "/usr/sbin/apparmor_parser",
              "aa-status": "/usr/sbin/aa-status",
              "dpkg-query": "/usr/bin/dpkg-query", "sysctl": "/usr/sbin/sysctl", "bwrap": "/usr/bin/bwrap"}

# The only privileged commands the script may run, and only on these paths.
SUDO_ALLOWED = (
    "sudo -v", "sudo aa-status",
    f"sudo apparmor_parser -Q {SOURCE}", f"sudo apparmor_parser -r {TARGET}",
    f"sudo apparmor_parser -R {TARGET}", f"sudo apparmor_parser -R {SOURCE}",
    "sudo install -d -m 0755 -- /var/lib/mx-worker-bootstrap", f"sudo tee {PENDING}",
    f"sudo install -m 0644 -- {SOURCE} {TARGET}", f"sudo mv -f -- {PENDING} {MARKER}",
    f"sudo rm -f -- {TARGET}", f"sudo rm -f -- {PENDING}", f"sudo rm -f -- {MARKER}",
)


@dataclass
class Result:
    """One run of the script: exit code, output and the mocks' call log."""
    code: int
    out: str
    err: str
    calls: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.out + self.err

    def sudo(self) -> list[str]:
        return [c for c in self.calls if c.startswith("sudo ")]


class FakeHost:
    """A fake Ubuntu host: fixture directories and mock state, run through bubblewrap."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.etc = root / "etc-apparmor.d"
        self.extra = root / "extra-profiles"
        self.var_lib = root / "var-lib"
        self.mock = root / "mock"
        self.mockbin = root / "mockbin"
        for path in (self.etc / "local", self.etc / "disable", self.etc / "abstractions", self.extra,
                     self.var_lib / "dpkg" / "info", self.mock, self.mockbin):
            path.mkdir(parents=True)
        (root / "os-release").write_text(UBUNTU_2404, encoding="utf-8")
        self.set_source(PROFILE)
        (self.etc / "abstractions" / "base").write_text("# unrelated\n", encoding="utf-8")
        for name, body in MOCKS.items():
            path = (root / "bwrap-mock") if name == "bwrap" else (self.mockbin / name)
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        (root / "absent").write_text("", encoding="utf-8")  # a non-executable placeholder: "not installed"
        self.missing: set[str] = set()
        self.state(sysctl="1", bwrap="broken", nested="denied")

    def set_source(self, text: str) -> None:
        (self.extra / "bwrap-userns-restrict").write_text(text, encoding="utf-8")
        digest = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
        (self.var_lib / "dpkg" / "info" / "apparmor-profiles.md5sums").write_text(
            f"{digest}  {SOURCE.lstrip('/')}\n", encoding="utf-8")

    def state(self, **values: str) -> None:
        for key, value in values.items():
            path = self.mock / key.replace("_", "-")
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(value, encoding="utf-8")

    def path(self, inside: str) -> Path:
        """The fixture file behind a path the script sees."""
        for prefix, base in (("/etc/apparmor.d/", self.etc), ("/var/lib/", self.var_lib),
                             ("/usr/share/apparmor/extra-profiles/", self.extra)):
            if inside.startswith(prefix):
                return base / inside[len(prefix):]
        raise ValueError(inside)

    def loaded(self) -> set[str]:
        path = self.mock / "loaded"
        return set(path.read_text(encoding="utf-8").split()) if path.exists() else set()

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes()
                for base in (self.etc, self.var_lib, self.extra) for p in sorted(base.rglob("*")) if p.is_file()}

    def run(self, *args: str, uid: int | None = None) -> Result:
        (self.mock / "calls.log").write_text("", encoding="utf-8")
        argv = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-all", "--new-session", "--tmpfs", "/usr"]
        for sub in ("bin", "sbin", "lib", "lib32", "lib64", "libx32", "libexec"):
            argv += ["--ro-bind-try", f"/usr/{sub}", f"/usr/{sub}"]
        for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32"):
            if os.path.islink(top):
                argv += ["--symlink", os.readlink(top), top]
            elif os.path.isdir(top):
                argv += ["--ro-bind", top, top]
        argv += ["--dir", "/usr/share/apparmor", "--bind", str(self.extra), "/usr/share/apparmor/extra-profiles",
                 "--dir", "/etc", "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
                 "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
                 "--ro-bind", str(self.root / "os-release"), "/etc/os-release",
                 "--bind", str(self.etc), "/etc/apparmor.d", "--bind", str(self.var_lib), "/var/lib",
                 "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--bind", str(self.mock), "/mock", "--ro-bind", str(self.mockbin), "/mockbin",
                 "--ro-bind", str(SCRIPT), "/script/setup-bwrap-apparmor.sh"]
        for name, real in REAL_PATHS.items():  # never the real binary, even outside PATH
            if name == "bwrap":
                replacement = self.root / ("absent" if name in self.missing else "bwrap-mock")
            else:
                replacement = self.root / "absent" if name in self.missing else self.mockbin / name
            if name in self.missing:
                (self.mockbin / name).unlink(missing_ok=True)
            if os.path.exists(real):
                argv += ["--ro-bind", str(replacement), real]
        if uid is not None:
            argv += ["--uid", str(uid), "--gid", str(uid)]
        argv += ["--clearenv", "--setenv", "PATH", "/mockbin:/usr/bin:/usr/sbin:/bin:/sbin",
                 "--setenv", "HOME", "/tmp", "--setenv", "LC_ALL", "C",
                 "/bin/bash", "/script/setup-bwrap-apparmor.sh", *args]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        calls = (self.mock / "calls.log").read_text(encoding="utf-8").splitlines()
        result = Result(proc.returncode, proc.stdout, proc.stderr, calls)
        assert "\\n" not in result.text, result.text  # never a literal "\n" in a message
        assert not (self.mock / "sysctl-writes").exists(), "the script tried to change a sysctl"
        for call in result.sudo():  # only allowlisted privileged commands, only on owned paths
            assert call in SUDO_ALLOWED, call
        return result


@pytest.fixture(scope="module")
def bwrap_usable() -> None:
    try:
        check_bwrap()
    except SandboxUnavailableError as exc:
        pytest.skip(f"the fake host needs a working top-level bubblewrap: {exc}")


@pytest.fixture
def host(tmp_path: Path, bwrap_usable) -> FakeHost:
    del bwrap_usable
    return FakeHost(tmp_path / "host")


def install_ok(host: FakeHost) -> Result:
    host.state(bwrap="works-when-loaded")
    result = host.run("--apply")
    assert result.code == 0, result.text
    return result


# -- usage, identity, unsupported hosts ----------------------------------------------------

@pytest.mark.parametrize("args, code", [((), 64), (("--bogus",), 64), (("--check", "--apply"), 64), (("--help",), 0)])
def test_usage(host: FakeHost, args, code) -> None:
    result = host.run(*args)
    assert result.code == code and "Usage:" in result.text and not result.sudo()


@pytest.mark.parametrize("mode", ["--check", "--apply", "--rollback"])
def test_refuses_to_run_as_root(host: FakeHost, mode: str) -> None:
    result = host.run(mode, uid=0)
    assert result.code == 1 and "ERROR: Run as a normal user, not with sudo." in result.err and not result.sudo()


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_unsupported_release(host: FakeHost, mode: str) -> None:
    (host.root / "os-release").write_text(UBUNTU_2404.replace("24.04", "22.04"), encoding="utf-8")
    result = host.run(mode)
    assert result.code == 1 and "supported only on Ubuntu 24.04" in result.err and not result.sudo()


@pytest.mark.parametrize("value", ["0", None])
def test_requires_the_userns_restriction_and_never_changes_it(host: FakeHost, value) -> None:
    host.state(sysctl=value)
    result = host.run("--apply")
    assert result.code == 1 and "apparmor_restrict_unprivileged_userns=1" in result.err
    assert "never changes that setting" in result.err and not result.sudo()


# -- missing dependencies ---------------------------------------------------------------------

@pytest.mark.parametrize("missing, mode, message", [
    ("bwrap", "--check", "Install the bubblewrap package first."),
    ("bwrap", "--apply", "Install the bubblewrap package first."),
    ("sudo", "--apply", "sudo is required for host modifications."),
    ("apparmor_parser", "--apply", "apparmor_parser is unavailable"),
    ("sudo", "--rollback", "sudo is required for host modifications."),
])
def test_missing_dependencies(host: FakeHost, missing: str, mode: str, message: str) -> None:
    if mode == "--rollback":
        install_ok(host)
    host.missing.add(missing)
    before = host.snapshot()
    result = host.run(mode)
    assert result.code == 1 and message in result.err, result.text
    assert host.snapshot() == before


@pytest.mark.parametrize("problem, message", [
    ("missing", "missing"),
    ("unowned", "not owned by an installed package"),
    ("modified", "modified since package apparmor-profiles installed it"),
])
def test_refuses_an_unverified_distribution_profile(host: FakeHost, problem: str, message: str) -> None:
    if problem == "missing":
        (host.extra / "bwrap-userns-restrict").unlink()
    elif problem == "unowned":
        host.state(dpkg="unowned")
    else:
        (host.extra / "bwrap-userns-restrict").write_text(PROFILE + "  allow capability,\n", encoding="utf-8")
    before = host.snapshot()
    result = host.run("--apply")
    assert result.code == 1 and message in result.err and "Refusing an unverified source" in result.err
    assert host.snapshot() == before and not result.sudo()


# -- --check: read-only ------------------------------------------------------------------------

def test_check_reports_a_failing_sandbox_without_changing_anything(host: FakeHost) -> None:
    before = host.snapshot()
    result = host.run("--check")
    assert result.code == 2
    assert "FAIL: the Worker sandbox cannot start from this session: bwrap: setting up uid map: Permission denied" \
        in result.out
    assert "Loaded profiles: not readable without privileges" in result.out
    assert "Distro profile: ok" in result.out and "Configured profile: not installed" in result.out
    assert not result.sudo() and host.snapshot() == before and not host.loaded()


def test_check_passes_and_explains_the_nested_sandbox_limitation(host: FakeHost) -> None:
    host.state(bwrap="works")
    result = host.run("--check")
    assert result.code == 0 and "PASS: the Worker sandbox" in result.out
    assert "Nested bubblewrap: denied" in result.out and "execution_backend: bwrap cannot run Codex" in result.out
    assert "native-cli (the default) and the gates are unaffected" in result.out
    host.state(nested="works")
    assert "Nested bubblewrap: works." in host.run("--check").out


def test_the_probe_uses_the_worker_sandbox_namespaces_and_mounts(host: FakeHost) -> None:
    host.state(bwrap="works")
    probe = next(c for c in host.run("--check").calls if c.startswith("bwrap ") and "/work" in c)
    for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--ro-bind /usr /usr", "--ro-bind /etc /etc",
                 "--proc /proc", "--dev /dev", "--tmpfs /tmp", "--tmpfs /var/tmp", "--remount-ro /", "--clearenv"):
        assert flag in probe, (flag, probe)
    assert " /work --remount-ro" in probe  # a writable bind, like a role root


# -- --apply ------------------------------------------------------------------------------------

def test_apply_changes_nothing_when_the_sandbox_already_works(host: FakeHost) -> None:
    host.state(bwrap="works")
    before = host.snapshot()
    result = host.run("--apply")
    assert result.code == 0 and "Already working: no change required." in result.out
    assert not result.sudo() and host.snapshot() == before


def test_apply_installs_verifies_and_records_ownership(host: FakeHost) -> None:
    result = install_ok(host)
    target, receipt = host.path(TARGET), host.path(MARKER)
    assert target.read_text(encoding="utf-8") == PROFILE and oct(target.stat().st_mode & 0o777) == "0o644"
    assert receipt.read_text(encoding="utf-8").strip() == hashlib.sha256(PROFILE.encode()).hexdigest()
    assert not host.path(PENDING).exists() and host.loaded() == {"bwrap", "unpriv_bwrap"}
    assert result.sudo() == [  # authentication first, the receipt written before the profile
        "sudo -v", "sudo aa-status", f"sudo apparmor_parser -Q {SOURCE}",
        "sudo install -d -m 0755 -- /var/lib/mx-worker-bootstrap", f"sudo tee {PENDING}",
        f"sudo install -m 0644 -- {SOURCE} {TARGET}", f"sudo apparmor_parser -r {TARGET}",
        f"sudo mv -f -- {PENDING} {MARKER}"]
    assert "PASS: the Worker sandbox starts" in result.out and "restriction: 1 (unchanged)" in result.out
    assert "Configured profile: installed by this script (receipt matches)" in host.run("--check").out


def test_repeated_apply_and_rollback_are_safe(host: FakeHost) -> None:
    install_ok(host)
    before = host.snapshot()
    again = host.run("--apply")
    assert again.code == 0 and "Already working" in again.out and not again.sudo() and host.snapshot() == before
    first = host.run("--rollback")
    assert first.code == 0 and "Rolled back script-installed profile (installed)" in first.out
    assert not host.path(TARGET).exists() and not host.path(MARKER).exists() and not host.loaded()
    second = host.run("--rollback")
    assert second.code == 0 and "Nothing to roll back" in second.out and not second.sudo()


@pytest.mark.parametrize("conflict, message, listed", [
    ("file", "Potential conflicting profile files detected:", "/etc/apparmor.d/flatpak-bwrap"),
    ("override", "Local overrides would change the distribution policy:", "/etc/apparmor.d/local/unpriv_bwrap"),
    ("loaded", "Potential conflicting loaded profiles detected:", "bwrap"),
    ("target", "already exists. Refusing to overwrite.", TARGET),
])
def test_apply_refuses_conflicting_policy(host: FakeHost, conflict: str, message: str, listed: str) -> None:
    if conflict == "file":
        (host.etc / "flatpak-bwrap").write_text("profile flatpak-bwrap /usr/bin/bwrap {\n  userns,\n}\n",
                                                encoding="utf-8")
    elif conflict == "override":
        (host.etc / "local" / "unpriv_bwrap").write_text("# allow more\n  capability sys_admin,\n", encoding="utf-8")
    elif conflict == "loaded":
        host.state(foreign="bwrap\n")
    else:
        host.path(TARGET).write_text("profile vendor_bwrap /usr/bin/bwrap {}\n", encoding="utf-8")
    before = host.snapshot()
    result = host.run("--apply")
    assert result.code == 1 and message in result.err and listed in result.err, result.text
    assert host.snapshot() == before  # nothing installed, nothing touched
    assert not any(c.startswith(("sudo install", "sudo tee", "sudo apparmor_parser -r")) for c in result.sudo())


def test_empty_or_comment_only_local_files_are_not_overrides(host: FakeHost) -> None:
    (host.etc / "local" / "bwrap-userns-restrict").write_text("# Site-specific additions\n\n", encoding="utf-8")
    install_ok(host)


@pytest.mark.parametrize("failure, message", [
    ("load", "The parser rejected the profile."),
    ("still-broken", "Profile loaded, but the Worker sandbox still fails: bwrap: setting up uid map"),
    ("copy", "Could not install /etc/apparmor.d/bwrap-userns-restrict."),
])
def test_a_failed_installation_is_undone(host: FakeHost, failure: str, message: str) -> None:
    host.state(bwrap="works-when-loaded")
    if failure == "load":
        host.state(load="fail")
    elif failure == "still-broken":
        host.state(bwrap="broken")
    else:
        host.state(sudo_fail="install -m 0644")
    before = host.snapshot()
    result = host.run("--apply")
    assert result.code == 1 and f"ERROR: {message}" in result.err, result.text
    assert "Partial installation undone; the host is as before." in result.err
    assert not host.loaded() and host.snapshot() == before


@pytest.mark.parametrize("failure, message", [
    ("syntax", "Profile syntax invalid; no changes made."),
    ("sudo", "sudo authentication failed; nothing changed."),
])
def test_failures_before_any_change_change_nothing(host: FakeHost, failure: str, message: str) -> None:
    host.state(**({"parse": "fail"} if failure == "syntax" else {"sudo": "denied"}))
    before = host.snapshot()
    result = host.run("--apply")
    assert result.code == 1 and message in result.err and host.snapshot() == before
    assert not any(c.startswith(("sudo install", "sudo tee")) for c in result.sudo())


def test_an_undo_that_fails_keeps_the_pending_receipt_for_rollback(host: FakeHost) -> None:
    host.state(bwrap="works-when-loaded", load="fail", sudo_fail=f"rm -f -- {TARGET}")
    result = host.run("--apply")
    assert result.code == 1 and "Could not fully undo it. The receipt" in result.err and "--rollback" in result.err
    assert host.path(TARGET).exists() and host.path(PENDING).exists() and not host.path(MARKER).exists()
    blocked = host.run("--apply")
    assert blocked.code == 1 and "did not finish" in blocked.err  # apply will not run over it
    assert "an installation by this script did not finish" in host.run("--check").out
    host.state(sudo_fail=None)
    rolled = host.run("--rollback")
    assert rolled.code == 0 and "(pending)" in rolled.out
    assert not host.path(TARGET).exists() and not host.path(PENDING).exists()


# -- --rollback ---------------------------------------------------------------------------------

def test_rollback_refuses_a_profile_it_did_not_install(host: FakeHost) -> None:
    host.path(TARGET).write_text(PROFILE, encoding="utf-8")
    before = host.snapshot()
    result = host.run("--rollback")
    assert result.code == 1 and "No script-owned installation receipt" in result.err
    assert host.snapshot() == before and not result.sudo()


def test_rollback_refuses_a_profile_changed_since_installation(host: FakeHost) -> None:
    install_ok(host)
    host.path(TARGET).write_text(PROFILE + "# edited\n", encoding="utf-8")
    before = host.snapshot()
    result = host.run("--rollback")
    assert result.code == 1 and "changed since this script installed it. Refusing rollback." in result.err
    assert host.snapshot() == before and host.loaded() == {"bwrap", "unpriv_bwrap"}


def test_rollback_keeps_everything_when_the_unload_fails(host: FakeHost) -> None:
    install_ok(host)
    host.state(unload="fail")
    result = host.run("--rollback")
    assert result.code == 1 and "Could not unload profile; nothing removed." in result.err
    assert host.path(TARGET).exists() and host.path(MARKER).exists()


def test_rollback_accepts_the_receipt_of_the_earlier_script_version(host: FakeHost) -> None:
    """Hosts set up before this revision have the same receipt: one sha256 line in *.sha256."""
    host.path(TARGET).write_text(PROFILE, encoding="utf-8")
    host.path(MARKER).parent.mkdir()
    host.path(MARKER).write_text(hashlib.sha256(PROFILE.encode()).hexdigest() + "\n", encoding="utf-8")
    host.state(loaded="bwrap\nunpriv_bwrap\n")
    result = host.run("--rollback")
    assert result.code == 0 and not host.path(TARGET).exists() and not host.path(MARKER).exists()
    assert not host.loaded()


def test_rollback_after_the_file_was_removed_unloads_from_the_identical_source(host: FakeHost) -> None:
    install_ok(host)
    host.path(TARGET).unlink()
    result = host.run("--rollback")
    assert result.code == 0 and f"sudo apparmor_parser -R {SOURCE}" in result.sudo()
    assert not host.loaded() and not host.path(MARKER).exists()


def test_rollback_of_a_pending_installation_that_never_copied(host: FakeHost) -> None:
    host.path(PENDING).parent.mkdir()
    host.path(PENDING).write_text(hashlib.sha256(PROFILE.encode()).hexdigest() + "\n", encoding="utf-8")
    result = host.run("--rollback")
    assert result.code == 0 and "already gone" in result.out and not host.path(PENDING).exists()


def test_rollback_refuses_a_malformed_receipt(host: FakeHost) -> None:
    install_ok(host)
    host.path(MARKER).write_text("not-a-hash\n", encoding="utf-8")
    before = host.snapshot()
    result = host.run("--rollback")
    assert result.code == 1 and "unreadable or malformed" in result.err and host.snapshot() == before


# -- the script text itself ---------------------------------------------------------------------

def test_the_script_never_weakens_apparmor() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())  # comments only, not ${#x}
    for forbidden in (r"sysctl\s+(-w|--write)", r"/proc/sys/", r"\baa-(complain|disable|audit)\b",
                      r"apparmor_parser\s+-C", r"--complain", r"flags=\([^)]*(complain|unconfined)",
                      r"\bsystemctl\b", r"\bapt(-get)?\b", r"\b(curl|wget)\b"):
        assert not re.search(forbidden, code), forbidden
    assert "${#found[@]}" in code  # the comment filter keeps code that contains '#'
    assert shutil.which("bash") and subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
