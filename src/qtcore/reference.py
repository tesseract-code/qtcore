from PyQt6 import QtCore, sip


def has_qt_binding(obj, strict: bool = False) -> bool:
    """
    Check if a Qt object has a valid C++ binding.

    Args:
        obj: The object to check.
        strict: If True, performs a method call to ensure the C++ pointer
                is valid (slower). usually sip.isdeleted is enough.
    """
    # 1. Fast Python-side type check
    if obj is None or not isinstance(obj, QtCore.QObject):
        return False

    try:
        # 2. Fast C-level binding check
        # This is usually 99.9% accurate
        if sip.isdeleted(obj):
            return False

        # 3. Paranoid check (Strict Mode)
        # Only needed if you suspect 'sip' is out of sync (rare)
        if strict:
            # signalsBlocked() is a very lightweight C++ call (returns bool).
            # It's faster/safer than objectName() (no string allocation).
            obj.signalsBlocked()

        return True

    except (RuntimeError, ReferenceError):
        # RuntimeError: "wrapped C/C++ object has been deleted"
        return False
    except Exception:
        # Catch-all for weird edge cases (e.g. obj is not actually a QObject
        # despite isinstance check, though unlikely)
        return False
