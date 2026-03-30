"""Auto-import all translation provider modules to trigger their register() calls.

Adding a new provider only requires placing a new .py file in this directory —
no edits to this file are needed.
"""

import importlib
import pkgutil

import src.services.text_translation.providers as _pkg  # pylint: disable=import-self

for _mod_info in pkgutil.iter_modules(_pkg.__path__, _pkg.__name__ + "."):
    if not _mod_info.ispkg:
        importlib.import_module(_mod_info.name)
