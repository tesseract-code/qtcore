import logging
import threading
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from pycore.log.server import TCPLogServer


class TCPLogBridge(QObject):
    """
    Bridge between TCP log server and Qt log widget.

    Receives log records from TCP server and emits them as Qt signals
    for thread-safe GUI updates.
    """

    log_received = pyqtSignal(object)  # Emits LogRecord

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: Optional[TCPLogServer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False

    def start_server(self, port: int = 0) -> int:
        """
        Start the TCP log server.

        Args:
            port: Port to listen on (0 for auto-assign)

        Returns:
            Actual port number being used
        """
        if self._server and self._server.is_running:
            return self._server.port

        self._server = TCPLogServer('localhost', port)
        self._server.start()

        # Start polling thread to check for new messages
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_messages,
            daemon=True,
            name="TCPLogBridge-Poller"
        )
        self._poll_thread.start()

        logging.info(f"TCP log server started on port {self._server.port}")
        return self._server.port

    def stop_server(self) -> None:
        """Stop the TCP log server."""
        self._running = False

        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)

        if self._server:
            self._server.stop()
            self._server = None

        logging.info("TCP log server stopped")

    def _poll_messages(self) -> None:
        """Poll for new log messages and emit signals."""
        last_count = 0

        while self._running and self._server:
            try:
                records = self._server.get_received_records()
                current_count = len(records)

                # Emit new records
                if current_count > last_count:
                    for record in records[last_count:]:
                        self.log_received.emit(record)
                    last_count = current_count

                # Sleep briefly to avoid busy-waiting
                threading.Event().wait(0.1)

            except Exception as e:
                logging.error(f"Error polling TCP log messages: {e}")

    @property
    def port(self) -> Optional[int]:
        """Get the server port."""
        return self._server.port if self._server else None

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server is not None and self._server.is_running
