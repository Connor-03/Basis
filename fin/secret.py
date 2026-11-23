"""Keeping a secret (the SnapTrade consumer key) on disk.

On Windows the value is encrypted with the Windows account that saved it (DPAPI, the same store browsers use for
saved passwords): another user of the computer, or someone who copies the file elsewhere, cannot read it. Elsewhere
the file's own permissions (owner only) are the protection and the value is stored as it is.
"""
import base64
import sys

PREFIX = 'dpapi:'


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), buf), Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), 'Windows could not ' + ('protect' if protect else 'read') + ' the saved key')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    if not text or sys.platform != 'win32' or text.startswith(PREFIX):
        return text
    return PREFIX + base64.b64encode(_dpapi(text.encode('utf-8'), True)).decode('ascii')


def reveal(value):
    if not value or not str(value).startswith(PREFIX):
        return value
    if sys.platform != 'win32':
        raise OSError('This key was saved on a Windows computer and can only be read there. Enter it again.')
    return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode('utf-8')
