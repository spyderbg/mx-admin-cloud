"""Auto-import all content provider modules to trigger their register() calls.

Adding a new provider only requires placing a new .py file — or a package whose
__init__.py imports the module that calls register(), like file_system/ — in this
directory; no edits to this file are needed.
"""

import importlib
import pkgutil

import src.services.content.providers as _pkg  # pylint: disable=import-self

for _mod_info in pkgutil.iter_modules(_pkg.__path__, _pkg.__name__ + "."):
    importlib.import_module(_mod_info.name)
