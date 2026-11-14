"""Denkzeug import names sharing existing implementation and serialization identity.

Do not forward __path__ alone: that executes submodules twice under different
names and duplicates settings, databases and connection pools. These aliases are
for imports, not replacement ``python -m`` service entrypoints. Bootstrap package
trees on the startup thread before concurrent consumers use both spellings;
arbitrary cross-name concurrent cold imports are not supported.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

ALIASES = {
    "denkzeug": "nautionette",
    "denkzeug_backend": "nautionette_backend",
    "denkzeug_docker_broker": "nautionette_docker_broker",
    "denkzeug_worker": "nautionette_worker",
    "denkzeug_workflow_mcp": "nautionette_workflow_mcp",
}


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self.target = target

    def create_module(self, spec):
        return None  # Let import machinery initialize a disposable alias shell.

    def exec_module(self, module) -> None:
        # Replace the shell, never mutate the canonical module's metadata even
        # temporarily. Canonical reload and pickling identities remain intact.
        sys.modules[module.__name__] = importlib.import_module(self.target)


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root, separator, child = fullname.partition(".")
        if not separator or root not in ALIASES:
            return None
        canonical = ALIASES[root] + "." + child
        # A finder runs under Python's global import lock. util.find_spec can
        # execute a parent package here and deadlock concurrent alias/canonical
        # imports. The already-loaded alias parent supplies the canonical path.
        cached = sys.modules.get(canonical)
        spec = getattr(cached, "__spec__", None) or importlib.machinery.PathFinder.find_spec(canonical, path)
        if spec is None:
            return None
        return importlib.machinery.ModuleSpec(
            fullname, _AliasLoader(canonical), is_package=spec.submodule_search_locations is not None
        )


_finder = _AliasFinder()


def install_alias(name: str) -> ModuleType:
    """Install one explicitly packaged root alias, resolving descendants lazily."""
    canonical = ALIASES[name]
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)
    module = importlib.import_module(canonical)
    sys.modules[name] = module
    return module
