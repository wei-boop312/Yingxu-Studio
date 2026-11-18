"""Read only selected private credentials; explicit process values override Windows user values."""

import os
import sys

PROVIDER_KEY_NAMES = frozenset(
    {"BFL_API_KEY", "HUBWAY_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY"}
)


def _windows_user_credential(name):
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", access=winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, name)
        return value if kind == winreg.REG_SZ and isinstance(value, str) else None
    except OSError:
        return None


def private_credential(name, environment=None):
    if name not in PROVIDER_KEY_NAMES:
        raise ValueError("unsupported_provider_credential")
    values = os.environ if environment is None else environment
    if name in values:
        # Even a blank explicit override must not silently resurrect an older user key.
        return values[name]
    if environment is not None:
        return None
    return _windows_user_credential(name)
