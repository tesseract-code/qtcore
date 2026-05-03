from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from pycore.platform import IS_MACOS

if IS_MACOS:
    class SafeVTKWidget(QVTKRenderWindowInteractor):
        """
        macOS-only: suppress paintEvent until the render window is ready.

        Calling paintEvent before the OpenGL context is fully initialized
        triggers a freeze on macOS.
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._render_ready = False

        def Initialize(self):
            super().Initialize()
            self._render_ready = True

        def paintEvent(self, event) -> None:
            if self._render_ready:
                super().paintEvent(event)
else:
    _SafeVTKWidget = QVTKRenderWindowInteractor
