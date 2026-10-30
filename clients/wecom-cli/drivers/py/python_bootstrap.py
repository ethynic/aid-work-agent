"""Run a driver with the bundled Windows DLL paths kept usable by native wheels.

Only existing filesystem aliases are queried. No files, links or system settings
are changed, and driver arguments/stdin remain the driver's own input channel.
"""
import os
import sys

_prefix = '\\\\?\\'
_runtime = os.path.dirname(sys.executable)
_dll_handles = []


def _namespace(value):
    value = os.path.abspath(value)
    if value.startswith(_prefix):
        return value
    return _prefix + 'UNC\\' + value[2:] if value.startswith('\\\\') else _prefix + value


def _plain(value):
    if value.startswith(_prefix + 'UNC\\'):
        return '\\\\' + value[8:]
    return value[4:] if value.startswith(_prefix) else value


if os.name == 'nt' and os.path.isfile(os.path.join(_runtime, 'asset-provenance.json')):
    _dll_handles.append(os.add_dll_directory(_namespace(_runtime)))
    sys.path[:] = [_namespace(value) for value in sys.path]
    import ctypes
    from ctypes import wintypes

    _get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
    _get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _get_short.restype = wintypes.DWORD

    def _existing_short(value):
        needed = _get_short(_namespace(os.path.normpath(value)), None, 0)
        if not needed:
            return value
        buffer = ctypes.create_unicode_buffer(needed)
        copied = _get_short(_namespace(os.path.normpath(value)), buffer, needed)
        if not copied or copied >= needed:
            return value
        plain = _plain(buffer.value)
        return plain if len(plain) < 260 else buffer.value

    sys.path[:] = [_existing_short(value) for value in sys.path]
    _realpath = os.path.realpath
    _runtime_key = os.path.normcase(_plain(_realpath(_runtime))).rstrip('\\')

    def _runtime_realpath(value, *args, **kwargs):
        result = _realpath(value, *args, **kwargs)
        if isinstance(result, str):
            key = os.path.normcase(_plain(result))
            if key == _runtime_key or key.startswith(_runtime_key + '\\'):
                return _existing_short(result)
        return result

    # cv2/Path.resolve must not expand a usable runtime alias back past MAX_PATH.
    # Business files and driver paths keep their original realpath semantics.
    os.path.realpath = _runtime_realpath

import runpy

_script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(_script, run_name='__main__')
