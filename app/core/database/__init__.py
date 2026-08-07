from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.database.base import Base as Base
    from app.core.database.engine import engine as engine
    from app.core.database.session import SessionLocal as SessionLocal

__all__ = [
    "Base",
    "SessionLocal",
    "engine",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "Base": ("app.core.database.base", "Base"),
    "SessionLocal": ("app.core.database.session", "SessionLocal"),
    "engine": ("app.core.database.engine", "engine"),
}
_MISSING = object()


def _resolve(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __getattr__(name: str) -> object:
    return _resolve(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


class _DatabasePackage(ModuleType):
    def __getattribute__(self, name: str) -> object:
        namespace = ModuleType.__getattribute__(self, "__dict__")
        value = namespace.get(name, _MISSING)
        if name in _EXPORTS and (value is _MISSING or isinstance(value, ModuleType)):
            return _resolve(name)
        return ModuleType.__getattribute__(self, name)


sys.modules[__name__].__class__ = _DatabasePackage
