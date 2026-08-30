---
description: Regenerate TypeScript types and verify frontend build
---

Run the following commands in sequence:

```bash
python scripts/generate_ts_client.py
python scripts/generate_ts_schemas.py
cd frontend && npm run build
```

If any command fails:
- Explain every issue
- Suggest fixes

Explain any TypeScript errors in detail.
