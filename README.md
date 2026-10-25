# Oriole — visual & holographic stimulation spots

Click spots on a microscope image; Oriole turns them into
* **visual (DMD)** frames: a small bin to merge into a stimulus bin, and
* **holographic (SLM)** phase masks, computed and saved by Wavefront IV (replaces the MATLAB DHgui).

Power is always 100 % (no intensity modulation). Protocols run in Plerion.

Formerly named **Aule**: the settings in `~/.aule/config.json` are picked up on first start.

## Oriole TCP (branch `tcp`, in progress)

This branch is checked out in `E:\Oriole-tcp` (a git worktree) and started from the **Oriole TCP**
desktop shortcut. The usual **Oriole** shortcut keeps running branch `oriole` from `E:\Oriole`.
Both share `~/.oriole/config.json` (each ignores the other's extra keys). What differs so far:
* **Window title and menu:** the title says *Oriole TCP*. A **Wavefront** menu chooses the
  Wavefront Oriole drives over TCP: IV, or V (not ready yet).
* **Wavefront IV over TCP, no script folder:** every script (each mask of a generation, a Load)
  goes to Wavefront IV's TCP server (`oriole/tcplink.py`). Oriole TCP never writes to the
  `script` folder and doesn't touch the auto-load setting (`[scriptEngine] autoload` in
  `wfdIV.ini`, used by Plerion and by Oriole on `oriole`).
  * **For now, by hand:** tick *Tools → Script engine → Tcp server* in Wavefront IV after every
    start; Wavefront doesn't remember it.
  * **The port:** Wavefront opens its server by itself at every start, on a new port written to
    `%TEMP%\blueBoxServer` (IP, then port). Oriole reads it there before connecting, as
    `arduinoReader` does, and connects again by itself when Wavefront restarts.
  * **The message:** a Qt `QDataStream` `QByteArray`, as captured from `arduinoReader`: the length
    (4 bytes, big-endian), then the UTF-8 JavaScript, the same as a `test.js`. Wavefront answers
    nothing.
  * **Knowing that scripts run:** the server listens from the start, whether *Tcp server* is
    ticked or not, so a connection proves nothing. The first mask saved does: the state then reads
    *TCP ✓*. A generation whose first mask is never saved ends with "Tcp server ticked?".
* **Phase masks of this session:**
  * **Asked at the first Generate:** nothing opens when Oriole starts. The first **GENERATE PHASE
    MASKS** of a session asks for the folder of the experiment, then goes on. The dialog opens on
    the experiment drive (`experiment_drive`, `D:\` by default), and Oriole makes a `Phasemasks`
    folder in it; a chosen folder already named so is used as it is. Nothing is remembered between
    runs.
  * **The button:** green while no folder is chosen yet; yellow only when a folder (Wavefront's or
    the session's) holds masks.
  * **Needed by:** Load, and that folder's 🧹 and 📂. **…** chooses it, or another.
  * **How masks get there:** Wavefront IV can't be told by script where to save, so it still saves
    each mask in its `GeneratedPhasemasks`. Oriole moves the checked masks (the 6 files of each)
    into the session's folder once the generation is done, then writes `Pattern_spots.json` there.
  * **Plerion** still reads masks from `GeneratedPhasemasks`, so it doesn't follow this version yet.
* **Brooms** accept a `GeneratedPhasemasks` folder (in a Wavefront install) or a `Phasemasks`
  folder, with the same allow-list (`Pattern<n>_000.*`, `Pattern_spots.json`): Plerion's order
  files, for instance, are never deleted. A `script` folder is never swept.
* **Still to come:** Wavefront V over TCP (port in `%APPDATA%\idv-photonics\wfdV\tcpPort.txt`; a
  message is a magic number `0xABCDEF88`, then a length, the command and a CRC-16, as in Plerion's
  `pythonToWfdVTcpCom.py`), then switching each Wavefront's TCP server on by itself.

## Install / run

Windows: double-click **`Oriole.vbs`** (no console window at all; a desktop shortcut to it works
too). It runs `Oriole.bat` hidden, which creates the `oriole` conda env from `environment.yml` on
first use (the console is then shown, as it takes a few minutes) and starts the window.
`Oriole.bat` also works on its own, with a console flashing at start. Linux: `./Oriole.sh`.
By hand:

```
conda env create -f environment.yml
conda activate oriole
python -m oriole                 # the application
python -m pytest tests           # self-tests
```

The shutter needs `nidaqmx` and the NI-DAQmx driver; without them Oriole runs and logs the error.
*Snap* / *Live* need `pyjavaz` (in `environment.yml`; an `oriole` env made before it:
`conda activate oriole` then `pip install pyjavaz`). An error Oriole did not foresee is logged in
red, its details in `~/.oriole/oriole.log`, and Oriole goes on; an unreadable `config.json` gives
the default settings (the file is kept as `config.json.bad`).

## The window

```
 VISUAL · DMD          image                                  HOLO · SLM
 Settings              Load image… Snap Live Update   Spot Ø   Settings
  DMD rows / columns    Undo Clear Reset                        Wavefront folder
  pixel size            left click: add a spot                  Wavefront: TCP ✓ (port n)  Check
                                                                phase masks of this session … 📂 🧹
                                                                shutter device + output
 Calibration            right click on a spot: remove it      Calibration
  X  Y  rotation        (spots are numbered 1..N)              X  Y  rotation
  stretch X / Y                                                stretch X / Y
                                                              Manual
                                                               mask [n] Load   shutter Open / Close
 EXTRACT BIN                                                  GENERATE PHASE MASKS
 Merge into bin…
 log ──────────────────────────────────────────────────────────────────────────────
```

Every value typed in the window is saved at once in `~/.oriole/config.json`. Until an image is
loaded the middle shows a camera framing target of the expected size (672 × 512).

**Calibration** (typed by hand, one per side, kept in `~/.oriole/config.json`). A camera pixel
`p` of an image of W × H pixels goes to

    µm = R(-rotation) · diag(stretch X, stretch Y) · (p + 0.5 - (W/2, H/2)) + (X, Y)

the DHgui transform: the stretches are µm per camera pixel, a negative stretch mirrors.
Holo default = `DHparams.m`: X 5, Y -8, rotation 0, stretch 0.67 / -0.67 (mirror y), sent to
Wavefront in µm. Visual µm are relative to the DMD frame centre and divided by the DMD pixel
size (2.5 µm, visual *Settings*). The line under the image shows where the mouse falls in both.

**Spot Ø** is the diameter of every spot, in µm on the sample. **Reset** (next to Undo / Clear) goes
back to how Oriole opens: no image (the camera target), no spot, no Undo, no SLM ring, Live off.

**Snap** / **Live** take the image from Micro-Manager, which keeps the camera. Oriole talks to it
through its pycro-manager server (in Micro-Manager, tick *Tools → Options → Run pycro-manager server
on port 4827* once) and never commands the camera directly. On the rig, changing the binning from
Oriole left the Hamamatsu sending black frames until Micro-Manager was restarted.
* **Snap** is Micro-Manager's own Snap (shown in Micro-Manager too), or its current frame while
  its live runs.
* **Live** switches Micro-Manager's own live on, unless it's already on, and shows its frames (four
  a second at most); the spots stay, and the image is logged once. Stopping it in Micro-Manager
  stops it here too. Stopping it here stops Micro-Manager's live only if Oriole switched it on.
  * A frame that fails is skipped (said in the log); 3 failures in a row switch Live off.
  * A frame that doesn't change for 5 s (frozen camera; "and black" when it is all zero) is said
    in the log.
* **Update** takes the image Micro-Manager's Snap/Live window shows: your last Snap there, with your
  settings, or its live frame. Nothing is taken: the camera isn't touched.
* **Snap and Update are always clickable.** During Live they switch Live off first, then give a
  still image to click spots on, as Micro-Manager's own Snap stops its live. Pressed while
  Micro-Manager is still busy with the previous one, they come right after (a double click gives
  one).
* **Camera settings:** Oriole never changes them. It logs them (binning, exposure) when they
  change, in yellow when they differ from the epi settings (2×2, 1000 ms): set those in
  Micro-Manager.
* **Micro-Manager not running, or its server off:** said in the log after half a second.
* **Quitting:** Oriole always quits completely, even when Micro-Manager no longer answers.

The epi image is expected binned 2×2, at **672 × 512 px** (width × height), the size the
calibrations are made for. Any other size gives a big red "WRONG IMAGE SIZE" banner above the holo
calibration and a detailed red error in the log (usually: the image was not binned 2×2).

The image is fixed (no pan, no zoom): **drag** a spot to move it; **?** at the top opens the help.

**EXTRACT BIN** writes `YYYY_MM_DD_<N>Frames.bin` (N = number of spots; `_2`, `_3`… if the name
is taken) in the folder you pick, for MEA3 (the only rig): for spot i (from 1) frame `2(i-1)` is
the spot and `2(i-1)+1` its negative. `<bin>.json` lists the spots (px, µm) and frame indices.

**Merge into bin…** asks for the linked bin, then where to save `<linked>_<spot bin>.bin`: the
linked frames byte for byte, then the spot frames. Inputs are never modified;
`<merged>.indices.json` gives each spot's frames in the merged bin.

**GENERATE PHASE MASKS** (Wavefront IV open with *Tools → Script engine → Tcp server* ticked): one
mask per spot, saved by Wavefront as `Pattern1_000.algoPhp.png` … `PatternN…` (DHgui's names, the
ones Plerion counts).
* The button is **yellow** while Wavefront's or the session's phase mask folder is not empty:
  pressing it then shows a warning listing what is left (the masks' files and dates, YYYY MM DD)
  and sweeps both folders only if you say so.
* Every mask script (and the Load script) first empties Wavefront's pattern list. As tried on the
  rig: a computed or loaded pattern takes the lowest free ID (the number shown in the list),
  `saveProfile(0)` saves it only if that ID is 0, and `deleteProfile(n)` deletes the pattern of ID
  `n` (not the n-th one). DHgui's scripts end with `deleteProfile(0)`, so one pattern left in the
  list (made by hand, loaded, or a mask not saved) made every following mask take ID 1 or more and
  none was saved. Wavefront has no script command to empty the list (its script commands are the
  `<scriptEngine>` entries of `invoke.xml` in `wfdIV.cfg`): the scripts call `deleteProfile(id)`
  for every ID from 0 to 500 (about 0.35 s).
* Each saved mask is compared with the spot Wavefront drew for it, `Pattern<n>_000.rawSrc.png`
  (white disc on black, (0, 0) µm at the centre, y up, µm per pixel from the disc's area): a spot
  more than half a diameter (3 µm at least) from where it was asked aborts the generation;
  nothing drawn only gets a big red warning.
* The scripts are sent over TCP one at a time, and each mask is checked: its png must be written,
  not empty, after its script was sent.
* While it runs, the button is a red **STOP**.
* A mask not saved 10 s after its script was sent is sent again; the log counts the attempts (1/3,
  2/3). At 3/3 the generation is aborted (*Tcp server* ticked? Or restart Wavefront). Wavefront IV
  not reachable over TCP aborts it at once.
* After STOP or an abort Oriole sends nothing more and waits 2 s (Wavefront may still be
  computing; the button counts down), then empties Wavefront's phase mask folder of Wavefront's and
  Oriole's files (it was empty of them when the generation started), and again every second while
  one is left (still being saved or open), 10 sweeps at most: Generate is ready again.
* `Pattern_spots.json` next to the masks says which spot each mask is.

**Wavefront folder** (holo *Settings*, `E:\wfdIV_20261022` by default): the Wavefront IV install
(`wfdIV.exe`, a launcher that runs the program itself, `wfdIVCore.dll`). Wavefront saves the masks
in its `GeneratedPhasemasks\`, from where Oriole moves them into the session's folder.
Wavefront's licence expires now and then (its fix: *Wavefront IV Errorcode 7536725.pdf* on the
desktop), and it is then installed again in a new folder: pick that one. The **?** next to
*Wavefront folder* gives the procedure step by step: extract the zip again
(`Documents\wfdIV.ifta2D-181220.00.zip`) into `wfdIV_<date>`, make its `script` and
`GeneratedPhasemasks` folders, double-click `log.bat` (it writes the file `log`), drag `log` onto
`wfdIV.exe`, click in the password box and press Ctrl, A and R together, close Wavefront, then pick
the new folder in Oriole and press Start (Oriole TCP needs only `GeneratedPhasemasks`; `script` is
for Plerion and Oriole on `oriole`).
The 🧹 next to the session's phase mask folder sweeps it after a confirmation (it lists how many
files and their dates). 📂 opens it in the file explorer.

**What a sweep may delete** (the 🧹, the sweep before generating, the cleanup after an abort), in
`oriole/holo.py` (`sweep_rule`, `SWEEPABLE`). Every folder is checked first, and if one fails,
nothing at all is deleted (a red line in the log says why). The folder must:
1. exist;
2. be named `GeneratedPhasemasks` or `Phasemasks`;
3. not be a drive root;
4. not be a link or junction to another folder;
5. for `GeneratedPhasemasks`, sit next to a `wfdIV.exe` (in a Wavefront install).

Then only files right inside are deleted, never a sub-folder, and only those Wavefront or Oriole
writes: `Pattern<n>_000.*` and `Pattern_spots.json`. Any other file is left alone and listed in the
log; it doesn't turn Generate yellow either.

**Starting Wavefront.** When Oriole opens and Wavefront is not running (*start Wavefront with
Oriole*, holo *Settings*), or with **Start**, Oriole starts it from the Wavefront folder; then tick
its *Tcp server*. As tried on the rig, Wavefront reads its settings, shared by every install, from
`%APPDATA%\wfdIV\wfdIV.ini` when it starts: Oriole sets `[mainSettings] rootPath` (the folder masks
are saved in: this install's `GeneratedPhasemasks`) there first, every other line left as it is.
The line under the folder says Wavefront's state (checked every second): not running, starting…,
running, *TCP port n (Tcp server ticked?)* (its server reached, no mask saved yet), *TCP ✓* (a mask
saved over TCP), *masks not saved (Tcp server ticked?)*, *TCP server not reachable*, or *another
install* (it runs from another folder: an error in the log, Generate and Load refused). Once it is
ready (its window open) Oriole connects to its TCP server (**Check** does it again). A core that is
empty or missing (licence), a missing `GeneratedPhasemasks` folder, or a Wavefront that does not
start within 20 s, is said in the log, with the other installs
next to it. A Wavefront started by hand that saves its masks elsewhere (`rootPath`, e.g. right
after a reinstall) is said too: close it and press Start. Closing Wavefront asks to confirm:
Oriole never closes it (it keeps running when Oriole closes). Generate and Load say at once when
Wavefront is not running.

**Wavefront's field on the image**, through the holo calibration, as Wavefront draws it (measured
on its display, in µm around its (0, 0)): its square (the 576 px canvas, ±187.8 µm) as a red
line, the image outside it hatched red (a no-go zone: Wavefront cannot put a spot there), its 20 %
diffraction efficiency circle (r 179 µm) in orange dashes, its 90 % one (r 47 µm) in yellow, its
centre cross in light grey. The parts outside the camera image are not drawn. *SLM field on the
image* (holo *Settings*, kept in the config) shows or hides it all. With a spot outside the field,
or cut by its edge, Generate (after the folder check) shows a warning listing those spots:
*Remove spots and generate* (removes them all, one *Undo* brings them back), *Generate anyway* (a
big bold red line in the log; their masks hold no spot, or a cut one, and are not checked) or
*Cancel* (a right click on a spot removes it). The values are
in `oriole/holo.py` (`FIELD_HALF_UM`, `EFFICIENCY_RADII_UM`, `CROSS_HALF_UM`).

**Manual**: pick a mask number (the `Pattern<n>` found in the phase mask folder) and *Load* it
onto the SLM, over TCP (Wavefront answers nothing; not reachable: not loaded, said in red). Its spot
gets a purple *SLM* ring on the image, where it was clicked (read from `Pattern_spots.json`; a
mask with no record there, or made again since, e.g. by DHgui, is loaded without a ring). *Open* /
*Close* drive the shutter (NI-DAQ output from the holo *Settings*, `Dev1/ao0` by default;
10 V = open, as Plerion). Oriole only holds that output while the shutter is open and closes it
on exit: close it before starting a Plerion protocol.

## Layout

```
oriole/
  calibration.py  the 5-number manual calibration, the expected epi image size
  camera.py       Snap / Live from the running Micro-Manager (pycro-manager server, pyjavaz)
  wavefront.py    Wavefront IV the program: start (wfdIV.ini), installs, processes
  tcplink.py      Wavefront IV's TCP server: its port, the message format, the connection
  visual.py       spots -> DMD discs, spot bin + json, merge into a linked bin
  holo.py         Wavefront scripts and field, mask checks, folder sweeps, masks stored, spot record
  shutter.py      NI-DAQ analog-output shutter
  binfile.py      lab bin reader / writer (numpy 2)
  images.py       image loading (grey)
  config.py       settings + calibrations (~/.oriole/config.json)
  theme.py        colours and styles (dark theme from Plerion)
  widgets.py      the image view (spots, SLM ring, Wavefront's field), calibration box, small factories
  gui.py          the window: settings, spots, extract / merge, Wavefront jobs (generate, Load), shutter
tests/            pytest (Wavefront, Micro-Manager and NI-DAQ simulated)
```
