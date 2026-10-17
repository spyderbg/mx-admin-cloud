# Backend Task B-7 — Tests Must Not Touch the Real `content/` Tree

> Owner role: **backend** (the fix lives in `backend/**`). Written by coordination, Phase 3, 2026-10-17. Status: **open**; blocks running the real `backend-tests` gate against a checkout ([feature-workflow §11](feature-workflow.md#11-gate-catalog-existing-generic-checks)).
> Origin: discovery finding F-5 ([discovery §12](../discovery/README.md)), blocker B-7 ([phase-status](phase-status.md)).

## Problem (pinpointed in Phase 3) **[V]**

`backend/src/services/content/__tests__/test_content_filesystem_item_images.py::test_delete_item_image_last_remaining_is_allowed` takes registry locks in the **real** content tree. It creates:

- `content/.categories_registry.lock`
- `content/foods/.items_registry.lock`

**Cause.** The test patches the settings only around the delete call, then asserts **outside** the `with` block:

```python
with patch_fs_settings() as mock_settings:
    mock_settings.content_path = content_root
    provider.delete_item_image("C_Foods", "I_Apple", "apple")

assert not list(item_path.glob("*.png"))
assert not provider.item_images("C_Foods", "I_Apple").images   # ← real settings: CONTENT_DIR=../content
```

`item_images()` therefore resolves the default `CONTENT_DIR=../content`, the real tree, and `registry_lock()` creates the lock files there.

Two properties hide the problem:

- `registry_lock()` deliberately falls back to an in-process lock when the lock file cannot be created, so the test also passes on a read-only tree;
- `.gitignore` ignores `content/**/.*_registry.*`, so the leaked files never show in `git status`.

**How it was found, without touching user content:**

1. a `git archive` export of `HEAD` (`backend/` + `content/`) in a scratch directory;
2. the full suite run in a bubblewrap sandbox where only that export was writable and the real checkout was not mounted;
3. a throwaway pytest plugin, living only in the export, recording which test first created each lock file.

The full suite on the export: **919 passed**. With the export's `content/` read-only, it also passed 919/919, with no write.

## Required fix (Backend role)

1. Move the final assertion inside the `with patch_fs_settings()` block, or patch the settings for the whole test with a fixture.
2. Add a guard so no backend test can resolve the real content tree. Either:
   - an autouse fixture in the content tests' `conftest.py` that points `content_path` (and `CONTENT_DIR`) at a per-test temporary directory unless a test sets its own; or
   - a session-scoped check that fails when any `.*_registry.lock` appears under the real `content/` during the run.
3. Verify by repeating the Phase 3 method: run the suite in an export whose `content/` is read-only, then in one where it is writable, and confirm that **no** file under `content/` was created.

**Acceptance:**

- the full backend suite passes (919 or more);
- a run on a writable export leaves `content/` unchanged;
- the coordination owner removes `backend-tests` from `unsafe_gates` in `config.py` and in [`project-config.example.yaml`](project-config.example.yaml); the gate is then allowed against a checkout, still sandboxed, read-only and offline.

## Constraints

- Do not change `registry_lock()`'s read-only fallback as part of this task: it is production behavior. Its silent fallback is a separate review item.
- Do not modify authentication or debug defaults (D-5), production data, or `content/` itself.
