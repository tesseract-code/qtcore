from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from pycore.platform import IS_MACOS

class SafeVTKWidget(QVTKRenderWindowInteractor):
        def paintEvent(self, ev):
            pass

