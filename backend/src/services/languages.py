"""Re-export shim — canonical definitions live in ``src.core.languages``."""
from src.core.languages import (  # noqa: F401
    LANG_MAP,
    LANG_NAME_TO_CODE,
    LANG_TO_BCP47,
    language_name,
    normalize_language_code,
    to_bcp47,
)
