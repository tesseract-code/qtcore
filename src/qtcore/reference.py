from PyQt6 import QtCore, sip


def has_qt_cpp_binding(obj, strict: bool = False) -> bool:
    """
    Check if a Qt object has a valid C++ binding.

    Args:
        obj: The object to check.
        strict: If True, performs a method call to ensure the C++ pointer
                is valid (slower). usually sip.isdeleted is enough.
    """
    # Python-side type check
    if obj is None or not isinstance(obj, QtCore.QObject):
        return False

    try:
        import shiboken6
        # C-level binding check
        if sip.isdeleted(obj) or not shiboken6.isValid(obj):
            return False

        if strict:
            # signalsBlocked() is a very lightweight C++ call (returns bool).
            # It's faster/safer than objectName() (no string allocation).
            obj.signalsBlocked()

        return True

    except (RuntimeError, ReferenceError):
        # RuntimeError: "wrapped C/C++ object has been deleted"
        return False
    except Exception:
        # Catch-all for weird edge cases if any
        return False
