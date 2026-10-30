# Worker Host Setup — Ubuntu 24.04 / Bubblewrap / AppArmor

**Applies to:** Linux worker hosts using the `mx-admin-cloud` Feature Coordinator's sandboxed validation gates.  
**Status:** bootstrap procedure. The first version of the script installed the profile on the first host (2026-10-23) before the W4.2 real-agent smoke test. This revision is covered by offline tests on a fake host (`scripts/tests/test_setup_bwrap_apparmor.py`) and was run read-only (`--check`) on that host; its `--apply` and `--rollback` have not yet run on a real host.  
**Owner:** host operator; requires explicit approval for `sudo` actions.  
**Related:** [worker-autonomy.md](worker-autonomy.md) (the Worker service; §16 limitations) · [worker-functional.md](worker-functional.md) (execution backends) · [agent-ownership.md](agent-ownership.md) (sandbox layers; §5 residual risks) · [operations.md](operations.md) (`sandbox-check`)

## Why this is necessary

Ubuntu 24.04 commonly enables `kernel.apparmor_restrict_unprivileged_userns=1`. If `/usr/bin/bwrap` has no suitable AppArmor policy, a sandboxed validation gate fails before its test command starts: an unconfined process that creates a user namespace is moved to the `unprivileged_userns` profile, and bubblewrap is then denied capabilities such as `setpcap` ("setting up uid map: Permission denied"). A **blocked** gate is an environment problem, not evidence that AI-generated code failed validation.

The W4.2.1 Worker fix reports such gates as `gates could not run: … (cause)`, classified **critical** (needs operator attention). It never spends additional model sessions to fix source code when a sandbox cannot start.

## Supported systems

| System | Status |
| --- | --- |
| **Ubuntu 24.04 LTS** with the distro `bubblewrap` (`/usr/bin/bwrap`) and `kernel.apparmor_restrict_unprivileged_userns=1` | Supported. The script installs the distribution's own `bwrap-userns-restrict` profile |
| Ubuntu 24.04 with the restriction off (`=0`) | Not supported by the script (it refuses). Bubblewrap needs no profile there; review why the restriction is off |
| Other Ubuntu releases | Not supported by the script. Newer releases may already ship and enable the profile in `/etc/apparmor.d/`: **do not copy over it**; verify manually with the commands under [Verification commands](#verification-commands) |
| Other distributions, containers without user namespaces, WSL | Not supported |

Verified versions on the first host: AppArmor `4.0.1really4.0.1-0ubuntu0.24.04.9`, `apparmor-profiles` (same), `bubblewrap 0.9.0-1ubuntu0.3`.

## Security rules

1. **Never** disable `kernel.apparmor_restrict_unprivileged_userns` globally. The script reads it and refuses to run unless it is `1`; it never writes it.
2. Prefer the **distribution-packaged** `bwrap-userns-restrict` policy for `/usr/bin/bwrap` instead of an unconfined profile or an untrusted download. The script verifies that an installed package owns the file and that it matches the package's recorded checksum.
3. **Do not** overwrite another vendor's AppArmor profile for `bwrap`. This can break GNOME, Flatpak and other programs that use Bubblewrap.
4. **Do not** extend the profile with local overrides (`/etc/apparmor.d/local/bwrap-userns-restrict`, `/etc/apparmor.d/local/unpriv_bwrap`). The script refuses to install while either contains rules.
5. Test `bwrap` as the **normal login user** from the **same shell / AppArmor context** that will run the Worker. Testing with `sudo` is not valid. The script refuses to run as root and prints this shell's AppArmor context.
6. Do not run real AI sessions until the **actual Worker sandbox check** (`sandbox-check --apply`) succeeds in the intended execution context.
7. Every change to `/etc/apparmor.d/` requires operator approval; no automated changes during routine Worker runtime.
8. Never switch AppArmor to complain mode, unload unrelated profiles or mark anything unconfined to make a test pass.

## Installation prerequisites

Install through Ubuntu APT (an explicit administrative step; the script never installs packages):

```bash
sudo apt update
sudo apt install bubblewrap apparmor apparmor-profiles apparmor-utils
```

- `bubblewrap` provides `/usr/bin/bwrap`.
- `apparmor-profiles` provides the distribution profile at `/usr/share/apparmor/extra-profiles/bwrap-userns-restrict`.
- `apparmor` provides `apparmor_parser` and `aa-status`; `apparmor-utils` is useful for inspection but not sufficient by itself.
- `sudo` rights for the operator (the script asks for the password once, before it changes anything).

Copy `scripts/setup/setup-bwrap-apparmor.sh` to the worker host (or use the checkout) and inspect it before running.

## Setup commands

**1. Read-only diagnostic** (no sudo, no changes):

```bash
bash scripts/setup/setup-bwrap-apparmor.sh --check
```

It reports:
- the AppArmor state and this shell's AppArmor context (a warning if the shell is confined: the result applies only to that context);
- whether the distribution profile is present and verified;
- the configured profile and whether this script installed it;
- local overrides, other candidate profile files, and the loaded profiles (an unprivileged `aa-status` cannot read them; use `sudo aa-status | grep -i bwrap`);
- the **Worker sandbox preflight**, and, for information only, whether a nested bubblewrap can start.

The preflight runs `/usr/bin/bwrap` unprivileged with the namespaces and mounts of the coordinator's sandbox (`sandbox.py`): `--unshare-all` (user, PID, network, IPC, UTS, cgroup), `--die-with-parent`, `--new-session`, a read-only `/usr` and `/etc`, `/proc`, `/dev`, tmpfs `/tmp` and `/var/tmp`, a writable bind (like a role root), `--remount-ro /` and a cleared environment.

Exit codes for all modes: `0` success or PASS; `1` unsupported host, refused, or another precondition failure; `2` (`--check`) the Worker sandbox preflight failed; `64` usage error.

**2. Explicit installation** (only after review; uses `sudo` for the listed steps only):

```bash
bash scripts/setup/setup-bwrap-apparmor.sh --apply
```

The script:
- Checks Ubuntu 24.04, the distro `/usr/bin/bwrap`, the global restriction (`1`) and that AppArmor is enabled.
- Runs the Worker sandbox preflight without root and **exits without changes when it already works** (safe to repeat).
- Refuses when an installation of its own did not finish (`--rollback` first), or when `/etc/apparmor.d/bwrap-userns-restrict` already exists.
- Verifies the distribution profile: owned by an installed package and equal to the package's recorded checksum.
- Refuses local overrides, conflicting profile files, and loaded profiles named like `bwrap` (read with `sudo aa-status`; running sandboxes listed among processes are not profiles and are ignored).
- Authenticates with `sudo -v` and parses the profile (`apparmor_parser -Q`) before changing anything.
- Writes a **pending receipt** (`/var/lib/mx-worker-bootstrap/bwrap-userns-restrict.pending`, the profile's SHA-256), copies the profile to `/etc/apparmor.d/`, checks the copy, loads it (`apparmor_parser -r`) and re-runs the preflight.
- On success, renames the pending receipt to `bwrap-userns-restrict.sha256` (the installation receipt).
- **On any failure after the first change** (copy, parse, load, failed preflight, Ctrl-C), it undoes its own steps: it unloads and removes the profile it installed, if the file is unchanged, and deletes the pending receipt. If the undo itself fails, the pending receipt is kept so that `--rollback` can finish it.
- Never changes sysctl, AppArmor modes or service startup, user accounts, linger, or the systemd Worker configuration.

The only privileged commands the script runs are: `sudo -v`, `sudo aa-status`, `sudo apparmor_parser -Q|-r|-R` on this profile, `sudo install` (the profile and the receipt directory), `sudo tee` (the pending receipt), `sudo mv` (pending → installed receipt) and `sudo rm -f` (only the profile and its receipts).

## Verification commands

In the **actual Worker shell** (the same terminal or service context):

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns          # must stay 1
bash scripts/setup/setup-bwrap-apparmor.sh --check           # PASS, exit 0
sudo aa-status | grep -E '^\s+(bwrap|unpriv_bwrap)$'         # both loaded in enforce mode
python -m feature_coordinator sandbox-check --apply              # the definitive test (project venv)
```

`sandbox-check --apply` runs the coordinator's own containment self-test in a disposable repository, inside every role's real sandbox, with a small Python probe (no AI model). A passing standalone `bwrap` smoke test **does not guarantee** every gate configuration is valid: use the project check, not an AI run, as the definitive environment test.

## Nested sandbox limitation

`bwrap-userns-restrict` runs everything that bubblewrap starts under the `unpriv_bwrap` profile, with **every capability denied**. Inside the coordinator's sandbox, processes report the AppArmor label `bwrap//&unpriv_bwrap (enforce)`, and a second bubblewrap started there cannot create namespaces ("No permissions to create new namespace"; kernel audit `apparmor="DENIED" … profile="unpriv_bwrap" … capname="sys_admin"`).

- **Affected:** the optional `runtime.execution_backend: bwrap` with **Codex**. Codex starts its own bubblewrap sandbox for the commands it runs, which would be nested inside the Frontend sandbox. Under `codex sandbox`, the command did not run at all.
- **Not affected:** the default `native-cli` backend, which runs agents outside the coordinator's sandbox, and the validation gates, which are top-level sandboxes.
- **Tests:** `test_fc_launchers.py::test_codex_own_sandbox_works_nested_inside_ours` is skipped with an explicit reason, but only when a probe in the same Frontend sandbox shows both the `unpriv_bwrap` label and the namespace refusal ([agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status), item 6).
- **What not to do:** do not disable AppArmor or the restriction, add a local override or switch to complain mode to make nested sandboxes work. Use `native-cli`, or a host without that profile, until the `bwrap` backend has a Codex mode that does not nest bubblewrap (an open decision).

`--check` reports the nested result for information; it does not change the exit code.

## Compatibility with Worker execution backends

| Component | Needs bubblewrap | With `bwrap-userns-restrict` installed |
| --- | --- | --- |
| Validation gates (always) | Yes, top-level | **Work** (this procedure is what makes them work) |
| `native-cli` backend (default; W2/W4) | No for the agents (they run directly with the subscription logins) | **Works**. Codex's own command sandbox under `native-cli` starts at top level from an unconfined terminal and is expected to work, but was not exercised by a real agent (in W4.2 Codex ran no shell command) |
| `bwrap` backend, Claude Code | Yes, top-level per role | Works (no nested sandbox) |
| `bwrap` backend, Codex | Yes, plus Codex's own nested bubblewrap | **Does not work** (nested sandbox denied, above) |
| Other software on the host (Flatpak, GNOME helpers, Chromium-based sandboxes) | Often | Programs started by `/usr/bin/bwrap` run as `unpriv_bwrap`, with all capabilities denied inside their sandbox; one that needs capabilities there (for example its own nested sandbox) can break. Not verified here: check the ones you use after installing (checklist below) |

## Post-reboot verification

The profile lives in `/etc/apparmor.d/`, so `apparmor.service` loads it again at boot; the receipt in `/var/lib/mx-worker-bootstrap/` persists. After a reboot, or after an `apparmor`, `apparmor-profiles` or `bubblewrap` package update:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns          # still 1
bash scripts/setup/setup-bwrap-apparmor.sh --check           # "installed by this script (receipt matches)" and PASS
sudo aa-status | grep -E '^\s+(bwrap|unpriv_bwrap)$'
python -m feature_coordinator sandbox-check --apply
```

If an `apparmor-profiles` update changed the distribution file, `--check` still reports the installed copy as matching its receipt (the copy in `/etc/apparmor.d/` is not updated by the package). To move to the new version: `--rollback`, then `--apply`.

## Diagnosing failure

If the preflight fails or a gate exits blocked (exit 125, `gates could not run: …`):

```bash
command -v bwrap
readlink -f "$(command -v bwrap)"
sysctl kernel.apparmor_restrict_unprivileged_userns
cat /proc/self/attr/current                                  # this shell's AppArmor context
sudo aa-status | grep -Ei 'bwrap|unpriv_bwrap' || true
sudo journalctl -k -b --no-pager | grep -Ei 'apparmor|userns|bwrap' | tail -50
```

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `bwrap: setting up uid map: Permission denied`; kernel `DENIED … profile="unprivileged_userns" … setpcap` | No bubblewrap profile: unprivileged user namespaces restricted | `--apply` |
| `--check` passes in one terminal but gates are blocked in another | Different AppArmor contexts (e.g. an IDE terminal under a snap profile in complain mode) | Run `--check` and `sandbox-check --apply` from the context that runs the Worker |
| `--apply`: "already exists. Refusing to overwrite." | A vendor or operator profile | Inspect it manually; do not overwrite it |
| `--apply`: "Potential conflicting …" or "Local overrides …" | Another bwrap policy, or local rules | Review manually; nothing was changed |
| `--apply`: "did not finish" | An earlier installation was interrupted, and its undo failed | Review, then `--rollback` |
| `--apply`: "Profile loaded, but the Worker sandbox still fails" | Something else blocks bubblewrap | Read the error and the kernel log; the script has already undone its own installation |
| "Nested bubblewrap: denied" | Expected with this profile | See [Nested sandbox limitation](#nested-sandbox-limitation) |

Check for conflicts from existing profiles, `bwrap` packaging, and terminal-dependent AppArmor confinement. Do not silently switch to an AppArmor `complain` environment or disable the restriction to make tests pass.

If the distro-provided profile source is missing or fails verification, inspect the package and distribution version before proceeding. Do not fetch an arbitrary internet profile in an automated installer.

## Rollback

Only changes owned by this script are reverted:

```bash
bash scripts/setup/setup-bwrap-apparmor.sh --rollback
```

- It requires a receipt: the installation receipt (`*.sha256`, also written by the earlier version of the script) or a pending one (`*.pending`, an unfinished installation).
- It refuses when `/etc/apparmor.d/bwrap-userns-restrict` exists without a receipt (not installed by this script), is not a regular file, or **differs from the hash in the receipt**.
- It unloads the profiles only if they are loaded (`sudo aa-status`), then removes the profile and the receipt. If the unload fails, nothing is removed.
- If the profile file was already removed but its profiles are still loaded, it unloads them using the identical distribution file, then removes the receipt.
- With no receipt and no profile, it reports "Nothing to roll back" and exits 0, so it is safe to repeat.
- It does **not** modify the global restriction, other profiles or packages. The empty `/var/lib/mx-worker-bootstrap/` directory is left in place.

After a rollback, gates are blocked again on a restricted host (expected).

## Validation checklist for each worker host

- [ ] Ubuntu release and `/usr/bin/bwrap` verified.
- [ ] `kernel.apparmor_restrict_unprivileged_userns=1` remains in force.
- [ ] No conflicting vendor or distro AppArmor bwrap profiles, and no local overrides.
- [ ] `--check` passes in the intended runtime context (no confinement warning, or a deliberate one).
- [ ] `python -m feature_coordinator sandbox-check --apply` passes in that same context.
- [ ] Worker still treats sandbox setup failures as **critical** "gates could not run", not as validation errors.
- [ ] Other bubblewrap users on the host still work (for example the Flatpak apps and GNOME features in use).
- [ ] The execution backend is `native-cli`, or, with `bwrap`, no role uses Codex (nested sandbox limitation).
- [ ] Post-reboot verification done once.
- [ ] No real model calls were made during setup.
- [ ] Changes, package versions and rollback instructions recorded in the host inventory.

## Deployment scope

This is a **per-machine** prerequisite, not part of the Coordinator's task scheduler or the independent MX Monitor Tool. The Monitor may display sandbox preflight state but must not install AppArmor policies or modify Worker hosts.

### References

- Ubuntu's explanation of user namespace restrictions: https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007
- Ubuntu AppArmor documentation: https://ubuntu.com/server/docs/how-to/security/apparmor/
- AppArmor upstream `bwrap-userns-restrict`: https://gitlab.com/apparmor/apparmor/-/blob/master/profiles/apparmor/profiles/extras/bwrap-userns-restrict
- Existing-profile conflict discussion: https://github.com/openai/codex/issues/38715
