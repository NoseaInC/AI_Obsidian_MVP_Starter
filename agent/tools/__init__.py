"""Restricted tools available to Agent Brain skills.

Keep the registry import lazy.  Importing a focused submodule such as
``agent.tools.vault_access`` must not eagerly load the complete PDF/tool stack;
doing so made otherwise isolated transaction tests depend on a caller having
already added ``00-System/Scripts`` to ``sys.path``.
"""

from typing import Any

__all__ = ["ToolRegistry", "build_tool_registry"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .registry import ToolRegistry, build_tool_registry

        return {
            "ToolRegistry": ToolRegistry,
            "build_tool_registry": build_tool_registry,
        }[name]
    raise AttributeError(name)
