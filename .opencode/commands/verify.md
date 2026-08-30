---
description: Validate backend build, run all tests, and verify frontend build
---

Run the following commands in sequence:

```bash
python scripts/validate_build.py
python scripts/validate_tests.py
python scripts/validate_frontend_build.py
```

If validation fails:
- Explain every issue
- Group errors by backend/frontend/tests

If successful:
- Report that the project is healthy
