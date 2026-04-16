# PyCharm Docstring Style Rule

## Goal

All Python docstrings must be optimized for navigation and readability in PyCharm, not for Sphinx or reStructuredText rendering.

## Rules

1. **Use Google-style docstrings**

   * Prefer `Args`, `Returns`, `Raises`, and optional sections like `Notes` or `Calls`
   * Keep formatting simple and IDE-friendly

2. **Reference methods and classes using valid Python syntax**

   * Use `emit()` instead of `:meth:\`emit``
   * Use `Service.run()` instead of `:meth:\`Service.run``

3. **Avoid Sphinx-specific roles**

   * Do NOT use:

     * `:meth:`
     * `:func:`
     * `:class:`
     * any reStructuredText cross-reference roles

4. **Prefer fully qualified references when ambiguity exists**

   * Good: `Logger.emit()`
   * Bad: `emit()` (if multiple emits exist in context)

5. **Keep references resolvable by the IDE**

   * Only reference functions/classes that exist and are importable
   * Avoid pseudo-links or documentation-only constructs

6. **Write natural, concise descriptions**

   * Docstrings should read like developer documentation, not markup
   * Example: "Calls Logger.emit() to send the message"

## Examples

### ? Correct

```python
def process(self, message: str) -> None:
    """
    Process a message and forward it to Logger.emit().

    Args:
        message (str): The message to process.
    """
```

### ? Incorrect (Sphinx-style)

```python
def process(self, message: str) -> None:
    """
    Process a message and forward it to :meth:`Logger.emit`.

    :param message: The message to process
    """
```

## Summary

* Write docstrings for developers using PyCharm
* Use real Python references for navigation
* Avoid Sphinx-specific syntax entirely
* Prioritize clarity and IDE usability over documentation tooling

