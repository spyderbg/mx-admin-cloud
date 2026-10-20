"""Runtime state directory (D-12): `$XDG_STATE_HOME/mx-admin-agents/`, else
`~/.local/state/mx-admin-agents/` — outside Git, private to the operator.

Before anything relies on `flock`, the directory is checked to be on a local native
filesystem (deny-by-default allowlist; FUSE and network filesystems are rejected — the
repository itself sits on a `fuseblk` mount, which is exactly why state does not live
there), owned by the current user, not group/other writable, and not a symlink. The
`flock_self_test()` then proves that two processes really exclude each other.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import StateDirError

APP_DIR = "mx-admin-agents"
NETWORK_OR_FUSE_PREFIXES = ("fuse", "nfs", "cifs", "smb", "9p", "afs", "ceph", "glusterfs", "lustre", "sshfs", "davfs")
DEFAULT_ALLOWED_FILESYSTEMS = ("ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "tmpfs")


def resolve_state_dir(env: Mapping[str, str] | None = None, *, app_dir: str = APP_DIR) -> Path:
    """`$XDG_STATE_HOME/<app_dir>`; per the XDG spec a relative or empty value is ignored."""
    env = os.environ if env is None else env
    xdg = (env.get("XDG_STATE_HOME") or "").strip()
    if xdg and os.path.isabs(xdg):
        base = Path(xdg)
    else:
        home = (env.get("HOME") or "").strip() or os.path.expanduser("~")
        base = Path(home) / ".local" / "state"
    return base / app_dir


@dataclass(frozen=True)
class MountInfo:
    """The mount a path lives on."""

    mount_point: str
    fs_type: str
    source: str


def _unescape(field: str) -> str:
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def parse_mountinfo(text: str) -> list[MountInfo]:
    """Parse /proc/self/mountinfo (see proc(5))."""
    mounts = []
    for line in text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        left_fields, right_fields = left.split(), right.split()
        if len(left_fields) < 5 or len(right_fields) < 2:
            continue
        mounts.append(MountInfo(_unescape(left_fields[4]), right_fields[0], right_fields[1]))
    return mounts


def find_mount(real_path: str, mounts: list[MountInfo]) -> MountInfo:
    """The mount covering the absolute, symlink-free *real_path* (longest mount-point prefix)."""
    best: MountInfo | None = None
    for mount in mounts:
        mp = mount.mount_point
        covers = mp == "/" or real_path == mp or real_path.startswith(mp + "/")
        # ">=" so that a later mount on the same mount point (which shadows earlier ones) wins.
        if covers and (best is None or len(mp) >= len(best.mount_point)):
            best = mount
    if best is None:
        raise StateDirError(f"cannot determine the filesystem of {real_path}")
    return best


def mount_for(path: Path, mountinfo_text: str | None = None) -> MountInfo:
    """The mount containing *path*, or its nearest existing ancestor, on this host."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if mountinfo_text is None:
        mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    return find_mount(str(probe.resolve()), parse_mountinfo(mountinfo_text))


def check_filesystem(path: Path, *, allowed: tuple[str, ...] = DEFAULT_ALLOWED_FILESYSTEMS,
                     mountinfo_text: str | None = None) -> MountInfo:
    """Raise StateDirError unless *path* is on an allowed local filesystem."""
    return check_mount(mount_for(path, mountinfo_text), path, allowed=allowed)


def check_mount(mount: MountInfo, path: Path, *, allowed: tuple[str, ...] = DEFAULT_ALLOWED_FILESYSTEMS) -> MountInfo:
    """Raise StateDirError unless *mount* is an allowed local filesystem for *path*."""
    fs_type = mount.fs_type.lower()
    if fs_type.startswith(NETWORK_OR_FUSE_PREFIXES):
        raise StateDirError(
            f"state directory {path} is on {mount.fs_type} ({mount.mount_point}); FUSE and network "
            "filesystems are rejected because flock semantics are not reliable there",
        )
    if fs_type not in allowed:
        raise StateDirError(
            f"state directory {path} is on {mount.fs_type}, which is not in the allowed local "
            f"filesystems {list(allowed)} (deny-by-default)",
        )
    return mount


def ensure_state_dir(path: Path, *, allowed: tuple[str, ...] = DEFAULT_ALLOWED_FILESYSTEMS,
                     mountinfo_text: str | None = None) -> Path:
    """Check the filesystem, create *path* with mode 0700 and verify ownership and mode."""
    check_filesystem(path, allowed=allowed, mountinfo_text=mountinfo_text)
    if path.is_symlink():
        raise StateDirError(f"state directory {path} must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise StateDirError(f"state directory {path} is not a directory")
    if info.st_uid != os.getuid():
        raise StateDirError(f"state directory {path} is not owned by the current user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StateDirError(f"state directory {path} must not be group/other writable")
    for sub in ("features", "tasks", "claims", "locks", "tmp"):
        (path / sub).mkdir(mode=0o700, exist_ok=True)
    return path


_CHILD_PROBE = textwrap.dedent("""
    import fcntl, sys
    fd = open(sys.argv[1], "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(3)
    sys.exit(0)
""")


def flock_self_test(state_dir: Path) -> None:
    """Prove that flock excludes a second process on this filesystem. Raises StateDirError."""
    probe = state_dir / "locks" / ".flock-self-test.lock"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with open(probe, "a+", encoding="utf-8") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        blocked = subprocess.run([sys.executable, "-c", _CHILD_PROBE, str(probe)], check=False, timeout=30)
        fcntl.flock(holder, fcntl.LOCK_UN)
    if blocked.returncode != 3:
        raise StateDirError(f"flock did not exclude a second process on {state_dir} (child rc={blocked.returncode})")
    released = subprocess.run([sys.executable, "-c", _CHILD_PROBE, str(probe)], check=False, timeout=30)
    if released.returncode != 0:
        raise StateDirError(f"flock was not released for a second process on {state_dir}")
    probe.unlink(missing_ok=True)
