import os
from pathlib import Path

import pytest

from oriole import wavefront
from oriole.wavefront import install_problem, other_installs, prepare, read_ini, write_ini

# as Wavefront (Qt 4 QSettings) writes %APPDATA%\wfdIV\wfdIV.ini: CRLF, backslashes doubled
QT_INI = ("[config]\r\nalgo\\scriptEngine=true\r\n\r\n[scriptEngine]\r\nautoload=false\r\n\r\n"
          "[mainSettings]\r\nrootPath=E:\\\\wfdIV_20241023\\\\GeneratedPhasemasks\r\n\r\n"
          "[bckgnd]\r\noffset=@Point(19 17)\r\n")


def _install(folder: Path, core=b"core") -> Path:
    folder.mkdir(parents=True)
    (folder / "wfdIV.exe").write_bytes(b"launcher")
    (folder / "wfdIVCore.dll").write_bytes(core)
    for name in ("script", "GeneratedPhasemasks"):
        (folder / name).mkdir()
    return folder


def test_install_problems_and_other_installs(tmp_path):
    good = _install(tmp_path / "wfdIV_20261022")
    expired = _install(tmp_path / "wfdIV_20241023", core=b"")             # the core left empty: licence expired
    bare = tmp_path / "wfdIV_bare"
    bare.mkdir()
    assert install_problem(good) is None
    assert "wfdIVCore.dll is missing or empty" in install_problem(expired)
    assert "Wavefront IV Errorcode 7536725.pdf" in install_problem(expired)
    assert install_problem(bare) == f"no wfdIV.exe in {bare}"
    assert other_installs(expired) == [good] and other_installs(good) == []
    fresh = tmp_path / "wfdIV_fresh"                    # just extracted from the zip: no mask folder
    fresh.mkdir()
    (fresh / "wfdIV.exe").write_bytes(b"launcher")
    (fresh / "wfdIVCore.dll").write_bytes(b"core")
    assert install_problem(fresh) == f"no GeneratedPhasemasks folder in {fresh}: make it"
    (fresh / "GeneratedPhasemasks").mkdir()
    assert install_problem(fresh) is None               # Oriole TCP needs no script folder


def test_settings_written_as_qsettings_every_other_line_kept(tmp_path):
    ini = tmp_path / "wfdIV.ini"
    ini.write_bytes(QT_INI.encode("latin-1"))
    assert read_ini("mainSettings", "rootPath", ini) == r"E:\wfdIV_20241023\GeneratedPhasemasks"
    assert read_ini("scriptEngine", "autoload", ini) == "false" and read_ini("nope", "x", ini) is None
    assert prepare(r"E:\wfdIV_20261022", ini) == ["mainSettings/rootPath"]      # the script source left alone
    assert ini.read_bytes().decode("latin-1") == QT_INI.replace("20241023", "20261022")
    before = ini.read_bytes()
    assert prepare(r"E:\wfdIV_20261022", ini) == [] and ini.read_bytes() == before     # nothing to change
    # a key or a section missing: added (a value with a comma is quoted, as QSettings does)
    assert write_ini({("bckgnd", "workingPath"): "C:/Users/S8", ("new", "k"): 'a, "b"'}, ini) == [
        "bckgnd/workingPath", "new/k"]
    text = ini.read_bytes().decode("latin-1")
    assert "offset=@Point(19 17)\r\nworkingPath=C:/Users/S8\r\n" in text
    assert text.endswith('\r\n\r\n[new]\r\nk="a, \\"b\\""\r\n') and read_ini("new", "k", ini) == 'a, "b"'
    # no settings yet (Wavefront never started on this PC): made
    fresh = tmp_path / "fresh" / "wfdIV.ini"
    assert prepare(tmp_path / "wfd", fresh) == ["mainSettings/rootPath"]
    assert read_ini("scriptEngine", "autoload", fresh) is None
    assert Path(read_ini("mainSettings", "rootPath", fresh)) == tmp_path / "wfd" / "GeneratedPhasemasks"


@pytest.mark.skipif(not wavefront.SUPPORTED, reason="Windows only")
def test_processes_and_windows_of_this_pc():
    found = wavefront.running()                     # whatever runs on this PC: only Wavefront's, with their folder
    assert set(found) <= {"launcher", "core"} and all(isinstance(pid, int) for pid, _ in found.values())
    assert not wavefront.window_open(os.getpid())   # pytest shows no Wavefront window
