import re
import socket
import struct
import threading
import time

import pytest

from oriole.tcplink import (TICK_JS, LinkError, Wavefront4Link, frame, parse_answer, read_answers, server_port,
                            wrap)


def _until(condition, timeout=2.0):
    end = time.time() + timeout
    while not condition():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


def _qstring(text: str) -> bytes:
    """A QString as Wavefront's broadcastMessage writes it: its length in bytes, then UTF-16, big-endian."""
    data = text.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


class Server:
    """Wavefront IV's TCP server as Oriole sees it: its port written in the port file, the messages read. As found on
    the rig: a message identical to the one before is ignored; one that runs answers, to every client, what its
    __oriole(...) calls say (``runs``: Tcp server ticked)."""

    def __init__(self, port_file):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        port_file.write_text(f"172.17.19.37\n{self.port}")         # as Wavefront writes it: its IP, then the port
        self.messages, self.ran, self.conns, self.runs, self.last = [], [], [], True, None
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
                js, buf = buf[4:4 + n].decode("utf-8"), buf[4 + n:]
                self.messages.append(js)
                if js == self.last:                                 # its clientMsg did not change: not run
                    continue
                self.last = js
                if self.runs:
                    self.ran.append(js)
                    done = re.search(r'__oriole\("(ORIOLE DONE \d+)"\);', js)
                    self.broadcast(done.group(1) if done else "something else")

    def broadcast(self, text: str):
        for conn in self.conns:
            try:
                conn.sendall(_qstring(text))
            except OSError:
                pass

    def drop_connections(self):
        for conn in self.conns:
            conn.close()

    def close(self):
        self.sock.close()
        self.drop_connections()


def test_frame_is_a_qdatastream_qbytearray():
    assert frame("cleanAllBlob();") == b"\x00\x00\x00\x0fcleanAllBlob();"
    assert frame("µ") == b"\x00\x00\x00\x02\xc2\xb5"                  # its UTF-8 bytes counted


def test_scripts_are_wrapped_to_answer_and_answers_read():
    js = wrap("cleanAllBlob();", 7)
    assert "cleanAllBlob();" in js and '__oriole("ORIOLE DONE 7");' in js and '__oriole("ORIOLE ERROR 7 " + e);' in js
    assert js.index("cleanAllBlob();") < js.index("ORIOLE DONE 7")    # done once it ran
    assert "broadcastMessage" in js and wrap("cleanAllBlob();", 8) != js
    assert "tcpServerQckx" in TICK_JS and "click()" in TICK_JS

    # QStrings as Wavefront writes them, whole or cut anywhere; anything not Oriole's left out
    stream = _qstring("ORIOLE DONE 7") + _qstring("hello") + _qstring("ORIOLE ERROR 8 ReferenceError: x") + b"\x00\x00"
    texts, rest = read_answers(stream)
    assert texts == ["ORIOLE DONE 7", "hello", "ORIOLE ERROR 8 ReferenceError: x"] and rest == b"\x00\x00"
    texts, rest = read_answers(stream[:10])
    assert texts == [] and rest == stream[:10]
    assert read_answers(b"\xff\xff\xff\xff")[0] == [""]                 # a null QString
    assert [parse_answer(t) for t in texts + ["ORIOLE DONE 7", "ORIOLE ERROR 8 ReferenceError: x", "hello",
                                              "ORIOLE DONE x"]] == [("DONE", 7, ""), ("ERROR", 8, "ReferenceError: x"),
                                                                    None, None]


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


def test_link_scripts_answer_even_when_the_same(tmp_path):
    f = tmp_path / "blueBoxServer"
    server = Server(f)
    link = Wavefront4Link(f)
    got = []

    def answered(sid):
        _until(lambda: got.extend(link.answers()) or any(a[1] == sid for a in got))
        return [a for a in got if a[1] == sid]

    mask = 'drawSpot(1.0000,2.0000,10.0000);\nproceedPhp();\n'
    a, b = link.send(mask), link.send(mask)                 # the same script twice: two scripts, both run
    assert answered(a) == [("DONE", a, "")] and answered(b) == [("DONE", b, "")] and a != b
    assert len(server.ran) == 2 and len(server.conns) == 1  # one connection kept

    # Tcp server not ticked: nothing runs, nothing answers
    server.runs = False
    c = link.send("")
    time.sleep(0.2)
    assert not [x for x in link.answers() if x[1] == c]
    server.runs = True
    d = link.send("")
    assert answered(d) == [("DONE", d, "")]

    # a script that goes another way (the script folder) answers here too
    e, js = link.script(TICK_JS)
    server.broadcast(re.search(r'__oriole\("(ORIOLE DONE \d+)"\);', js).group(1))
    assert answered(e) == [("DONE", e, "")]
    link.close()
    server.close()


def test_link_reconnects_and_follows_a_restart(tmp_path):
    f = tmp_path / "blueBoxServer"
    server = Server(f)
    link = Wavefront4Link(f)
    link.send("a")
    _until(lambda: len(server.messages) == 1)
    assert link.connections == 1

    # Wavefront closed the connection: made again, nothing lost
    server.drop_connections()
    _until(lambda: not link._drain())
    link.send("b")
    _until(lambda: len(server.messages) == 2)
    assert len(server.conns) == 2 and link.connections == 2

    # Wavefront restarted: a new port in the file, followed
    server.close()
    new = Server(f)
    link.send("c")
    _until(lambda: len(new.messages) == 1)
    assert link.port == new.port

    # closed, its port file left behind: not reachable, said at once (no 2 s Windows retry, no second try)
    new.close()
    _until(lambda: not link._drain())
    t = time.time()
    with pytest.raises(LinkError, match=rf"Wavefront IV not reachable over TCP \(port {new.port}\)"):
        link.send("d")
    assert time.time() - t < 1.5
    assert {kind for kind, _, _ in link.answers()} <= {"DONE"}         # answers come before it closed: kept
    f.unlink()
    with pytest.raises(LinkError, match="is Wavefront IV running"):
        link.send("e")
    link.close()
