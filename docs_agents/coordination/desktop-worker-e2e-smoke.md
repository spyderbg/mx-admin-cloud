# Desktop Worker End-to-End Smoke Test

This smoke test verifies the first supervised, real desktop Worker run.

## Intended workflow

1. A GitHub Issue is approved by a human operator.
2. The desktop Worker (run as a systemd user service) picks up the approved Issue.
3. The Worker coordinator runs the configured `docs-check` gate.
4. The coordinator publishes a Draft PR.
5. A human reviews and merges. Merging is **human-only**; the Worker never merges.

## Read-only checks

These commands only inspect state. `CFG` is the operator's machine-local
configuration path (not stored in the repository).

```bash
systemctl --user is-active mx-admin-worker
python scripts/feature.py --project-config "$CFG" worker status
python scripts/feature.py --project-config "$CFG" worker attention
```

## Safety note

As part of this test, do not:

- enable systemd at boot,
- enable linger,
- perform any automatic merge.

The Issue's approval is bound to its exact title and body; any edit to either
invalidates the approval.
