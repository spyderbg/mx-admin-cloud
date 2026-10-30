"""Finding sources: each module maps one machine-readable results format to
Finding objects. Every parser here is a pure function over already-produced output —
none of them invoke pytest/pylint/playwright themselves (scripts/validate_tests.py,
validate_build.py, and playwright.config.ts own that).
"""
