---
description: Export OpenAPI spec and regenerate TypeScript client + schemas
---

Run the following commands in sequence:

```bash
python scripts/export_openapi.py
python scripts/generate_ts_client.py
python scripts/generate_ts_schemas.py
```

If any command fails:
- Explain why
- Suggest fixes

If generation succeeds:
- Summarize which files changed
- Do not commit anything
