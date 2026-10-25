import socket
import struct
import threading
import time

import pytest

from oriole.tcplink import LinkError, Wavefront4Link, frame, server_port


def _until(condition, timeout=2.0):
    end = time.time() + timeout
    while not condition():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


class Server:
    """Wavefront IV's TCP server as Oriole sees it: its port written in the port file, the messages read, no answer."""

    def __init__(self, port_file):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        port_file.write_text(f"172.17.19.37\n{self.port}")         # as Wavefront writes it: its IP, then the port
        self.messages, self.conns = [], []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        buf = b""
        while True:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            buf += data
            while len(buf) >= 4 and len(buf) >= 4 + struct.unpack(">I", buf[:4])[0]:
                n = struct.unpack(">I", buf[:4])[0]
                self.messages.append(buf[4:4 + n].decode("utf-8"))
                buf = buf[4 + n:]

    def drop_connections(self):
        for conn in self.conns:
            conn.close()

    def close(self):
        self.sock.close()
        self.drop_connections()


def test_frame_is_a_qdatastream_qbytearray():
    assert frame("cleanAllBlob();") == b"\x00\x00\x00\x0fcleanAllBlob();"
    assert frame("µ") == b"\x00\x00\x00\x02\xc2\xb5"                  # its UTF-8 bytes counted


def test_server_port_from_wavefronts_file(tmp_path):
    f = tmp_path / "blueBoxServer"
    f.write_text("172.17.19.37\n59454")
    assert server_port(f) == 59454
    for text in ("", "172.17.19.37", "172.17.19.37\nport"):
        f.write_text(text)
        with pytest.raises(LinkError, match="no Wavefront IV server port in .*is Wavefront IV running"):
            server_port(f)
    with pytest.raises(LinkError, match="is Wavefront IV running"):
        server_port(tmp_path / "missing")


def test_link_sends_reconnects_and_follows_a_restart(tmp_path):
    f = tmp_path / "blueBoxServer"
    server = Server(f)
    link = Wavefront4Link(f)
    assert link.check() == server.port
    mask = 'drawSpot(1.0000,2.0000,10.0000);\nproceedPhp();\n'
    link.send("cleanAllBlob();")
    link.send(mask)
    _until(lambda: len(server.messages) == 2)
    assert server.messages == ["cleanAllBlob();", mask] and len(server.conns) == 1     # one connection kept

    # Wavefront closed the connection: made again, nothing lost
    server.drop_connections()
    _until(lambda: not link._alive())
    link.send("a")
    _until(lambda: server.messages[-1:] == ["a"])
    assert len(server.conns) == 2

    # Wavefront restarted: a new port in the file, followed
    server.close()
    new = Server(f)
    link.send("b")
    _until(lambda: new.messages == ["b"])
    assert link.port == new.port

    # closed: not reachable, said at once (no 2 s Windows retry, no second try)
    new.close()
    _until(lambda: not link._alive())
    t = time.time()
    with pytest.raises(LinkError, match=rf"Wavefront IV not reachable over TCP \(port {new.port}\)"):
        link.send("c")
    assert time.time() - t < 1.5
    f.unlink()
    with pytest.raises(LinkError, match="is Wavefront IV running"):
        link.check()
    link.close()
