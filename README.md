# Oriole — visual & holographic stimulation spots

Click spots on a microscope image; Oriole turns them into
* **visual (DMD)** frames: a small bin to merge into a stimulus bin, and
* **holographic (SLM)** phase masks, computed and saved by Wavefront IV (replaces the MATLAB DHgui).

Power is always 100 % (no intensity modulation). Protocols run in Plerion.

Formerly named **Aule**: the settings in `~/.aule/config.json` are picked up on first start.

## Install / run

Double-click `Oriole.bat` (Windows) or run `./Oriole.sh` (Linux): they create the `oriole` conda
env from `environment.yml` on first use, then start the window. By hand:

```
conda env create -f environment.yml
conda activate oriole
python -m oriole                 # the application
python -m pytest tests           # self-tests
```

The shutter needs `nidaqmx` and the NI-DAQmx driver; without them Oriole runs and logs the error.

## The window

```
 VISUAL · DMD          image                                  HOLO · SLM
 Calibration           Load image…  Spot Ø  Undo  Clear       Calibration
  X  Y  rotation                                               X  Y  rotation
  stretch X / Y         left click: add a spot                 stretch X / Y
 DMD (MEA3)             right click on a spot: remove it      Manual
  rows  columns         (spots are numbered 1..N)              Load mask…  shutter Open / Close
  pixel size                                                  Wavefront & shutter
                                                               script / phase mask folders
                                                               shutter device + output
 EXTRACT BIN                                                  GENERATE PHASE MASKS
 Merge into bin…
 log ──────────────────────────────────────────────────────────────────────────────
```

Every value typed in the window is saved at once in `~/.oriole/config.json`.

**Calibration** (typed by hand, one per side, kept in `~/.oriole/config.json`). A camera pixel
`p` of an image of W × H pixels goes to

    µm = R(-rotation) · diag(stretch X, stretch Y) · (p + 0.5 - (W/2, H/2)) + (X, Y)

the DHgui transform: the stretches are µm per camera pixel, a negative stretch mirrors.
Holo default = `DHparams.m`: X 5, Y -8, rotation 0, stretch 0.67 / -0.67 (mirror y), sent to
Wavefront in µm. Visual µm are relative to the DMD frame centre and divided by the DMD pixel
size (2.5 µm, *DMD* box). The bottom line under the image shows where the mouse falls in both.

**Spot Ø** is the diameter of every spot, in µm on the sample.

**EXTRACT BIN** writes `YYYY_MM_DD_<N>Frames.bin` (N = number of spots; `_2`, `_3`… if the name
is taken) in the folder you pick, for MEA3 (the only rig): for spot i (from 1) frame `2(i-1)` is
the spot and `2(i-1)+1` its negative. `<bin>.json` lists the spots (px, µm) and frame indices.

**Merge into bin…** asks for the linked bin, then where to save `<linked>_<spot bin>.bin`: the
linked frames byte for byte, then the spot frames. Inputs are never modified;
`<merged>.indices.json` gives each spot's frames in the merged bin.

**GENERATE PHASE MASKS** (Wavefront IV open with *script auto-load* on): one mask per spot,
saved by Wavefront as `Pattern1_000.algoPhp.png` … `PatternN…` (the names Plerion counts).
Old `Pattern<n>` files are reported first with their dates (YYYY MM DD), with the choice to
delete them. The `Pattern<n>` names are fixed (DHgui's format). Scripts are dropped as `test.js`
+ `zdone.js` (as DHgui and Plerion do), one at a time, each given up to 2 minutes;
`Pattern_spots.json` next to the masks says which spot each mask is.

**Manual**: *Load mask…* shows a saved mask on the SLM; *Open* / *Close* drive the shutter
(NI-DAQ output from the *Wavefront & shutter* box, `Dev1/ao0` by default; 10 V = open, as
Plerion). Oriole only holds that output while the shutter is open and closes it on exit: close
it before starting a Plerion protocol.

## Layout

```
oriole/
  calibration.py  the 5-number manual calibration
  visual.py       spots -> DMD discs, spot bin + json, merge into a linked bin
  holo.py         Wavefront scripts, file-drop client, old masks, spot record
  shutter.py      NI-DAQ analog-output shutter
  binfile.py      lab bin reader / writer (numpy 2)
  images.py       image loading (grey)
  config.py       settings + calibrations (~/.oriole/config.json)
  gui.py          the window (dark theme from Plerion)
tests/            pytest (Wavefront and NI-DAQ simulated)
```

Later: Micro-Manager as the image source (snap, then live) through `MainWindow.set_image`.
