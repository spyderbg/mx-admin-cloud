# Run Checks Command

Run backend validation checks for this project.

## Steps

1. **Syntax Check**

* Run:

  ```bash
  python3 -m compileall src
  ```
* Confirm there are no syntax errors

---

2. **Test Execution**

* Try running:

  ```bash
  python3 -m pytest tests -q
  ```

* If pytest is missing:

  * Detect project virtual environment (e.g. `.venv/`, `venv/`)
  * Re-run using:

    ```bash
    .venv/bin/python -m pytest tests -q
    ```

* If no venv is found:

  * Suggest installing pytest:

    ```bash
    pip install pytest
    ```

---

3. **Linting**

* Check IDE diagnostics for `src/`
* Ensure no PyLint errors
* If issues exist, fix them following project rules

---

## Expected Behavior

* Prefer project virtual environment over system Python
* Do NOT modify global environment
* Do NOT install packages globally
* Report clear results for:

  * syntax
  * tests
  * linting

---

## Output Format

Return a summary:

* ✅ Syntax: passed / failed
* ✅ Tests: passed / failed (include reason)
* ✅ Lint: passed / failed

If tests fail due to environment issues, suggest the correct command using the project venv.

---

## Principle

Always run checks in an isolated environment and provide actionable feedback.
