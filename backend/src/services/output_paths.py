"""Re-export shim — canonical definitions live in ``src.core.output_paths``."""
from src.core.output_paths import (  # noqa: F401
    SAFE_NAME_RE,
    build_output_stem,
    build_output_stem_from_result,
    logs_dir,
    normalized_output_dir,
    optimized_output_dir,
    raw_output_dir,
    reports_dir,
    requests_log_path,
    scores_dir,
    slugify_filename,
)
