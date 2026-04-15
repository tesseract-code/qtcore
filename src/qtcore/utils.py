def configure_high_dpi():
    """
    High DPI configuration for Qt6 applications.
    Must be called BEFORE creating QApplication.
    """
    import os
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtCore import Qt

    # Check for Windows scaling issues
    # "PassThrough" allows for 125%, 150%, 175% scaling (fractional)
    # instead of rounding to the nearest integer (100% or 200%).
    # This prevents UI elements from looking too small or too huge on Windows laptops.
    if hasattr(QGuiApplication, 'setHighDpiScaleFactorRoundingPolicy'):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    if "QT_SCALE_FACTOR" not in os.environ:
        os.environ["QT_SCALE_FACTOR"] = "1"