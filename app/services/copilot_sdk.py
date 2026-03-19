from __future__ import annotations

from importlib import import_module


def _load_sdk_module():
    for module_name in ("copilot", "github_copilot_sdk"):
        try:
            return import_module(module_name)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("No module named 'copilot'")


_sdk_module = _load_sdk_module()
CopilotClient = _sdk_module.CopilotClient
PermissionHandler = _sdk_module.PermissionHandler

__all__ = ["CopilotClient", "PermissionHandler"]
