"""Wavefront IV over TCP (Oriole TCP), as tried on the rig.

With "Tcp server" ticked in Wavefront's script engine (Tools → Script engine; one source at a time with "auto-load"
and "Simple file", not remembered across restarts), every message is run as a script: the same JavaScript as a
test.js in its script folder. A message is a Qt QDataStream QByteArray, as arduinoReader sends it (captured on the
rig): its length (4 bytes, big-endian), then the UTF-8 script. Its server opens by itself at every start, listening on
every address, on a new port written after its IP in %TEMP%\\blueBoxServer. The file can outlive Wavefront: a refused
connection then says it is not running.

As found on the rig (2026-10-25):
* Wavefront ignores a message identical to the one before it: its server keeps the last one (property clientMsg) and
  passes it on only when it changes. So every script is made unique (wrap: its id in it).
* It can answer: the script object (global ``scriptEngine``) has the server among its children, whose
  broadcastMessage(QString) sends a QDataStream QString (its length in bytes, then UTF-16, big-endian) to every client.
  So every script ends saying "ORIOLE DONE <id>", or "ORIOLE ERROR <id> <error>": what ran, and when it ended.
* One connection, kept: Wavefront (Qt 4) runs a script inside the read of its connection, and Qt reads that connection
  again only once the script has ended. A script sent on a second connection while one ran crashed it (QtScript4.dll).
  Oriole sends a script only once the last one answered (or never will).
* A connection made before Tcp server was ticked runs scripts once it is.
"""
from __future__ import annotations

import socket
import struct
import tempfile
import time
from pathlib import Path

PORT_FILE = Path(tempfile.gettempdir()) / "blueBoxServer"
CONNECT_S = 0.5             # Wavefront on this PC accepts at once; a refused connection takes 2 s to fail on Windows
TIMEOUT_S = 2.0
TCP_HINT = 'tick "Tools → Script engine → Tcp server" in Wavefront IV'
PING_WAIT_S = 2.0           # a ping not answered this long: Wavefront does not run scripts (Tcp server not ticked)
PING_EVERY_S = 3.0          # ... pinged again this often until it does
TICK_WAIT_S = 5.0           # the script ticking Tcp server, dropped in the script folder, not answered this long: not taken
LOAD_WAIT_S = 5.0           # a Load not answered this long: not done
REPLY = "ORIOLE"            # what Oriole's scripts answer: ORIOLE DONE <id> / ORIOLE ERROR <id> <error>
_ANSWER = ('function __oriole(m) { var k = scriptEngine.findChildren(); for (var i = 0; i < k.length; i++) '
           '{ if (typeof k[i].broadcastMessage == "function") { k[i].broadcastMessage(m); return; } } }\n')
# Ticks Tcp server in Wavefront's Script engine dialog, as a click would (the three sources are exclusive): a script
# run from the script folder while Wavefront is in auto-load mode, as it starts with [scriptEngine] autoload=true
TICK_JS = ('var k = scriptEngine.getUi().findChildren(); for (var i = 0; i < k.length; i++) '
           '{ if (k[i].objectName == "tcpServerQckx" && !k[i].checked) { k[i].click(); } }')


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


def wrap(js: str, sid: int) -> str:
    """``js`` as script ``sid``: answering ORIOLE DONE <sid> once it ran, ORIOLE ERROR <sid> <error> if it failed."""
    return (f"{_ANSWER}try {{\n{js}\n__oriole(\"{REPLY} DONE {sid}\");\n}} catch (e) "
            f"{{ __oriole(\"{REPLY} ERROR {sid} \" + e); }}\n")


def read_answers(buf: bytes) -> tuple[list[str], bytes]:
    """The QDataStream QStrings complete in ``buf`` (Wavefront's broadcastMessage), and what is left of it."""
    texts = []
    while len(buf) >= 4:
        n = struct.unpack(">I", buf[:4])[0]
        if n == 0xFFFFFFFF:                             # a null QString
            texts.append("")
            buf = buf[4:]
            continue
        if len(buf) < 4 + n:
            break
        texts.append(buf[4:4 + n].decode("utf-16-be", "replace"))
        buf = buf[4 + n:]
    return texts, buf


def parse_answer(text: str) -> tuple[str, int, str] | None:
    """("DONE" | "ERROR", id, error) from an answer of Oriole's scripts; None for anything else."""
    parts = text.split(" ", 3)
    if len(parts) < 3 or parts[0] != REPLY or parts[1] not in ("DONE", "ERROR") or not parts[2].isdigit():
        return None
    return parts[1], int(parts[2]), parts[3] if len(parts) > 3 else ""


class Wavefront4Link:
    """The connection to Wavefront IV's TCP server: made when first needed, made again when it broke or Wavefront
    restarted (a new port in the file). Scripts go wrapped (wrap), each with an id of its own; their answers are read
    by answers()."""

    def __init__(self, port_file=None):
        self.port_file = port_file
        self.port: int | None = None
        self.connections = 0                        # connections made so far
        self._sock: socket.socket | None = None
        self._buf = b""                             # what came, not a whole QString yet
        self._texts: list[str] = []                 # what came, whole, not read by answers() yet
        self._next_id = int(time.time() * 1000) % 10**9     # unique across Oriole's runs too

    def script(self, js: str) -> tuple[int, str]:
        """``js`` as a new script (wrap), and its id: also for one that goes another way (the script folder); its
        answer still comes here, to every client."""
        self._next_id += 1
        return self._next_id, wrap(js, self._next_id)

    def send(self, js: str) -> int:
        """Send ``js`` as a new script: its id, or LinkError when Wavefront IV cannot be reached."""
        sid, script = self.script(js)
        data = frame(script)
        for attempt in (1, 2):                     # a connection found dead while sending is made again once
            sock = self._connected()
            try:
                sock.sendall(data)
                return sid
            except OSError as e:
                self.close()
                if attempt == 2:
                    raise LinkError(f"Wavefront IV not reachable over TCP (port {self.port}): {e}") from e

    def answers(self) -> list[tuple[str, int, str]]:
        """The answers of Oriole's scripts arrived since the last call, (DONE | ERROR, id, error); without waiting."""
        if self._sock is not None and not self._drain():
            self.close()
        texts, self._texts = self._texts, []
        return [a for a in map(parse_answer, texts) if a is not None]

    def close(self):
        """Hang up; the answers already arrived are kept for answers()."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock, self._buf = None, b""

    def _drain(self) -> bool:
        """Read what Wavefront sent, without waiting (its answers kept for answers()): False when the connection is
        closed or broken. Read before every send too: answers left unread would make a closed connection look open."""
        alive = True
        try:
            self._sock.setblocking(False)
            while True:
                data = self._sock.recv(65536)
                if not data:                            # closed by Wavefront
                    alive = False
                    break
                self._buf += data
        except BlockingIOError:
            pass
        except OSError:
            alive = False
        finally:
            self._sock.settimeout(TIMEOUT_S)
        texts, self._buf = read_answers(self._buf)
        self._texts += texts
        return alive

    def _connected(self) -> socket.socket:
        port = server_port(self.port_file)
        if self._sock is not None and (port != self.port or not self._drain()):
            self.close()                            # Wavefront restarted, or closed the connection
        if self._sock is None:
            self.port = port
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=CONNECT_S)
            except OSError as e:
                raise LinkError(f"Wavefront IV not reachable over TCP (port {port}): {e}") from e
            self._sock.settimeout(TIMEOUT_S)
            self.connections += 1
        return self._sock
