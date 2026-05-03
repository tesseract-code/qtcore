"""
Integrated LogManager with TCP logging support for local and remote processes.

Supports:
- Local logging (same process)
- TCP server for receiving logs from remote processes
- Process-safe port management
- Singleton pattern with process awareness
"""
import logging
import sys
import threading
import traceback
from contextlib import contextmanager
from typing import Optional, Any, Type

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QApplication

from pycore.log.handler import JSONSocketHandler
from pycore.log.port import PortManager
from pycore.log.remote import RemoteLogServerProcess
from qtcore.app import Application
from qtcore.log.bridge import TCPLogBridge
from qtcore.meta import QSingletonMeta
from qtgui.log.widget import LogWidget


class LogManager(QObject, metaclass=QSingletonMeta):
    """
    Thread-safe manager for application-wide logging with TCP support.

    Supports two modes:
    1. Local mode: Logs from the same process
    2. Server mode: Receives logs from remote processes via TCP

    In server mode, it can run as a standalone log viewer process.
    """

    # Class-level singleton (per-process)
    _instance = None
    _instance_lock = threading.RLock()
    _initialized = False

    def __new__(cls):
        """Implement singleton pattern per process."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self) -> None:
        """Initialize the log manager with default state."""
        # Only initialize once
        if LogManager._initialized:
            return

        super().__init__()

        # Initialize instance variables
        self._log_widget: Optional['LogWidget'] = None
        self._original_excepthook: Any = None
        self._handler: Optional[logging.Handler] = None
        self._app: Optional[QApplication] = None
        self._initialization_lock = threading.RLock()

        # TCP support
        self._tcp_bridge: Optional[TCPLogBridge] = None
        self._tcp_port: Optional[int] = None
        self._is_server_mode = False

        self._initialized = False

    def initialize(
            self,
            app: QApplication,
            log_widget: 'LogWidget',
            level: int = logging.DEBUG,
            auto_show: bool = True,
            enable_tcp_server: bool = False,
            tcp_port: Optional[int] = None
    ) -> 'LogWidget':
        """
        Initialize the logging system.

        Args:
            app: QApplication instance
            log_widget: LogWidget instance to display logs
            level: Logging level (default: DEBUG)
            auto_show: Whether to show log widget immediately
            enable_tcp_server: Enable TCP server for remote logging
            tcp_port: TCP port (None for auto-select)

        Returns:
            The configured log widget

        Raises:
            RuntimeError: If initialization fails
        """
        with self._initialization_lock:
            if self._log_widget is not None:
                logging.debug("LogManager already initialized")
                return self._log_widget

            try:
                self._app = app
                self._log_widget = log_widget
                self._original_excepthook = sys.excepthook

                # Configure log widget
                log_widget.setWindowTitle("Application Log Viewer")
                log_widget.resize(1400, 700)

                # Setup local logging handler
                self._handler = log_widget.get_handler()
                self._handler.setLevel(level)

                # Configure root logger
                root_logger = logging.getLogger()
                root_logger.addHandler(self._handler)
                root_logger.setLevel(level)

                # Setup TCP server if requested
                if enable_tcp_server:
                    self._setup_tcp_server(tcp_port)

                # Install exception hook
                sys.excepthook = self._exception_hook

                # Connect cleanup to application quit
                app.aboutToQuit.connect(self.cleanup)

                # Show widget if requested
                if auto_show:
                    log_widget.show()

                logging.info("Logging system initialized successfully")
                if self._tcp_port:
                    logging.info(
                        f"TCP log server listening on port {self._tcp_port}")

                self._initialized = True
                return log_widget

            except Exception as e:
                # Reset state on failure
                self._cleanup_state()
                raise RuntimeError(
                    f"Failed to initialize LogManager: {e}") from e

    def _setup_tcp_server(self, port: Optional[int] = None) -> None:
        """Setup TCP server for receiving remote logs."""
        try:
            # Acquire a port
            acquired_port = PortManager.acquire_port(port)
            if acquired_port is None:
                raise RuntimeError("Failed to acquire TCP port")

            # Create and start TCP bridge
            self._tcp_bridge = TCPLogBridge()
            self._tcp_bridge.log_received.connect(self._handle_remote_log)

            actual_port = self._tcp_bridge.start_server(acquired_port)
            self._tcp_port = actual_port
            self._is_server_mode = True

            # Update lock file with actual port
            if actual_port != acquired_port:
                PortManager.release_port(acquired_port)
                PortManager.acquire_port(actual_port)

        except Exception as e:
            logging.error(f"Failed to setup TCP server: {e}")
            raise

    @pyqtSlot(object)
    def _handle_remote_log(self, record: logging.LogRecord) -> None:
        """Handle a log record received from TCP server."""
        if self._handler:
            self._handler.emit(record)

    def _exception_hook(
            self,
            exc_type: Type[BaseException],
            exc_value: BaseException,
            exc_traceback: Any
    ) -> None:
        """Custom exception hook for uncaught exceptions."""
        if issubclass(exc_type, KeyboardInterrupt):
            if self._original_excepthook:
                self._original_excepthook(exc_type, exc_value, exc_traceback)
            return

        try:
            tb_text = ''.join(
                traceback.format_exception(exc_type, exc_value, exc_traceback))

            logging.critical(
                f"Uncaught exception: {exc_type.__name__}: {exc_value}\n{tb_text}",
                exc_info=(exc_type, exc_value, exc_traceback)
            )

            self._ensure_log_widget_visible()

        except Exception as log_error:
            if self._original_excepthook:
                self._original_excepthook(exc_type, exc_value, exc_traceback)
            else:
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
            logging.error(f"Failed to log exception: {log_error}")

    def _ensure_log_widget_visible(self) -> None:
        """Ensure log widget is visible and raised to front."""
        if self._log_widget and not self._log_widget.isVisible():
            self._log_widget.show()
            self._log_widget.raise_()
            self._log_widget.activateWindow()

    def _cleanup_state(self) -> None:
        """Clean up internal state."""
        self._log_widget = None
        self._handler = None
        self._app = None
        if self._tcp_port:
            PortManager.release_port(self._tcp_port)
            PortManager.clean_stale_locks()
        self._tcp_port = None

    @pyqtSlot()
    def cleanup(self) -> None:
        """Cleanup logging resources."""
        logging.info("Shutting down logging system")

        try:
            # Stop TCP server
            if self._tcp_bridge:
                self._tcp_bridge.stop_server()
                self._tcp_bridge = None

            # Release port
            if self._tcp_port:
                PortManager.release_port(self._tcp_port)
                self._tcp_port = None

            # Remove handler from root logger
            if self._handler:
                root_logger = logging.getLogger()
                root_logger.removeHandler(self._handler)
                self._handler.close()
                self._handler = None

            # Restore original exception hook
            if self._original_excepthook:
                sys.excepthook = self._original_excepthook
                self._original_excepthook = None

            # Close log widget
            if self._log_widget:
                # if self._log_widget.isVisible():
                # self._log_widget.close()
                self._log_widget = None

            self._cleanup_state()
            logging.info("Logging system shutdown complete")

        except Exception as e:
            logging.error(f"Error during LogManager cleanup: {e}")

    @property
    def is_initialized(self) -> bool:
        """Check if logging system is initialized."""
        return self._log_widget is not None

    @property
    def log_widget(self) -> Optional['LogWidget']:
        """Get the current log widget instance."""
        return self._log_widget

    @property
    def tcp_port(self) -> Optional[int]:
        """Get the TCP server port (None if not enabled)."""
        return self._tcp_port

    @property
    def is_server_mode(self) -> bool:
        """Check if running in TCP server mode."""
        return self._is_server_mode

    def show_log_widget(self) -> None:
        """Show the log widget window."""
        self._ensure_log_widget_visible()

    def hide_log_widget(self) -> None:
        """Hide the log widget window."""
        if self._log_widget:
            self._log_widget.hide()

    def __del__(self) -> None:
        """Destructor to ensure cleanup."""
        if hasattr(self, '_handler') and self._handler:
            self.cleanup()


def setup_local_logging(
        app: QApplication,
        log_widget: 'LogWidget',
        level: int = logging.DEBUG,
        auto_show: bool = True
) -> 'LogWidget':
    """
    Setup local logging (same process).

    Args:
        app: QApplication instance
        log_widget: LogWidget instance
        level: Logging level
        auto_show: Whether to show log widget immediately

    Returns:
        The configured log widget
    """
    return LogManager().initialize(
        app, log_widget, level, auto_show,
        enable_tcp_server=False
    )


def setup_tcp_server_logging(
        app: QApplication,
        log_widget: 'LogWidget',
        port: Optional[int] = None,
        level: int = logging.DEBUG,
        auto_show: bool = True
) -> tuple['LogWidget', int]:
    """
    Setup TCP server logging (receives logs from remote processes).

    Args:
        app: QApplication instance
        log_widget: LogWidget instance
        port: TCP port (None for auto-select)
        level: Logging level
        auto_show: Whether to show log widget immediately

    Returns:
        Tuple of (log_widget, port_number)
    """
    widget = LogManager().initialize(
        app, log_widget, level, auto_show,
        enable_tcp_server=True,
        tcp_port=port
    )
    return widget, LogManager().tcp_port


def setup_remote_client_logging(
        server_host: str = 'localhost',
        server_port: Optional[int] = None,
        level: int = logging.DEBUG,
        use_ssl: bool = False
) -> bool:
    """
    Setup logging to send to a remote TCP log server.

    This is called by client processes to send their logs to the server.

    Args:
        server_host: Server hostname
        server_port: Server port (None to auto-detect)
        level: Logging level
        use_ssl: Enable SSL

    Returns:
        True if successful, False otherwise
    """
    try:
        # Auto-detect server port if not specified
        if server_port is None:
            server_port = PortManager.find_active_server_port()
            if server_port is None:
                logging.error("No active TCP log server found")
                return False

        # Setup TCP handler
        handler = JSONSocketHandler(
            server_host,
            server_port,
            use_ssl=use_ssl
        )
        handler.setLevel(level)

        # Add to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(level)

        logging.info(
            f"Remote logging configured to {server_host}:{server_port}")
        return True

    except Exception as e:
        logging.error(f"Failed to setup remote logging: {e}")
        return False


@contextmanager
def remote_log_manager(
        port: Optional[int] = None,
        max_lines: int = 10000,
        font_size: int = 10,
        auto_connect: bool = True
):
    """
    Context manager that starts a log server in a separate process.

    This spawns a PyQt6 log viewer in another process and optionally
    connects the current process to it.

    Args:
        port: TCP port (None for auto-select)
        max_lines: Maximum lines in log widget
        font_size: Font size for log display
        auto_connect: Automatically connect current process to server

    Yields:
        RemoteLogServerProcess instance with .actual_port property

    Example:
        ```python
        with remote_log_manager() as log_server:
            print(f"Log server running on port {log_server.actual_port}")

            # Your logging automatically goes to the remote viewer
            logging.info("Hello from main process!")

            # Do your work here
            my_application()
        # Server automatically stops when context exits
        ```

    Example without auto-connect:
        ```python
        with remote_log_manager(auto_connect=False) as log_server:
            # Manually connect when ready
            setup_remote_client_logging(server_port=log_server.actual_port)
            logging.info("Now connected!")
        ```
    """
    server = RemoteLogServerProcess(port, max_lines, font_size)

    try:
        # Start server
        actual_port = server.start()

        # Auto-connect if requested
        if auto_connect:
            success = setup_remote_client_logging(server_port=actual_port)
            if not success:
                logging.warning(
                    f"Failed to auto-connect to log server on port {actual_port}")

        yield server

    finally:
        server.stop()


def run_standalone_log_server(port: Optional[int] = None):
    """
    Run a standalone TCP log server process.

    This can be run in a separate process to collect logs from multiple clients.

    Args:
        port: TCP port (None for auto-select)
    """
    import sys

    app = Application(argv=sys.argv)
    log_widget = LogWidget(max_lines=10000, font_size=10)

    try:
        widget, actual_port = setup_tcp_server_logging(
            app, log_widget, port=port, auto_show=True
        )

        log_widget.setWindowTitle(f"TCP Log Server - Port {actual_port}")

        print(f"TCP Log Server running on port {actual_port}")
        print(
            f"Clients can connect using: setup_remote_client_logging(server_port={actual_port})")
        print("Press Ctrl+C to stop...")

        sys.exit(app.exec())

    except KeyboardInterrupt:
        print("\nShutting down...")
        LogManager().cleanup()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TCP Log Server")
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=None,
        help='TCP port to listen on (auto-select if not specified)'
    )

    args = parser.parse_args()
    run_standalone_log_server(port=args.port)
