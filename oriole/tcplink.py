"""Wavefront IV over TCP (Oriole TCP), as tried on the rig.

With "Tcp server" ticked in Wavefront's script engine (Tools → Script engine; one source at a time, not remembered
across restarts: ticked by hand for now), every message is run as a script: the same JavaScript as a test.js in its
script folder. A message is a Qt QDataStream QByteArray, as arduinoReader sends it (captured on the rig): its length
(4 bytes, big-endian), then the UTF-8 script. Wavefront answers nothing: what a script did shows in the files it saves.
Its server opens by itself at every start, listening on every address, on a new port written after its IP in
%TEMP%\\blueBoxServer.
"""
from __future__ import annotations

import socket
import struct
import tempfile
from pathlib import Path

PORT_FILE = Path(tempfile.gettempdir()) / "blueBoxServer"
CONNECT_S = 0.5             # Wavefront on this PC accepts at once; a refused connection takes 2 s to fail on Windows
TIMEOUT_S = 2.0
TCP_HINT = 'tick "Tools → Script engine → Tcp server" in Wavefront IV'


class LinkError(RuntimeError):
    pass


def server_port(path=None) -> int:
    """The port Wavefront IV's server listens on, from the file it writes at every start (IP, then port)."""
    path = Path(path or PORT_FILE)
    try:
        return int(path.read_text().split()[1])
    except (OSError, IndexError, ValueError) as e:
        raise LinkError(f"no Wavefront IV server port in {path} ({e}): is Wavefront IV running?") from e


def frame(js: str) -> bytes:
    """A message as Wavefront IV reads it: a QDataStream QByteArray (length, then the UTF-8 script)."""
    data = js.encode("utf-8")
    return struct.pack(">I", len(data)) + data


class Wavefront4Link:
    """The connection to Wavefront IV's TCP server: made when first needed, made again when it broke or Wavefront
    restarted (a new port in the file)."""

    def __init__(self, port_file=None):
        self.port_file = port_file
        self.port: int | None = None
        self._sock: socket.socket | None = None

    def send(self, js: str):
        """Send one script; LinkError when Wavefront IV cannot be reached."""
        data = frame(js)
        for attempt in (1, 2):                     # a connection found dead while sending is made again once
            sock = self._connected()
            try:
                sock.sendall(data)
                return
            except OSError as e:
                self.close()
                if attempt == 2:
                    raise LinkError(f"Wavefront IV not reachable over TCP (port {self.port}): {e}") from e

    def check(self) -> int:
        """Connect (or keep the connection): the port, or LinkError."""
        self._connected()
        return self.port

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def _connected(self) -> socket.socket:
        port = server_port(self.port_file)
        if self._sock is not None and (port != self.port or not self._alive()):
            self.close()                            # Wavefront restarted, or closed the connection
        if self._sock is None:
            self.port = port
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=CONNECT_S)
            except OSError as e:
                raise LinkError(f"Wavefront IV not reachable over TCP (port {port}): {e}") from e
            self._sock.settimeout(TIMEOUT_S)
        return self._sock

    def _alive(self) -> bool:
        """Still connected: nothing to read (Wavefront never writes), and not closed by Wavefront."""
        try:
            self._sock.setblocking(False)
            return self._sock.recv(1, socket.MSG_PEEK) != b""
        except BlockingIOError:
            return True
        except OSError:
            return False
        finally:
            if self._sock is not None:
                self._sock.settimeout(TIMEOUT_S)
