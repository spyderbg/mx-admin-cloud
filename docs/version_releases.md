# Version releases

Run from the repository root (or use the script's absolute path from any directory):

```sh
python scripts/increment_version.py --dry-run
python scripts/increment_version.py
```

The default increments the build digit. `--part minor` and `--part major` reset
the lower digits. Each component is a single digit: `1.0.9` becomes `1.1.0`,
`1.9.9` becomes `2.0.0`, and increments beyond `9.9.9` fail. This is the
project's major.minor.build policy. The script updates the frontend version in
`frontend/package.json` and both root version fields in `frontend/package-lock.json`.
The footer reads this version. Backend and CLI package versions have separate lifecycles.

Before writing files, the script requires:

- A clean Git index and working tree, including untracked files, with no unfinished
  merge, rebase, cherry-pick, or revert.
- The current branch to be `dev`.
- A successful fetch of `origin/dev`, `origin/main`, and tags, followed by an exact
  match between `dev` and `origin/dev`. Local commits must already be pushed;
  behind or divergent branches must be synchronized first.
- Local `main` to have no unpushed/divergent commits and to be available in this
  worktree. A missing local `main` is created from `origin/main` during publishing.
- Matching package versions, an unused new version tag, and changes since the
  previous version increment.

Ignored build output and environment files are unaffected. A failed fetch stops
the release. A dry run performs the same checks and refreshes remote-tracking
refs/tags, but leaves working files, commits, local branches, and origin unchanged.

## Changelog

`docs/log_changes.md` follows [Keep a Changelog 1.0.0](https://keepachangelog.com/en/1.0.0/):
an Unreleased section, newest releases first, ISO release dates in Europe/Sofia,
and nonempty Added, Changed, Deprecated, Removed, Fixed, and Security sections.
Existing history and curated Unreleased text are preserved; Unreleased entries
move into the new release. HTTP/SSH origins get release and comparison links.

The script collects commit subjects (first lines) since the most recent
first-parent `Increment version to X.Y.Z` commit. Without such a commit, the
current version tag (`releases/vX.Y.Z`, with legacy `X.Y.Z` and `vX.Y.Z` tags also
recognized) supplies the boundary. If no boundary exists,
the first release includes the reachable history. Commit bodies are excluded.

Subject classification recognizes conventional `feat:`, `fix:`, and `security:`
prefixes, common Added/Fixed/Removed/Deprecated wording, and security keywords.
Other subjects go under Changed. Breaking-change markers are retained, repeated
entries are deduplicated, and merge/release bookkeeping is omitted. Classification
is a heuristic: write clear subjects and review `--dry-run` output before release.
For curated wording, commit entries in the appropriate Unreleased sections first.

## Publication

The script commits only the three release files using the exact message
`Increment version to X.Y.Z`, creates an annotated tag named `releases/vX.Y.Z`,
and atomically pushes `dev` and that tag to origin. The origin must support atomic
pushes. It then performs the equivalent of:

```sh
git checkout main
git pull --ff-only origin main
git merge --no-edit dev
git push origin HEAD:refs/heads/main
git checkout dev
```

There are no force pushes. Merge conflicts are aborted and the script returns to
`dev`. Network errors, permissions, hooks, concurrent remote updates, and branch
protections can still reject publication. Errors identify the failing phase and
completed steps. Releases across two branch pushes are not one atomic operation:
if updating `main` fails, the release commit/tag may already be on origin/dev.
Inspect Git state and resolve the reported problem before retrying. The script
preserves commits, tags, and failed-release edits for inspection.

The script requires Python 3.10+, Git, configured Git author identity, access to
origin, and system timezone data for Europe/Sofia. It uses the Python standard
library. Run its tests without contacting the project's origin:

```sh
python -m unittest discover -s scripts/tests -p 'test_increment_version.py' -v
```
