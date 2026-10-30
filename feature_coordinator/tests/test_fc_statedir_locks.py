"""State directory suitability, flock behavior across processes and process identity."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from feature_coordinator.errors import LockBusyError, StateDirError
from feature_coordinator.locks import FileLock, ProcessIdentity
from feature_coordinator.statedir import (
    check_filesystem,
    check_mount,
    ensure_state_dir,
    find_mount,
    flock_self_test,
    mount_for,
    parse_mountinfo,
    resolve_state_dir,
)

from fc_helpers import Holder, run_py

MOUNTINFO = """\
22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme1n1p2 rw
30 22 259:3 / /media/data rw,nosuid shared:2 - fuseblk /dev/nvme0n1p2 rw,user_id=0
31 22 0:40 / /mnt/nfs rw shared:3 - nfs4 server:/export rw
32 22 0:41 / /mnt/sshfs rw shared:4 - fuse.sshfs host:/ rw
33 22 0:42 / /mnt/odd rw shared:5 - vfat /dev/sdb1 rw
34 22 0:43 / /mnt/with\\040space rw shared:6 - xfs /dev/sdc1 rw
"""


def test_xdg_state_home_resolution(tmp_path: Path) -> None:
    assert resolve_state_dir({"XDG_STATE_HOME": str(tmp_path), "HOME": "/home/u"}) == tmp_path / "mx-admin-agents"
    assert resolve_state_dir({"HOME": "/home/u"}) == Path("/home/u/.local/state/mx-admin-agents")
    # The XDG spec says relative values must be ignored.
    assert resolve_state_dir({"XDG_STATE_HOME": "relative/dir", "HOME": "/home/u"}) == \
        Path("/home/u/.local/state/mx-admin-agents")


MOUNTS = parse_mountinfo(MOUNTINFO)


def simulated(path: str):
    return check_mount(find_mount(path, MOUNTS), Path(path))


def test_mountinfo_parsing_and_longest_prefix() -> None:
    assert MOUNTS[5].mount_point == "/mnt/with space"
    assert find_mount("/media/data/x/y", MOUNTS).fs_type == "fuseblk"
    assert find_mount("/media/database", MOUNTS).fs_type == "ext4"  # string prefix is not a path prefix


@pytest.mark.parametrize("path,fragment", [
    ("/media/data/state", "FUSE and network"),
    ("/mnt/nfs/state", "FUSE and network"),
    ("/mnt/sshfs/state", "FUSE and network"),
    ("/mnt/odd/state", "deny-by-default"),
])
def test_unsupported_filesystems_are_rejected(path: str, fragment: str) -> None:
    with pytest.raises(StateDirError, match=fragment):
        simulated(path)


def test_local_filesystems_are_accepted() -> None:
    assert simulated("/home/u/.local/state/x").fs_type == "ext4"
    assert simulated("/mnt/with space/s").fs_type == "xfs"


def test_the_repository_checkout_would_be_rejected_as_state_location() -> None:
    """The real checkout is on fuseblk on this host; state must never live there (D-12)."""
    repo_root = Path(__file__).resolve().parents[2]
    mount = mount_for(repo_root)
    if not mount.fs_type.startswith("fuse"):
        pytest.skip(f"checkout is on {mount.fs_type} on this host")
    with pytest.raises(StateDirError, match="FUSE"):
        check_filesystem(repo_root / ".agent-state")


def test_this_hosts_default_state_location_is_suitable() -> None:
    """The real D-12 location (resolved, not created) sits on an accepted local filesystem."""
    real = resolve_state_dir({"HOME": os.path.expanduser("~")})
    assert check_filesystem(real).fs_type in ("ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "tmpfs")


def test_ensure_state_dir_creates_a_private_directory(tmp_path: Path) -> None:
    state = ensure_state_dir(tmp_path / "s" / "mx-admin-agents")
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert all((state / sub).is_dir() for sub in ("features", "tasks", "claims", "locks", "tmp"))


def test_ensure_state_dir_rejects_group_writable_and_symlinks(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o777)
    os.chmod(loose, 0o777)
    with pytest.raises(StateDirError, match="group/other writable"):
        ensure_state_dir(loose)
    link = tmp_path / "link"
    os.symlink(tmp_path, link)
    with pytest.raises(StateDirError, match="symlink"):
        ensure_state_dir(link)


def test_flock_self_test_passes_on_a_local_filesystem(state_dir: Path) -> None:
    flock_self_test(state_dir)


def test_exclusive_and_shared_semantics(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    first = FileLock(path).acquire()
    with pytest.raises(LockBusyError):
        FileLock(path).acquire()
    first.release()
    a, b = FileLock(path, shared=True).acquire(), FileLock(path, shared=True).acquire()
    with pytest.raises(LockBusyError):
        FileLock(path).acquire(timeout=0.1)
    a.release()
    b.release()
    FileLock(path).acquire().release()


def test_lock_held_by_another_process_and_released_when_it_crashes(tmp_path: Path, holders: list[Holder]) -> None:
    path = tmp_path / "role-backend.lock"
    holder = Holder("import sys\nfrom pathlib import Path\nfrom feature_coordinator.locks import FileLock\n"
                    "lock = FileLock(Path(sys.argv[1])).acquire()", str(path))
    holders.append(holder)
    with pytest.raises(LockBusyError):
        FileLock(path).acquire()
    holder.kill()  # SIGKILL: no cleanup code runs, the kernel drops the flock
    FileLock(path).acquire().release()


def test_process_identity_detects_death_reuse_and_reboot(holders: list[Holder]) -> None:
    me = ProcessIdentity.current()
    assert me.is_alive()
    holder = Holder("pass")
    holders.append(holder)
    other = ProcessIdentity.of_pid(holder.pid)
    assert other is not None and other.is_alive()
    holder.kill()
    assert not other.is_alive()
    assert not ProcessIdentity(me.pid, me.start_ticks + 1, me.boot_id).is_alive()  # recycled PID
    assert not ProcessIdentity(me.pid, me.start_ticks, "another-boot").is_alive()
    assert ProcessIdentity.from_dict(me.to_dict()) == me


def test_zombie_counts_as_dead() -> None:
    result = run_py("""
        import os, time
        from feature_coordinator.locks import ProcessIdentity
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        ident = ProcessIdentity.of_pid(pid)
        time.sleep(0.3)  # child exited but is not reaped yet: a zombie
        print(ident is not None and not ident.is_alive())
        os.waitpid(pid, 0)
    """)
    assert result.stdout.strip() == "True", result.stderr
