"""Oriole window: visual (DMD) on the left, the image to click spots on in the middle, holo (SLM) on the right.

Look and dark theme taken from Plerion; the holo side follows the MATLAB DHgui. Wavefront IV gets one script at a
time (the phase masks of a generation, a Load, or a check of its script mode), polled every 100 ms: a script it does
not take, or a mask it does not save, is erased and sent again, MAX_ATTEMPTS sends in all. Images come from a file or
from Micro-Manager (camera.Feed).
"""
from __future__ import annotations

import datetime as _dt
import html
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from . import camera, holo, visual, wavefront
from .calibration import EPI_SIZE
from .config import DEFAULT_CONFIG_PATH, Config
from .images import load_gray
from .shutter import Shutter, ShutterError
from .theme import (ACTION_STYLE, BLOCKED_STYLE, BTN, DIM, HOLO_COLOUR, IMAGE_COLOUR, INNER_CIRCLE,
                    LOG_COLOURS, NO_GO, OUTER_CIRCLE, PANEL_BG, SLM_RING, STOP_STYLE, TEXT, VISUAL_COLOUR, WARN,
                    apply_dark_theme)
from .widgets import (CalibrationBox, FolderEdit, ImageView, action_button, button, card, help_button, icon_button,
                      spin_box, title_label)

LOG_PATH = DEFAULT_CONFIG_PATH.parent / "oriole.log"   # tracebacks of internal errors
AUTOLOAD_HINT = 'check that "Tools → Script engine → auto load (script folder)" is selected in Wavefront'
NOT_TAKEN_FIX = f"{AUTOLOAD_HINT}, or restart Wavefront IV"
NOT_SAVED_FIX = "restart Wavefront IV and generate again"
START_TIMEOUT_S = 20.0          # Wavefront started by Oriole, its program still not running after this long
HELP_HTML = f"""
<h3>Spots</h3>
<p><b>Load image…</b> an epi image binned 2×2, {EPI_SIZE[0]} × {EPI_SIZE[1]} px (any other size: big red
warning), or take it from Micro-Manager: <b>Snap</b> (one image at {camera.EXPOSURE_MS:.0f} ms, binning 2×2) or
<b>Live</b> (one after the other; the frames of Micro-Manager's own live mode while it runs). Micro-Manager must be
running with Tools → Options → <i>Run pycro-manager server on port {camera.PORT}</i> ticked.<br>
<b>Left click</b> adds a spot, <b>drag</b> a spot to move it, <b>right click</b> on a spot removes it.
<b>Undo</b> / <b>Clear</b>. <b>Spot Ø</b> is the diameter of every spot on the sample (µm).</p>
<h3>Calibrations (typed by hand, one per side)</h3>
<p>µm = R(−rotation) · diag(stretch X, stretch Y) · (pixel + 0.5 − image centre) + (X, Y)<br>
Stretches are µm per camera pixel; a negative stretch mirrors. Holo default = DHparams.m
(X 5, Y −8, stretch 0.67 / −0.67). The line under the image shows the mouse in both.</p>
<h3>Visual · DMD</h3>
<p><b>EXTRACT BIN</b> writes <code>YYYY_MM_DD_&lt;N&gt;Frames.bin</code>: for spot i (from 1), frame 2(i−1) is the
spot and 2(i−1)+1 its negative; a <code>.json</code> lists the frames.<br>
<b>Merge into bin…</b> appends it to a linked bin (inputs untouched; <code>.indices.json</code> gives the
new frame numbers).</p>
<h3>Holo · SLM</h3>
<p><b>Wavefront folder</b>: the Wavefront IV install (<code>wfdIV.exe</code>); scripts go to its
<code>script\\</code>, masks are read from its <code>GeneratedPhasemasks\\</code>; 🧹 sweeps a folder clean, 📂 opens
the phase mask folder in the file explorer. After a new install (its licence expired: the <b>?</b> next to it
says how), pick its folder.<br>
Under it, Wavefront's state. <b>Start</b> starts it from that folder in script mode (set in its settings,
<code>%APPDATA%\\wfdIV\\wfdIV.ini</code>, with the folder it saves masks in); Oriole does so itself when it opens
(<b>start Wavefront with Oriole</b>). <b>Check</b> sends it a script that does nothing: taken, Wavefront is in script
mode. Oriole never closes Wavefront.<br>
On the image, through the holo calibration, Wavefront's field as Wavefront draws it: its square in
<span style="color:{NO_GO}">red</span>, the image outside it hatched red (no-go zone: Wavefront cannot put a spot
there), its 20 % diffraction efficiency circle in <span style="color:{OUTER_CIRCLE}">orange</span> dashes, its 90 %
one in <span style="color:{INNER_CIRCLE}">yellow</span>, its centre cross in light grey; <b>SLM field on the image</b>
(holo Settings) shows or hides it. With spots (partly) outside the field, Generate first asks: <b>Remove spots and
generate</b> (Undo brings them back), <b>Generate anyway</b> (their masks hold no spot, or a cut one; big red
warning in the log) or <b>Cancel</b> (a right click removes a spot).<br>
<b>GENERATE PHASE MASKS</b> has Wavefront save one mask per spot, <code>Pattern1…N</code>, each script
emptying Wavefront's pattern list first (a pattern left there, made by hand or loaded, would stop every mask from
being saved), each mask checked against the spot Wavefront drew for it (<code>rawSrc.png</code>).
It is <span style="color:{WARN}">yellow</span> while the script
or phase mask folder is not empty: it then warns and sweeps them only if you say so. While it runs it is a red
<b>STOP</b>.<br>
A script Wavefront does not take within {holo.NOT_READ_S:.0f} s, or a mask it does not save within
{holo.MASK_WAIT_S:.0f} s, is erased and sent again (attempt 1/{holo.MAX_ATTEMPTS}, 2/{holo.MAX_ATTEMPTS}…); at
{holo.MAX_ATTEMPTS}/{holo.MAX_ATTEMPTS} the generation is aborted. After STOP or an abort Oriole waits
{holo.ABORT_WAIT_S:.0f} s (Wavefront may still be computing), then sweeps both Wavefront folders, again every second
until they are empty: ready to generate again (check Tools → Script engine → auto load (script folder), or restart
Wavefront).<br>
<b>Manual</b>: pick a mask number and <b>Load</b> it onto the SLM (a script not taken is erased and sent again,
{holo.MAX_ATTEMPTS} attempts): its spot gets a
<span style="color:{SLM_RING}">purple SLM ring</span> on the image (where it was clicked, from
<code>Pattern_spots.json</code>); <b>Open</b> / <b>Close</b> the shutter (close it before a Plerion protocol).</p>
<p>Everything typed is saved in <code>~/.oriole/config.json</code>.</p>
"""


def reinstall_html(wfd_root: str) -> str:
    """How to install Wavefront IV again when its licence expired: the procedure on the desktop, step by step."""
    new = Path(wfd_root).parent / f"wfdIV_{_dt.date.today():%Y%m%d}"

    def link(path: Path, text: str) -> str:
        if not path.exists():
            return f"{text} (not found on this PC: {html.escape(str(path))})"
        return f'<a href="{QtCore.QUrl.fromLocalFile(str(path)).toString()}">{text}</a>'
    return f"""
<h3>When Wavefront IV breaks</h3>
<p>Its licence expires now and then: Wavefront does not start any more (<i>Failed to launch WfdIVCore, error code
7536725</i>), or its <code>wfdIVCore.dll</code> is left empty (Oriole says so in red). Install it again in a new
folder, as in {link(wavefront.LICENCE_PDF, "the procedure on the desktop")}:</p>
<ol>
<li><b>Extract the zip</b> <code>{html.escape(wavefront.INSTALL_ZIP.name)}</code>
({link(wavefront.INSTALL_ZIP.parent, "its folder")}), then rename the <code>wfdIV</code> folder it holds after
today, next to the others: <code>{html.escape(str(new))}</code>.</li>
<li><b>Make the folders</b> <code>script</code> and <code>GeneratedPhasemasks</code> in it (the zip has neither).</li>
<li><b>Make the log file</b>: double-click <code>log.bat</code> in it. It writes the file <code>log</code> (a small box
says "log": OK).</li>
<li><b>Licence</b>: drag the file <code>log</code> onto <code>wfdIV.exe</code>. A window asks for a user and a
password: click in the password box, then press <b>Ctrl</b>, <b>A</b> and <b>R</b> together. Wavefront starts:
close it (it asks to confirm), as it still saves its masks in the old folder.</li>
<li><b>In Oriole</b>: pick the new folder as the Wavefront folder (<b>…</b>), then press <b>Start</b>. Oriole starts
Wavefront from it in script mode, saving the masks in its <code>GeneratedPhasemasks</code>.</li>
</ol>
<p>Still not starting: Vincent De Sars (Vincent.de-sars@inserm.fr), as the procedure says. The masks made before stay
in the old folder's <code>GeneratedPhasemasks</code>.</p>
"""


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _names(paths) -> str:
    return ", ".join(p.name for p in paths)


def _same_folder(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@dataclass
class Job:
    """A script for Wavefront: a phase mask to compute ("mask": ``png`` its file, for spot ``spot``), a saved one to
    show ("load"), or one doing nothing, to see that Wavefront takes scripts ("check")."""
    kind: str
    name: str                               # Pattern<n>
    js: str
    png: Path | None = None
    spot: int = 0
    attempt: int = 1                        # sends so far
    sent: float = 0.0                       # time.time() of the last send
    taken: float | None = None              # when Wavefront took it

    @property
    def what(self) -> str:
        return {"mask": f"{self.name} script", "load": f"script loading {self.name}", "check": "check script"}[
            self.kind]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: Config, config_path=DEFAULT_CONFIG_PATH):
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.image: np.ndarray | None = None
        self.image_source = ""
        self.spots: list[tuple[float, float]] = []      # camera px
        self.undo_stack: list[list[tuple[float, float]]] = []
        self.dragging: int | None = None                # spot being dragged
        self.dialogs: dict[str, QtWidgets.QDialog] = {}   # help pages, by title
        self.last_spot_bin: Path | None = None
        self.slm_spot: dict | None = None               # where the mask loaded on the SLM was clicked
        self.shutter = Shutter(config.nidaq_device, config.shutter_ao)
        self.jobs: list[Job] = []                       # for Wavefront, the first one sent: masks, a Load or a check
        self.generation: dict | None = None             # the spots of the masks being generated
        self.cleanup: dict | None = None                # after an abort: both folders swept once Wavefront is done
        self.wf_folder: Path | None = None              # install the running Wavefront started from
        self.wf_ready = False                           # its window is open: it takes scripts (in script mode)
        self.wf_mode: bool | None = None                # in script mode, as last seen (None: not known)
        self.wf_started: float | None = None            # when Oriole started it, until it is ready
        self.feed = camera.Feed(parent=self)
        self.feed.frame.connect(self._on_frame)
        self.feed.failed.connect(self._on_camera_failed)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.wf_timer = QtCore.QTimer(self)             # Wavefront's state, every second
        self.wf_timer.setInterval(1000)
        self.wf_timer.timeout.connect(self._watch_wavefront)
        self.wf_check = QtCore.QTimer(self)             # its script mode, checked 2 s after it is ready
        self.wf_check.setSingleShot(True)
        self.wf_check.setInterval(2000)
        self.wf_check.timeout.connect(self._check_when_idle)
        self.setWindowTitle("Oriole")
        self.resize(1500, 900)
        self._build()
        self.redraw_spots()
        self.refresh_masks()
        self.log("Oriole ready: load an image, click spots (drag to move, right-click removes; ? for help).")
        if config.load_error:
            self.log(config.load_error, "error")
        if wavefront.SUPPORTED:
            self._watch_wavefront()
            self.wf_timer.start()
            if config.start_wavefront and self.wf_folder is None:
                QtCore.QTimer.singleShot(0, self.start_wavefront)

    # ------------------------------------------------------------------ layout
    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(10)
        root.addLayout(cols, stretch=1)
        cols.addWidget(card(self._build_visual(), "visualCard", VISUAL_COLOUR, 290))
        cols.addWidget(card(self._build_image(), "imageCard", IMAGE_COLOUR), stretch=1)
        cols.addWidget(card(self._build_holo(), "holoCard", HOLO_COLOUR, 290))
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFixedHeight(130)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        self.log_view.setStyleSheet("background: #0A0A0A; color: #00FF00; border: none;")
        root.addWidget(self.log_view)
        self.watcher = QtCore.QFileSystemWatcher(self)        # mask list and button follow the folders
        self.watcher.directoryChanged.connect(lambda _: self.refresh_masks())
        self._watch_folders()

    def _build_visual(self) -> QtWidgets.QVBoxLayout:
        col = QtWidgets.QVBoxLayout()
        col.addWidget(title_label("VISUAL · DMD", VISUAL_COLOUR))
        dmd = QtWidgets.QGroupBox("Settings")
        form = QtWidgets.QFormLayout(dmd)
        self.spin_rows = spin_box(self.config.dmd_height, 8, 4096, " px", slot=self._on_dmd)
        self.spin_cols = spin_box(self.config.dmd_width, 8, 4096, " px", slot=self._on_dmd)
        self.spin_dmd_px = spin_box(self.config.pixel_size_um, 0.01, 100, " µm", 3, slot=self._on_dmd)
        form.addRow("rows", self.spin_rows)
        form.addRow("columns", self.spin_cols)
        form.addRow("pixel size", self.spin_dmd_px)
        col.addWidget(dmd)
        self.visual_box = CalibrationBox(self.config.visual)
        self.visual_box.changed.connect(self._on_calibration)
        col.addWidget(self.visual_box)
        col.addStretch(1)
        self.btn_extract = action_button("EXTRACT BIN", self.extract)
        self.btn_merge = button("Merge into bin…", self.merge)
        col.addWidget(self.btn_extract)
        col.addWidget(self.btn_merge)
        return col

    def _build_image(self) -> QtWidgets.QVBoxLayout:
        col = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(button("Load image…", self.load_image_dialog))
        mm = f"Micro-Manager ({camera.EXPOSURE_MS:.0f} ms, binning 2x2; its server on port {camera.PORT})"
        self.btn_snap = button("Snap", self.snap, f"One image from {mm}")
        self.btn_live = button("Live", lambda: None, f"Images from {mm}, one after the other",
                               "QPushButton:checked { background: #8B1A1A; color: white; }")
        self.btn_live.setCheckable(True)
        self.btn_live.toggled.connect(self._on_live)
        bar.addWidget(self.btn_snap)
        bar.addWidget(self.btn_live)
        bar.addWidget(QtWidgets.QLabel("  Spot Ø"))
        self.spin_diam = spin_box(self.config.spot_diameter_um, 0.5, 2000, " µm", 1, slot=self._on_diameter)
        bar.addWidget(self.spin_diam)
        bar.addWidget(button("Undo", self.undo))
        bar.addWidget(button("Clear", self.clear))
        bar.addStretch(1)
        self.lbl_count = QtWidgets.QLabel()
        bar.addWidget(self.lbl_count)
        self.btn_help = help_button(self.show_help, "Help")
        bar.addWidget(self.btn_help)
        col.addLayout(bar)
        self.view = ImageView()
        self.view.vb.clicked.connect(self._on_click)
        self.view.vb.dragged.connect(self._on_drag)
        self.view.mouseMoved.connect(self._on_mouse)
        self.view.show_placeholder()
        col.addWidget(self.view, stretch=1)
        self.lbl_mouse = QtWidgets.QLabel(" ")
        self.lbl_mouse.setStyleSheet(f"color: {DIM};")
        col.addWidget(self.lbl_mouse)
        return col

    def _build_holo(self) -> QtWidgets.QVBoxLayout:
        col = QtWidgets.QVBoxLayout()
        col.addWidget(title_label("HOLO · SLM", HOLO_COLOUR))
        setup = QtWidgets.QGroupBox("Settings")
        box = QtWidgets.QVBoxLayout(setup)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Wavefront folder"))
        row.addStretch(1)
        self.btn_reinstall_help = help_button(self.show_reinstall_help, "Wavefront broken (licence expired)? How to "
                                                                        "install it again", 20)
        row.addWidget(self.btn_reinstall_help)
        box.addLayout(row)
        self.edit_wfd_root = FolderEdit(self.config.wfd_root)
        self.edit_wfd_root.setToolTip("the Wavefront IV install: wfdIV.exe, script\\ and GeneratedPhasemasks\\")
        self.edit_wfd_root.changed.connect(self._on_wavefront_root)
        box.addWidget(self.edit_wfd_root)
        row = QtWidgets.QHBoxLayout()
        self.lbl_wavefront = QtWidgets.QLabel()
        self.lbl_wavefront.setWordWrap(True)
        self.btn_wavefront = button("Start", self.start_or_check_wavefront)
        self.btn_wavefront.setFixedWidth(60)
        row.addWidget(self.lbl_wavefront, stretch=1)
        row.addWidget(self.btn_wavefront)
        box.addLayout(row)
        self.btn_sweep_scripts = icon_button("🧹", self.sweep_script_folder, "Sweep the script folder clean")
        self.lbl_script_dir = self._folder_row(box, "script folder", self.btn_sweep_scripts)
        self.btn_open_masks = icon_button("📂", self.open_mask_folder, "Open it in the file explorer")
        self.btn_sweep_masks = icon_button("🧹", self.sweep_mask_folder, "Sweep the phase mask folder clean (asks "
                                                                         "first)")
        self.lbl_output_dir = self._folder_row(box, "phase mask folder", self.btn_open_masks, self.btn_sweep_masks)
        self._show_wavefront_dirs()
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("shutter"))
        self.edit_device = QtWidgets.QLineEdit(self.config.nidaq_device)
        self.edit_device.editingFinished.connect(self._on_shutter_output)
        self.spin_ao = spin_box(self.config.shutter_ao, 0, 31, slot=self._on_shutter_output)
        self.spin_ao.setPrefix("ao")
        row.addWidget(self.edit_device, stretch=1)
        row.addWidget(self.spin_ao)
        box.addLayout(row)
        self.chk_start_wavefront = QtWidgets.QCheckBox("start Wavefront with Oriole")
        self.chk_start_wavefront.setToolTip("when Oriole opens and Wavefront is not running, start it in script mode")
        self.chk_start_wavefront.setChecked(self.config.start_wavefront)
        self.chk_start_wavefront.toggled.connect(self._on_start_wavefront)
        box.addWidget(self.chk_start_wavefront)
        self.chk_slm_field = QtWidgets.QCheckBox("SLM field on the image")
        self.chk_slm_field.setToolTip("Wavefront's square, the no-go zone outside it, its 20 % and 90 % power circles "
                                      "and its centre")
        self.chk_slm_field.setChecked(self.config.show_slm_field)
        self.chk_slm_field.toggled.connect(self._on_slm_field)
        box.addWidget(self.chk_slm_field)
        col.addWidget(setup)
        self.lbl_image_warning = QtWidgets.QLabel()
        self.lbl_image_warning.setWordWrap(True)
        self.lbl_image_warning.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_image_warning.setStyleSheet("background: #B00000; color: white; font: bold 14pt 'Segoe UI';"
                                             "padding: 8px; border-radius: 4px;")
        self.lbl_image_warning.hide()
        col.addWidget(self.lbl_image_warning)
        self.holo_box = CalibrationBox(self.config.holo)
        self.holo_box.changed.connect(self._on_calibration)
        col.addWidget(self.holo_box)
        manual = QtWidgets.QGroupBox("Manual")
        grid = QtWidgets.QGridLayout(manual)
        grid.addWidget(QtWidgets.QLabel("mask"), 0, 0)
        self.combo_mask = QtWidgets.QComboBox()
        self.combo_mask.setToolTip("Pattern<n> masks found in the phase mask folder")
        grid.addWidget(self.combo_mask, 0, 1)
        self.btn_load_mask = button("Load", self.load_selected_mask)
        grid.addWidget(self.btn_load_mask, 0, 2)
        self.lbl_shutter = QtWidgets.QLabel()
        self.lbl_shutter.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_shutter, 1, 0, 1, 3)
        self.btn_open = button("Open", self.open_shutter)
        self.btn_close = button("Close", self.close_shutter)
        shutter_row = QtWidgets.QHBoxLayout()
        shutter_row.addWidget(self.btn_open)
        shutter_row.addWidget(self.btn_close)
        grid.addLayout(shutter_row, 2, 0, 1, 3)
        col.addWidget(manual)
        col.addStretch(1)
        self.btn_generate = action_button("GENERATE PHASE MASKS",
                                          lambda: self.stop_generation() if self.generation else self.generate())
        col.addWidget(self.btn_generate)
        self._show_shutter()
        self._show_wavefront()
        return col

    @staticmethod
    def _folder_row(box: QtWidgets.QVBoxLayout, label: str, *buttons) -> QtWidgets.QLineEdit:
        """Read-only path of a folder inside the Wavefront folder, followed by its buttons."""
        box.addWidget(QtWidgets.QLabel(label))
        row = QtWidgets.QHBoxLayout()
        path = QtWidgets.QLineEdit()
        path.setReadOnly(True)
        path.setStyleSheet("color: #9A9A9A;")
        row.addWidget(path, stretch=1)
        for btn in buttons:
            row.addWidget(btn)
        box.addLayout(row)
        return path

    def _show_wavefront_dirs(self):
        for edit, folder in ((self.lbl_script_dir, self.config.wfd_script_dir),
                             (self.lbl_output_dir, self.config.wfd_output_dir)):
            edit.setText(folder)            # cursor at the end: the sub-folder name stays visible
            edit.setToolTip(folder)

    def show_help(self):
        self._show_page("Oriole help", HELP_HTML, (620, 640))

    def show_reinstall_help(self):
        self._show_page("Installing Wavefront IV again", reinstall_html(self.config.wfd_root), (620, 520))

    def _show_page(self, title: str, page: str, size: tuple[int, int]):
        """A help page in a window of its own (kept open beside the main one); its links open in Windows."""
        dialog = self.dialogs.get(title)
        if dialog is None:
            dialog = self.dialogs[title] = QtWidgets.QDialog(self)
            dialog.setWindowTitle(title)
            dialog.resize(*size)
            layout = QtWidgets.QVBoxLayout(dialog)
            text = QtWidgets.QTextBrowser()
            text.setOpenExternalLinks(True)
            text.setStyleSheet(f"background: {PANEL_BG}; color: {TEXT}; border: none; font: 10pt 'Segoe UI';")
            layout.addWidget(text)
            layout.addWidget(button("Close", dialog.close))
        dialog.findChild(QtWidgets.QTextBrowser).setHtml(page)
        dialog.show()
        dialog.raise_()

    def log(self, text: str, level: str = "info"):
        big = "; font-size: 14pt; font-weight: bold" if level == "alarm" else ""
        self.log_view.appendHtml(f'<span style="color:{LOG_COLOURS[level]}{big}">'
                                 f'{_dt.datetime.now():%H:%M:%S}  {html.escape(text)}</span>')

    def internal_error(self, kind, value, tb):
        """An exception no slot caught (sys.excepthook): logged, its traceback in LOG_PATH, Oriole goes on (PyQt
        would abort it)."""
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n{''.join(traceback.format_exception(kind, value, tb))}")
        except OSError:
            pass
        self.log(f"internal error ({kind.__name__}: {value}), details in {LOG_PATH}; Oriole goes on", "error")

    def _choose(self, title: str, text: str, choices: dict[str, str]) -> str | None:
        """A warning with a button per choice ({key: label}) and Cancel, the default: the key clicked, or None."""
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Warning, title, text, parent=self)
        buttons = {box.addButton(label, QtWidgets.QMessageBox.ButtonRole.AcceptRole): key
                   for key, label in choices.items()}
        box.setDefaultButton(box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel))
        box.exec()
        return buttons.get(box.clickedButton())

    def save_config(self):
        self.config.save(self.config_path)

    # ------------------------------------------------------------------ image + spots
    def load_image_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Microscope image", self.config.last_dir or str(Path.home()),
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)")
        if not path:
            return
        try:
            image = load_gray(path)
        except Exception as e:  # noqa: BLE001
            self.log(f"cannot load {path}: {e}", "error")
            return
        self.config.last_dir = str(Path(path).parent)
        self.set_image(image, path)

    def set_image(self, image: np.ndarray, source: str = "", announce: bool = True):
        """Show a camera image (a file, or from Micro-Manager). ``announce``: log it and check its size (live frames
        do so only when their size changes)."""
        image = np.asarray(image, dtype=np.float32)
        resized = self.image is None or image.shape != self.image.shape
        self.image, self.image_source = image, source
        self.view.set_image(image, fit=resized)
        if resized:
            self.redraw_field()
        if not (announce or resized):
            return
        h, w = image.shape
        self.log(f"image {Path(source).name or 'set'}: {w} x {h} px")
        if (w, h) == EPI_SIZE:
            self.lbl_image_warning.hide()
            return
        ew, eh = EPI_SIZE
        self.lbl_image_warning.setText(f"WRONG IMAGE SIZE\n{w} × {h} px")
        self.lbl_image_warning.show()
        if (w, h) == (2 * ew, 2 * eh):
            why = "This is the unbinned camera size: the epi image was not binned 2x2"
        else:
            why = f"The epi image was probably not binned 2x2 (unbinned it would be {2 * ew} x {2 * eh})"
        self.log(f"WRONG IMAGE SIZE: {w} x {h} px instead of {ew} x {eh}. {why}. Bin it 2x2 and load it "
                 f"again: both calibrations are made for {ew} x {eh} epi images, so spot positions, sizes "
                 f"and everything extracted or generated from this image would be wrong.", "error")

    def snap(self):
        if self.feed.busy:
            return
        self.btn_snap.setEnabled(False)
        self.feed.snap()

    def _on_live(self, on: bool):
        self.feed.set_live(on)
        self.btn_snap.setEnabled(not on and not self.feed.busy)
        self.log("Micro-Manager live: on" if on else "Micro-Manager live: off")

    def _on_frame(self, image, mm_live: bool):
        source = "Micro-Manager live" if self.feed.live or mm_live else "Micro-Manager snap"
        self.set_image(image, source, announce=source != self.image_source or not self.feed.live)
        self.btn_snap.setEnabled(not self.btn_live.isChecked())

    def _on_camera_failed(self, message: str):
        self.log(message, "error")
        self.btn_live.setChecked(False)
        self.btn_snap.setEnabled(True)

    def _on_click(self, x: float, y: float, right: bool):
        if self.image is None:
            self.log("load an image first", "warn")
            return
        h, w = self.image.shape
        k = self._spot_at(x, y) if right else None
        if k is not None:
            self._edit_spots([p for i, p in enumerate(self.spots) if i != k])
        elif not right and 0 <= x < w and 0 <= y < h:
            self._edit_spots(self.spots + [(x, y)])

    def _spot_at(self, x: float, y: float) -> int | None:
        """Index of the spot under (x, y), if any."""
        if not self.spots:
            return None
        d = np.hypot(*(np.asarray(self.spots) - [x, y]).T)
        k = int(d.argmin())
        return k if d[k] <= max(self.diameter_px() / 2, 5) else None

    def _on_drag(self, x: float, y: float, phase: int):
        """Drag a spot with the left button; dragging elsewhere does nothing (the view is fixed)."""
        if self.image is None:
            return
        if phase == 0:
            self.dragging = self._spot_at(x, y)
            if self.dragging is not None:
                self._push_undo()
            return
        if self.dragging is None:
            return
        h, w = self.image.shape
        self.spots[self.dragging] = (min(max(x, 0.0), w - 1e-3), min(max(y, 0.0), h - 1e-3))
        self.redraw_spots()
        if phase == 2:
            self.dragging = None

    def _push_undo(self):
        self.undo_stack.append(list(self.spots))
        del self.undo_stack[:-50]

    def _edit_spots(self, spots):
        """New spots, the old ones kept for Undo."""
        self._push_undo()
        self.spots = spots
        self.redraw_spots()

    def undo(self):
        if self.undo_stack:
            self.spots = self.undo_stack.pop()
            self.redraw_spots()

    def clear(self):
        if self.spots:
            self._edit_spots([])

    def diameter_px(self, diameter_um: float | None = None) -> float:
        px_um = (self.config.visual.px_um + self.config.holo.px_um) / 2
        return (self.spin_diam.value() if diameter_um is None else diameter_um) / max(px_um, 1e-6)

    def redraw_spots(self):
        self.view.set_spots(self.spots, self.diameter_px())
        s = self.slm_spot
        self.view.set_slm_spot(None if s is None else (s["x_px"], s["y_px"]),
                               0.0 if s is None else self.diameter_px(s["diameter_um"]))
        self.lbl_count.setText(f"{_n(len(self.spots), 'spot')}  ")

    def redraw_field(self):
        """Wavefront's field on the image, through the holo calibration: its square in red, the image outside it
        hatched red (no-go zone), its 20 % efficiency circle in orange dashes, its 90 % one in yellow, its centre
        cross in light grey."""
        if self.image is None or not self.config.show_slm_field:
            self.view.set_field(None)
            return
        cal, shape = self.config.holo, self.image.shape
        self.view.set_field([(kind, cal.from_um(pts, shape)) for kind, pts in holo.field_outline()])

    def _on_mouse(self, x: float, y: float):
        if self.image is None:
            return
        over_spot = self.dragging is not None or self._spot_at(x, y) is not None
        self.view.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if over_spot else QtCore.Qt.CursorShape.CrossCursor)
        vx, vy = self.config.visual.to_um([(x, y)], self.image.shape)[0]
        hx, hy = self.config.holo.to_um([(x, y)], self.image.shape)[0]
        self.lbl_mouse.setText(f"x {x:.0f}  y {y:.0f} px    |    visual {vx:.1f}, {vy:.1f} µm    |    "
                               f"holo {hx:.1f}, {hy:.1f} µm")

    def _have_spots(self) -> bool:
        if self.image is None or not self.spots:
            self.log("load an image and click at least one spot first", "warn")
            return False
        return True

    # ------------------------------------------------------------------ visual
    def extract(self):
        if not self._have_spots():
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder for the spot bin",
                                                               self.config.last_dir or str(Path.home()))
        if not directory:
            return
        cal = self.config.visual
        try:
            path = visual.write_spot_bin(directory, self.spots, self.image.shape, self.spin_diam.value(),
                                         cal, self.config)
        except Exception as e:  # noqa: BLE001
            self.log(f"extract failed: {e}", "error")
            return
        self.config.last_dir = directory
        self.last_spot_bin = path
        self.save_config()
        self.log(f"wrote {path} ({len(self.spots)} spots: frame 2i = spot i, 2i+1 = its negative)")
        h, w = self.config.dmd_shape
        centres = visual.dmd_px(cal.to_um(self.spots, self.image.shape), self.config)
        outside = [k + 1 for k, (cx, cy) in enumerate(centres) if not (0 <= cx < w and 0 <= cy < h)]
        if outside:
            self.log(f"spots outside the DMD frame: {', '.join(map(str, outside))}", "warn")

    def merge(self):
        start = self.config.linked_bin or self.config.last_dir or str(Path.home())
        linked, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Linked bin (frames kept first)", start,
                                                          "Bin files (*.bin)")
        if not linked:
            return
        spot_bin = self.last_spot_bin
        if spot_bin is None or not spot_bin.exists():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Spot bin", self.config.last_dir or start,
                                                            "Bin files (*.bin)")
            if not path:
                return
            spot_bin = Path(path)
        linked = Path(linked)
        default = linked.with_name(f"{linked.stem}_{spot_bin.stem}.bin")
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Merged bin", str(default), "Bin files (*.bin)")
        if not out:
            return
        try:
            m = visual.merge_bin(linked, spot_bin, out)
        except Exception as e:  # noqa: BLE001
            self.log(f"merge failed: {e}", "error")
            return
        self.config.linked_bin = str(linked)
        self.save_config()
        first = f"; spot 1 at frames {m['spots'][0]['pos_idx']}/{m['spots'][0]['neg_idx']}" if m["spots"] else ""
        self.log(f"wrote {out}: {m['n_linked_frames']} linked + "
                 f"{m['n_total_frames'] - m['n_linked_frames']} spot frames{first} (indices in .indices.json)")

    # ------------------------------------------------------------------ holo: Wavefront itself
    def start_or_check_wavefront(self):
        if self.wf_folder is None:
            self.start_wavefront()
        else:
            self.check_wavefront()

    def start_wavefront(self):
        """Start Wavefront from the Wavefront folder, in script mode, saving masks where Oriole reads them."""
        procs = wavefront.running()
        if "core" in procs:
            self.log(f"Wavefront IV is already running (from {procs['core'][1]})")
            return
        folder = Path(self.config.wfd_root)
        problem = wavefront.install_problem(folder)
        if problem:
            others = wavefront.other_installs(folder)
            hint = f"; other installs: {', '.join(map(str, others))} (pick one as the Wavefront folder)" if others else ""
            self.log(f"cannot start Wavefront IV: {problem}{hint}", "error")
            return
        try:
            changed = wavefront.prepare(folder)
            wavefront.start(folder)
        except OSError as e:
            self.log(f"cannot start Wavefront IV from {folder}: {e}", "error")
            return
        self.wf_started = time.time()
        done = f"; set in {wavefront.INI_PATH.name}: {', '.join(changed)}" if changed else ""
        self.log(f"starting Wavefront IV from {folder}, in script mode{done}")
        self._show_wavefront()

    def check_wavefront(self):
        """Send Wavefront a script that does nothing: taken, it is in script mode."""
        if self.jobs or self.computing:
            self.log("not now: Wavefront has scripts to run", "warn")
            return
        if self._wavefront_missing(log=True) or not self._purge_scripts():
            return
        self.jobs = [Job("check", "check", holo.CHECK_SCRIPT)]
        self._send()

    def _watch_wavefront(self):
        """Every second: is Wavefront running, from which install, ready; its script mode is checked once it is
        ready, and said when it does not start."""
        core = wavefront.running().get("core")
        folder = (core[1] or Path(self.config.wfd_root)) if core else None     # its folder unknown: taken as ours
        ready = core is not None and wavefront.window_open(core[0])
        if ready and not self.wf_ready:                 # just ready (or seen for the first time)
            saved_in = wavefront.read_ini("mainSettings", "rootPath")
            if not _same_folder(folder, self.config.wfd_root):
                self.log(f"Wavefront IV runs from {folder}, not from the Wavefront folder {self.config.wfd_root}: it "
                         f"reads its scripts elsewhere. Close it and press Start, or pick {folder} as the Wavefront "
                         f"folder", "error")
            elif saved_in and not _same_folder(saved_in, self.config.wfd_output_dir):   # started by hand
                self.log(f"Wavefront IV saves its masks in {saved_in}, not in {self.config.wfd_output_dir}: close it "
                         f"and press Start (Oriole sets where it saves them)", "error")
            elif self.wf_mode is None:
                self.wf_check.start()
        if core is None:
            self.wf_mode = None
        if ready:
            self.wf_started = None
        elif self.wf_started is not None and core is None and time.time() - self.wf_started > START_TIMEOUT_S:
            self.wf_started = None
            self.log(f"Wavefront IV did not start (see its own message): {wavefront.LICENCE_HELP}", "error")
        self.wf_folder, self.wf_ready = folder, ready
        self._show_wavefront()

    def _check_when_idle(self):
        if self.wf_ready and self.wf_mode is None and not self.jobs and not self.computing:
            self.check_wavefront()

    def _wavefront_missing(self, log: bool = False) -> str | None:
        """Why Wavefront cannot take Oriole's scripts now (not running, or from another install), if so."""
        if not wavefront.SUPPORTED:
            return None
        core = wavefront.running().get("core")
        if core is None:
            why = "Wavefront IV is not running: press Start (holo Settings)"
        elif core[1] is not None and not _same_folder(core[1], self.config.wfd_root):
            why = f"Wavefront IV runs from {core[1]}, not from the Wavefront folder {self.config.wfd_root}"
        else:
            return None
        if log:
            self.log(why, "error")
        return why

    def _show_wavefront(self):
        if not wavefront.SUPPORTED:
            text, colour = "Wavefront: started by hand (not Windows)", DIM
        elif self.wf_folder is None:
            text, colour = ("Wavefront: starting…", WARN) if self.wf_started else ("Wavefront: not running", DIM)
        elif not _same_folder(self.wf_folder, self.config.wfd_root):
            text, colour = f"Wavefront: another install ({self.wf_folder.name})", LOG_COLOURS["error"]
        elif not self.wf_ready:
            text, colour = "Wavefront: starting…", WARN
        elif self.wf_mode:
            text, colour = "Wavefront: script mode ✓", LOG_COLOURS["info"]
        elif self.wf_mode is False:
            text, colour = "Wavefront: NOT in script mode", LOG_COLOURS["error"]
        else:
            text, colour = "Wavefront: running", WARN
        self.lbl_wavefront.setText(text)
        self.lbl_wavefront.setStyleSheet(f"color: {colour}; font-weight: bold;")
        self.btn_wavefront.setText("Start" if self.wf_folder is None else "Check")
        self.btn_wavefront.setEnabled(wavefront.SUPPORTED and not (self.wf_started and self.wf_folder is None))
        self.btn_wavefront.setToolTip("start Wavefront IV from the Wavefront folder, in script mode"
                                      if self.wf_folder is None else "check that Wavefront takes scripts")

    # ------------------------------------------------------------------ holo: folders
    @property
    def computing(self) -> bool:
        """A generation running, or its cleanup: the Wavefront folders are Wavefront's."""
        return self.generation is not None or self.cleanup is not None

    def _wavefront_folders(self) -> tuple[str, str]:
        return self.config.wfd_script_dir, self.config.wfd_output_dir

    def _purge_scripts(self, quiet: bool = False) -> bool:
        """Empty the script folder before sending a script; False if a file could not be deleted."""
        swept, left = holo.sweep(self.config.wfd_script_dir)
        if left:
            self.log(f"cannot empty the script folder: {_names(left)} left (in use?)", "error")
            return False
        if swept and not quiet:
            self.log(f"script folder purged ({', '.join(swept)} left there by Wavefront)", "warn")
        return True

    def sweep_script_folder(self):
        if self.computing:
            self.log("not now: Wavefront is computing masks from that folder", "warn")
            return
        self.jobs = []                                  # a Load or a check still waiting goes with its script
        swept, left = holo.sweep(self.config.wfd_script_dir)
        if left:
            self.log(f"cannot empty the script folder: {_names(left)} left (in use?)", "error")
        else:
            self.log(f"🧹 script folder swept clean ({', '.join(swept)})" if swept else "🧹 script folder already clean")

    def sweep_mask_folder(self):
        if self.computing:
            self.log("not now: Wavefront is saving masks in that folder", "warn")
            return
        folder = self.config.wfd_output_dir
        files = holo.files_in(folder)
        if not files:
            self.log("🧹 phase mask folder already clean")
            return
        if self._choose("Sweep the phase mask folder",
                        f"Delete the {len(files)} files ({len(holo.available_masks(folder))} masks, dated "
                        f"{holo.file_dates(files)}) in\n{folder}?", {"delete": "Delete them"}) is None:
            return
        swept, left = holo.sweep(folder)
        self.log(f"🧹 phase mask folder swept clean ({_n(len(swept), 'file')})")
        if left:
            self.log(f"cannot empty {folder}: {_names(left)} left (in use?)", "error")
        self.refresh_masks()

    def open_mask_folder(self):
        folder = Path(self.config.wfd_output_dir)
        if not folder.is_dir():
            self.log(f"folder not found: {folder}", "warn")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _watch_folders(self):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        for folder in self._wavefront_folders():
            if Path(folder).is_dir():
                self.watcher.addPath(folder)

    def refresh_masks(self):
        """Mask numbers found in the phase mask folder, and the Generate button: STOP while generating, locked while
        cleaning up after an abort, yellow while a Wavefront folder holds files that are not Oriole's own script."""
        current = self.combo_mask.currentText()
        self.combo_mask.clear()
        self.combo_mask.addItems([str(n) for n in holo.available_masks(self.config.wfd_output_dir)])
        self.combo_mask.setCurrentText(current)         # kept when still there
        self.btn_load_mask.setEnabled(self.combo_mask.count() > 0 and not self.computing)
        self.btn_generate.setEnabled(self.cleanup is None)
        if self.cleanup is not None:                    # _clean_up counts the seconds down
            state = ("CLEANING UP…", STOP_STYLE, "waiting for Wavefront to finish before sweeping")
        elif self.generation is not None:
            state = ("■ STOP", STOP_STYLE, "stop the generation (both Wavefront folders are emptied after it)")
        elif not self.jobs and any(holo.files_in(folder) for folder in self._wavefront_folders()):
            state = ("GENERATE PHASE MASKS", BLOCKED_STYLE, "a Wavefront folder is not empty: Generate asks to sweep it")
        else:
            state = ("GENERATE PHASE MASKS", ACTION_STYLE, "")
        text, style, tip = state
        self.btn_generate.setText(text)
        self.btn_generate.setStyleSheet(style)
        self.btn_generate.setToolTip(tip)

    # ------------------------------------------------------------------ holo: generate
    def generate(self):
        if self.computing:
            self.log("Wavefront is still computing the previous masks", "warn")
            return
        script_dir, out_dir = self._wavefront_folders()
        if not Path(out_dir).is_dir() or not Path(script_dir).is_dir():
            self.log(f"Wavefront folders not found ({script_dir}, {out_dir})", "error")
            return
        if not self._have_spots() or self._wavefront_missing(log=True):
            return
        if self.jobs:                                   # a Load or a check still waiting: the masks replace it
            self.jobs = []
            self._purge_scripts(quiet=True)
        if not self._sweep_before_generating():
            return
        fits = self._field_fits()
        if fits is None:
            return
        cal, d = self.config.holo, self.spin_diam.value()
        spots_um = cal.to_um(self.spots, self.image.shape)
        names = [holo.mask_name(k + 1) for k in range(len(spots_um))]
        self.jobs = [Job("mask", name, holo.mask_script(name, x, y, d), holo.mask_png(out_dir, name), k)
                     for k, (name, (x, y)) in enumerate(zip(names, spots_um))]
        self.generation = {"spots_px": list(self.spots), "spots_um": spots_um, "diameter": d, "cal": cal,
                           "fits": fits}
        self.slm_spot = None                            # Wavefront computes on the SLM: no longer the loaded mask
        self.redraw_spots()
        self.refresh_masks()                            # Generate becomes STOP, Load is disabled
        self._send()

    def _sweep_before_generating(self) -> bool:
        """Both Wavefront folders must be empty: a warning says what is left and sweeps it if you say so."""
        script_dir, out_dir = self._wavefront_folders()
        scripts, files = holo.files_in(script_dir), holo.files_in(out_dir)
        if not scripts and not files:
            return True
        left = []
        if scripts:
            left.append(f"script folder: {_names(scripts)} (not taken by Wavefront: is auto load on?)")
        if files:
            left.append(f"phase mask folder: {_n(len(files), 'file')} "
                        f"({_n(len(holo.available_masks(out_dir)), 'mask')}), dated {holo.file_dates(files)}")
        self.log(f"Wavefront folders not empty: {'; '.join(left)}", "warn")
        if self._choose("Wavefront folders not empty",
                        "These Wavefront folders are not empty:\n\n" + "\n".join(f"• {line}" for line in left) +
                        "\n\nSweep them and generate?", {"sweep": "Sweep and generate"}) is None:
            self.log("not generating: the Wavefront folders must be empty", "warn")
            return False
        swept, still = holo.sweep(script_dir, out_dir)
        if still:                                       # in use, or written again at once: Wavefront still busy
            self.log(f"not generating: {_names(still)} still in the Wavefront folders after the sweep (in use, or "
                     f"Wavefront still saving?)", "error")
            return False
        self.log(f"🧹 Wavefront folders swept ({_n(len(swept), 'file')})")
        return True

    def _field_fits(self) -> list[str] | None:
        """Where each spot falls in Wavefront's field (holo.field_fit). Spots (partly) outside: a warning lists them,
        to remove them, generate anyway (a big red line in the log) or cancel (None)."""
        d = self.spin_diam.value()
        spots_um = self.config.holo.to_um(self.spots, self.image.shape)
        fits = [holo.field_fit(x, y, d) for x, y in spots_um]
        outside = [k for k, fit in enumerate(fits) if fit != "in"]
        if not outside:
            return fits
        off = [f"spot {k + 1}{' partly' if fits[k] == 'partly' else ''} at ({spots_um[k][0]:.1f}, "
               f"{spots_um[k][1]:.1f}) µm" for k in outside]
        field = f"(Wavefront: ±{holo.FIELD_HALF_UM} µm)"
        choice = self._choose(
            "Spots outside the SLM field",
            f"Outside the SLM field {field}:\n\n" + "\n".join(f"• {s}" for s in off) +
            "\n\nTheir masks would hold no spot, or a cut one. A right click on a spot removes it; Remove spots "
            "removes them all (Undo brings them back) and generates the others.",
            {"remove": "Remove spots and generate", "anyway": "Generate anyway"})
        if choice is None:
            self.log(f"not generating: {', '.join(off)} outside the SLM field (a right click on a spot removes it)",
                     "warn")
            return None
        if choice == "anyway":
            self.log(f"⚠ OUTSIDE THE SLM FIELD {field}: {', '.join(off)}. Their masks hold no spot or a cut one; "
                     f"generating anyway", "alarm")
            return fits
        self._edit_spots([p for k, p in enumerate(self.spots) if k not in outside])
        self.log(f"removed {', '.join(f'spot {k + 1}' for k in outside)}: outside the SLM field (Undo brings them "
                 f"back)", "warn")
        if not self.spots:
            self.log("not generating: no spot left in the SLM field", "warn")
            return None
        return ["in"] * len(self.spots)

    def stop_generation(self):
        if self.generation is not None:
            self._abort("STOP pressed", level="warn")

    # ------------------------------------------------------------------ holo: Wavefront jobs
    def _send(self) -> bool:
        """Send the first job's script; False if it cannot be (a generation is then aborted, a Load dropped)."""
        job = self.jobs[0]
        try:
            holo.send_script(self.config.wfd_script_dir, job.js)
        except Exception as e:  # noqa: BLE001
            if job.kind == "mask":
                self._abort(f"cannot send the {job.name} script: {e}")
            else:
                self.jobs = []
                self.log(f"cannot send the {job.what}: {e}", "error")
            return False
        job.sent, job.taken = time.time(), None
        self.timer.start()
        if job.kind == "mask":
            again = f", attempt {job.attempt}/{holo.MAX_ATTEMPTS}" if job.attempt > 1 else ""
            self.log(f"Wavefront: computing {job.name} ({job.spot + 1} of {len(self.generation['spots_px'])}{again})")
        return True

    def poll(self):
        """Every 100 ms: the next job once Wavefront took this one (and saved its mask, with the right spot); this one
        again when it did not in time. After an abort, the cleanup."""
        if self.cleanup is not None:
            self._clean_up()
            return
        if not self.jobs:
            self.timer.stop()
            return
        job, now = self.jobs[0], time.time()
        if job.taken is None:
            if holo.script_pending(self.config.wfd_script_dir):
                if now - job.sent >= holo.NOT_READ_S:
                    self._retry(f"Wavefront did not take the {job.what} within {holo.NOT_READ_S:.0f} s", NOT_TAKEN_FIX)
                return
            job.taken = now
            if not self.wf_mode:                        # it takes scripts: in script mode
                self.wf_mode = True
                self._show_wavefront()
        if job.kind != "mask":                          # a Load or a check: done once taken
            self.jobs = []
            self.timer.stop()
            if job.kind == "check":
                self.log("Wavefront IV takes scripts: script mode ✓")
            self.refresh_masks()
        elif holo.mask_saved(job.png, job.sent):
            if self._mask_holds_its_spot(job):
                self._next_job()
        elif now - job.taken >= holo.MASK_WAIT_S:
            self._retry(f"Wavefront took the {job.name} script but did not save {job.png.name} within "
                        f"{holo.MASK_WAIT_S:.0f} s", NOT_SAVED_FIX)

    def _mask_holds_its_spot(self, job: Job) -> bool:
        """The spot Wavefront drew for the mask is the one asked for, else the generation is aborted. Nothing drawn:
        a big red line only. A spot (partly) outside the field was said at Generate: its mask is not checked."""
        g = self.generation
        if g["fits"][job.spot] != "in":
            return True
        (x, y), d = g["spots_um"][job.spot], g["diameter"]
        drawn = holo.drawn_spot(self.config.wfd_output_dir, job.name, d)
        if drawn is None:
            return True
        if math.isnan(drawn[0]):
            self.log(f"⚠ {job.name} HOLDS NO SPOT: Wavefront drew nothing for spot {job.spot + 1} at ({x:.1f}, "
                     f"{y:.1f}) µm (outside its field?)", "alarm")
            return True
        if holo.off_spot(drawn, x, y, d):
            self._abort(f"{job.name} holds a spot drawn at ({drawn[0]:.1f}, {drawn[1]:.1f}) µm, not at ({x:.1f}, "
                        f"{y:.1f}) µm (spot {job.spot + 1})", NOT_SAVED_FIX)
            return False
        return True

    def _next_job(self):
        self.jobs.pop(0)
        if self.jobs:
            self._send()
            return
        g, self.generation = self.generation, None
        self.timer.stop()
        out_dir = self.config.wfd_output_dir
        rec = holo.write_spot_record(out_dir, g["spots_px"], g["spots_um"], g["diameter"], g["cal"])
        self.log(f"{_n(len(g['spots_px']), 'phase mask')} saved in {out_dir} (spot i = {holo.PREFIX}i, each checked "
                 f"against its drawn spot; {rec.name})")
        self.refresh_masks()

    def _retry(self, problem: str, fix: str):
        """Erase the script and send it again, MAX_ATTEMPTS sends in all; then abort the generation, or give up the
        Load. A check is sent once: not taken, Wavefront is not in script mode."""
        job = self.jobs[0]
        if job.kind == "check":
            self._purge_scripts(quiet=True)
            self.jobs, self.wf_mode = [], False
            self._show_wavefront()
            self.refresh_masks()
            self.log(f"{problem}: Wavefront IV is not in script mode; {AUTOLOAD_HINT}", "error")
            return
        problem += f" (attempt {job.attempt}/{holo.MAX_ATTEMPTS})"
        if self._purge_scripts(quiet=True) and job.attempt < holo.MAX_ATTEMPTS:
            self.log(f"{problem}: script erased, sending it again", "warn")
            job.attempt += 1
            self._send()
        elif job.kind == "mask":
            self._abort(problem, fix)
        else:
            self.jobs = []
            self.slm_spot = None                        # not loaded: what the SLM shows is unknown
            self.redraw_spots()
            self.log(f"{problem}: {job.name} not loaded; {fix}", "error")

    def _abort(self, reason: str, fix: str = "", level: str = "error"):
        """Send nothing more; ABORT_WAIT_S later (Wavefront may still be computing) _clean_up sweeps."""
        self.cleanup = {"at": time.time() + holo.ABORT_WAIT_S, "fix": fix, "level": level, "sweeps": 0, "swept": []}
        self.jobs, self.generation = [], None
        self.log(f"{reason}: generation aborted; cleaning up in {holo.ABORT_WAIT_S:.0f} s, Wavefront may still be "
                 f"computing", level)
        self.refresh_masks()
        self.timer.start()

    def _clean_up(self, wait: bool = True):
        """Back to the normal state: both Wavefront folders empty (they were when the generation started), Generate
        ready. Swept at cleanup["at"], then every second while a file is left (Wavefront still saving, a file still
        open), MAX_SWEEPS sweeps at most."""
        c = self.cleanup
        left = c["at"] - time.time()
        if wait and left > 0:
            self.btn_generate.setText(f"CLEANING UP IN {math.ceil(left)} s")
            return
        swept, still = holo.sweep(*self._wavefront_folders())
        c["swept"] += swept
        c["sweeps"] += 1
        if still and wait and c["sweeps"] < holo.MAX_SWEEPS:
            c["at"] = time.time() + 1                   # not empty yet: again in 1 s
            self.btn_generate.setText("CLEANING UP…")
            return
        self.timer.stop()
        self.cleanup = None
        fix = f"; {c['fix']}" if c["fix"] else ""
        if still:
            self.log(f"cleanup after the abort incomplete: {_names(still)} still in the Wavefront folders after "
                     f"{_n(c['sweeps'], 'sweep')}: sweep them with the 🧹{fix}", "error")
        else:
            again = f" ({c['sweeps']} sweeps)" if c["sweeps"] > 1 else ""
            files = f": {', '.join(c['swept'])}" if c["swept"] else ""
            self.log(f"cleaned up after the abort{again}: both Wavefront folders emptied "
                     f"({_n(len(c['swept']), 'file')}{files}){fix}", c["level"])
        self.refresh_masks()

    # ------------------------------------------------------------------ holo: manual
    def load_selected_mask(self):
        if not self.combo_mask.currentText():
            self.log(f"no {holo.PREFIX}<n> mask in {self.config.wfd_output_dir}", "warn")
            return
        if self._wavefront_missing(log=True) or not self._purge_scripts():
            return
        n = int(self.combo_mask.currentText())
        name = holo.mask_name(n)
        self.jobs = [Job("load", name, holo.load_script(holo.mask_png(self.config.wfd_output_dir, name)))]
        if not self._send():
            return
        self.log(f"SLM: loading {name}")
        self.slm_spot = holo.recorded_spot(self.config.wfd_output_dir, n)
        if self.slm_spot is None:
            self.log(f"{name} is not in an up to date {holo.RECORD_NAME}: its spot cannot be shown on the image",
                     "warn")
        self.redraw_spots()

    def open_shutter(self):
        try:
            self.shutter.open()
            self.log(f"shutter OPEN ({self.shutter.channel})", "warn")
        except ShutterError as e:
            self.log(str(e), "error")
        self._show_shutter()

    def close_shutter(self):
        try:
            self.shutter.close()
            self.log("shutter closed")
        except ShutterError as e:
            self.log(str(e), "error")
        self._apply_shutter_output()
        self._show_shutter()

    def _apply_shutter_output(self):
        """The shutter output of the settings; an open shutter keeps its own until closed."""
        if not self.shutter.is_open:
            self.shutter = Shutter(self.config.nidaq_device, self.config.shutter_ao)

    def _show_shutter(self):
        if self.shutter.is_open:
            self.lbl_shutter.setText("Shutter OPEN")
            self.lbl_shutter.setStyleSheet("background: #FF4400; color: white; font-weight: bold; padding: 4px;")
        else:
            self.lbl_shutter.setText("Shutter closed")
            self.lbl_shutter.setStyleSheet(f"background: {BTN}; color: {TEXT}; padding: 4px;")

    # ------------------------------------------------------------------ settings / exit
    def _on_dmd(self, *_):
        self.config.dmd_height, self.config.dmd_width = self.spin_rows.value(), self.spin_cols.value()
        self.config.pixel_size_um = self.spin_dmd_px.value()
        self.save_config()

    def _on_diameter(self, value: float):
        self.config.spot_diameter_um = value
        self.save_config()
        self.redraw_spots()

    def _on_calibration(self):
        self.config.visual_cal = self.visual_box.calibration().to_dict()
        self.config.holo_cal = self.holo_box.calibration().to_dict()
        self.save_config()
        self.redraw_spots()
        self.redraw_field()

    def _on_slm_field(self, show: bool):
        self.config.show_slm_field = show
        self.save_config()
        self.redraw_field()

    def _on_start_wavefront(self, on: bool):
        self.config.start_wavefront = on
        self.save_config()

    def _on_wavefront_root(self, root: str):
        self.config.wfd_root = root
        self.save_config()
        self._show_wavefront_dirs()
        for d in self._wavefront_folders():
            if not Path(d).is_dir():
                self.log(f"folder not found: {d}", "warn")
        self._watch_folders()
        self.refresh_masks()
        if wavefront.SUPPORTED:
            self.wf_ready = False                       # seen again as just ready: its folder and mode checked
            self._watch_wavefront()

    def _on_shutter_output(self, *_):
        self.config.nidaq_device = self.edit_device.text().strip() or "Dev1"
        self.config.shutter_ao = self.spin_ao.value()
        self.save_config()
        if self.shutter.is_open:
            self.log("the new shutter output applies once the shutter is closed", "warn")
        self._apply_shutter_output()

    def closeEvent(self, ev):
        self.feed.live = False
        self.wf_timer.stop()
        self.wf_check.stop()
        if self.generation is not None:
            self._abort("window closed", level="warn")
        if self.cleanup is not None:
            self._clean_up(wait=False)                  # the window is closing: sweep now
        if self.shutter.is_open:
            self.close_shutter()
        self.timer.stop()
        self.save_config()
        super().closeEvent(ev)


def run(config: Config | None = None, config_path=DEFAULT_CONFIG_PATH) -> int:
    QtCore.QLocale.setDefault(QtCore.QLocale.c())      # decimal point in every number box, as in DHparams.m
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    win = MainWindow(config or Config.load(config_path), config_path)
    sys.excepthook = win.internal_error
    win.show()
    return app.exec()
