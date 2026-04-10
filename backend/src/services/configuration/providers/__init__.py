"""Auto-import all configuration provider modules to trigger register() calls."""

import importlib
import pkgutil

import src.services.configuration.providers as _pkg  # pylint: disable=import-self

for _mod_info in pkgutil.iter_modules(_pkg.__path__, _pkg.__name__ + "."):
    if not _mod_info.ispkg:
        importlib.import_module(_mod_info.name)
