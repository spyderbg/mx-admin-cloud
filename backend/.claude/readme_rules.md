# README Synchronization Rules

Claude, follow these rules whenever you update or regenerate README.md.
These rules are strict. Do not deviate from them.

---

# 1. Source Layout Section

When generating the "Source layout" section of README.md, ALWAYS reproduce
the exact folder structure shown below. Preserve:

- indentation
- tree characters (├──, └──, │)
- inline comments
- ordering of folders and files
- spacing and blank lines
- Markdown code block formatting

Never reorder, rename, or omit items. Never add new folders unless I explicitly ask.

The structure to ALWAYS output:

### Source layout

---

# 2. General README Update Rules

When updating README.md:

- Keep the "Source layout" section EXACTLY as defined above.
- Never infer or hallucinate folder names, files, or endpoints.
- Only document features that exist in the codebase.
- When describing endpoints, models, or services, extract information from the actual source files.
- Preserve Markdown formatting and heading hierarchy.
- Keep explanations concise and technical.
- Do not rewrite or reorganize sections unless explicitly asked.

---

# 3. Endpoint Documentation Rules

When documenting API endpoints:

- Read the FastAPI route definitions directly.
- Include:
  - HTTP method
  - path
  - summary (if present)
  - request model
  - response model
- Do not invent parameters or responses.

---

# 4. Model Documentation Rules

When documenting Pydantic models or dataclasses:

- List fields in the exact order they appear in the file.
- Include type annotations.
- Include default values if present.
- Do not add fields that do not exist.

---

# 5. Service Documentation Rules

When documenting services:

- Describe only what is implemented.
- Do not speculate about future features.
- Keep descriptions short and factual.

---

# 6. Output Format Rules

- Always output the full updated README.md file.
- Do not output diffs unless I explicitly ask.
- Do not wrap the README in quotes or backticks unless I ask.

---

# 7. Safety Rules

- If something is unclear or ambiguous, ask me before making assumptions.
- Never modify the folder structure unless I explicitly request it.

