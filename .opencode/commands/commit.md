---
description: Stage all changes and commit with a concise message
---

Run the following commands in sequence:

```bash
git add -A
git diff --cached --stat
```

Review the staged changes and generate a concise commit message following the Conventional Commits convention:

- Use a short subject line (imperative mood, no period, no prefix like `feat:` or `docs:`)
- Use a body to explain what and why, not how
- Group related changes logically
- Always format the body of the groups as a bullet list (e.g. `- Group: description`)

Then run:

```bash
git commit -m "<message>"
```

If the commit fails:
- Explain why
- Suggest fixes

If successful:
- Report the commit hash and summary of changes
