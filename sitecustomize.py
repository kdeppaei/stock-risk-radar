from __future__ import annotations

import importlib.abc
import importlib.util
import sys


class OpenKiriOverlayHook(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname != "app":
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None:
            return spec
        original_loader = spec.loader

        class OverlayLoader(importlib.abc.Loader):
            def create_module(self, module_spec: object) -> object:
                create_module = getattr(original_loader, "create_module", None)
                return create_module(module_spec) if create_module else None

            def exec_module(self, module: object) -> None:
                original_loader.exec_module(module)
                if getattr(module, "_OPENKIRI_LIVE_BOOTSTRAPPED", False):
                    return
                setattr(module, "_OPENKIRI_LIVE_BOOTSTRAPPED", True)
                import openkiri_live  # noqa: F401

        spec.loader = OverlayLoader()
        return spec


if not any(isinstance(hook, OpenKiriOverlayHook) for hook in sys.meta_path):
    sys.meta_path.insert(0, OpenKiriOverlayHook())
