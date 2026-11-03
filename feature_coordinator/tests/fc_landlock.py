"""Run a command under a Landlock domain that denies every TCP connect except to given ports.

The native-hardening tests drive the REAL Claude Code / Codex CLIs. Those must run top-level (their
own sandboxes cannot start inside our bubblewrap on hosts with Ubuntu's bwrap-userns-restrict
AppArmor policy), so their network is limited here instead: Landlock (ABI >= 4) needs neither
privileges nor a namespace and leaves a top-level bubblewrap working. The fake provider's port on
127.0.0.1 is the only TCP destination; nothing else can be reached. UDP (DNS) is not covered by
Landlock, so the tests use only `.invalid` names. File access is not restricted.

    python3 -I fc_landlock.py PORT[,PORT...] -- command args...     (no ports: no TCP connect at all)
"""

from __future__ import annotations

import ctypes
import os
import sys

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446  # x86_64 and aarch64
ACCESS_NET_CONNECT_TCP = 1 << 1
RULE_NET_PORT = 2
PR_SET_NO_NEW_PRIVS = 38
CREATE_RULESET_VERSION = 1


class _RulesetAttr(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class _NetPortAttr(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def abi_version() -> int:
    """The kernel's Landlock ABI version (<= 0: unavailable)."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = ctypes.CDLL(None, use_errno=True)
    return int(libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION)))


def supported() -> bool:
    """True when TCP connect rules (Landlock ABI 4) are available."""
    try:
        return abi_version() >= 4
    except OSError:
        return False


def command(ports: list[int], argv: list[str]) -> list[str]:
    """*argv* wrapped so that it runs under the restriction."""
    return [sys.executable, "-I", os.path.abspath(__file__), ",".join(map(str, ports)), "--", *argv]


def _restrict(ports: list[int]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    attr = _RulesetAttr(0, ACCESS_NET_CONNECT_TCP, 0)
    fd = libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)),
                      ctypes.c_uint32(0))
    if fd < 0:
        raise SystemExit(f"landlock_create_ruleset: {os.strerror(ctypes.get_errno())}")
    for port in ports:
        rule = _NetPortAttr(ACCESS_NET_CONNECT_TCP, port)
        if libc.syscall(SYS_ADD_RULE, fd, RULE_NET_PORT, ctypes.byref(rule), ctypes.c_uint32(0)) != 0:
            raise SystemExit(f"landlock_add_rule: {os.strerror(ctypes.get_errno())}")
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 or libc.syscall(SYS_RESTRICT_SELF, fd, ctypes.c_uint32(0)):
        raise SystemExit(f"landlock_restrict_self: {os.strerror(ctypes.get_errno())}")
    os.close(fd)


def main() -> None:
    """`fc_landlock.py PORTS -- argv...`: restrict this process, then exec *argv*."""
    split = sys.argv.index("--")
    ports = [int(p) for p in sys.argv[1].split(",") if p] if split == 2 else []
    _restrict(ports)
    os.execvp(sys.argv[split + 1], sys.argv[split + 1:])


if __name__ == "__main__":
    main()
