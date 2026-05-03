from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from pycore.platform import IS_MACOS

if IS_MACOS:

    class SafeVTKWidget(QVTKRenderWindowInteractor):
        def paintEvent(self, ev):
            pass
else:
    SafeVTKWidget = QVTKRenderWindowInteractor
