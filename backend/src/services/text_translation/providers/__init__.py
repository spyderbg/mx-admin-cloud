"""Auto-import every translation provider package to trigger its register() call.

Each concrete provider is its own package (``<name>/__init__.py`` imports ``provider.py``,
which calls register()), so adding a provider only requires adding its package here.
``_``-prefixed modules hold code shared by several providers; they register nothing.
"""

import importlib
import pkgutil

import src.services.text_translation.providers as _pkg  # pylint: disable=import-self

for _mod_info in pkgutil.iter_modules(_pkg.__path__, _pkg.__name__ + "."):
    importlib.import_module(_mod_info.name)
