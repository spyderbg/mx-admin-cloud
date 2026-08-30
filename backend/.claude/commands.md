# Custom Commands for This Project

Claude, treat the following slash-prefixed names as project commands.
When I type one of these commands, execute the corresponding task.

---

## Command: /sync_readme

Description:
Synchronize README.md with the current state of the FastAPI backend.

Steps:
1. Inspect the codebase for recent changes.
2. Detect new endpoints, models, services, or renamed functions.
3. Update README.md so it accurately reflects the implementation.
4. Output the full updated README.md file.
5. Do not invent features; only document what exists.

---

## Command: /create_tests

Description:
Generate missing unit and integration tests for the FastAPI backend.

Steps:
1. Inspect the project structure.
2. Identify missing unit tests for services, utils, and models.
3. Identify missing integration tests for API endpoints.
4. Generate pytest-compatible tests using:
   - pytest
   - pytest-asyncio
   - httpx.AsyncClient
5. Place tests under ``__tests__/unit/`` and ``__tests__/integration/`` beside the module (see ``src/api/internal/__tests__/``).
6. Output the created or updated test files.

---

## Command: /create_tests_for <path>

Description:
Generate tests only for a specific module or file.

Steps:
1. Read the file at <path>.
2. Generate appropriate unit tests.
3. Place them under ``__tests__/unit/`` or ``__tests__/integration/`` next to that module.
4. Output the full test file.
