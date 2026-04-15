from abc import ABCMeta

from PyQt6.QtCore import QObject

from pycore.singleton import SingletonMeta


class QSingletonMeta(type(QObject), SingletonMeta):
    """
    PyQt-compatible thread-safe singleton metaclass.

    Combines Qt's metaclass requirements with singleton pattern.
    """
    pass


class QABCMeta(ABCMeta, type(QObject)):  # order matters
    """
    QHybridMeta: metaclass combining ABCMeta and QObject's metaclass.

    This metaclass allows defining classes that are both Python abstract base
    classes and PyQt6 QObject subclasses. It merges ABCMeta's abstract-method
    management with the Qt meta-type used by QObject so that:

    - @abstractmethod prevents instantiation until implemented in subclasses.
    - Qt features (signals/slots, meta-object) continue to function.

    Usage:
        class MyBase(QObject, metaclass=QHybridMeta):
            @abstractmethod
            def do_work(self) -> None:
                ...

    Notes:
    - The order of base metaclasses in the class definition matters; ABCMeta must
      come before the QObject metaclass to preserve abstract-method semantics.
    - Requires PyQt6.QtCore.QObject to be importable at class creation time.
    """
    pass
