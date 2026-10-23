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
 Settings              Load image…  Spot Ø  Undo  Clear       Settings
  DMD rows / columns                                           Wavefront folder, 🧹 sweeps
  pixel size            left click: add a spot                 shutter device + output
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

**Spot Ø** is the diameter of every spot, in µm on the sample.

The epi image is expected at **672 × 512 px** (width × height), the size the calibrations are made
for: any other size gives a red error in the log and a big red banner above the holo calibration.

**EXTRACT BIN** writes `YYYY_MM_DD_<N>Frames.bin` (N = number of spots; `_2`, `_3`… if the name
is taken) in the folder you pick, for MEA3 (the only rig): for spot i (from 1) frame `2(i-1)` is
the spot and `2(i-1)+1` its negative. `<bin>.json` lists the spots (px, µm) and frame indices.

**Merge into bin…** asks for the linked bin, then where to save `<linked>_<spot bin>.bin`: the
linked frames byte for byte, then the spot frames. Inputs are never modified;
`<merged>.indices.json` gives each spot's frames in the merged bin.

**GENERATE PHASE MASKS** (Wavefront IV open with *Tools → Script engine → auto load (script
folder)* selected): one mask per spot, saved by Wavefront as `Pattern1_000.algoPhp.png` …
`PatternN…` (DHgui's names, the ones Plerion counts).
* It first empties the script folder (Wavefront sometimes leaves a script there).
* The button is **yellow** while the phase mask folder already holds `Pattern<n>` masks: pressing
  it then only logs an error (with the masks' dates, YYYY MM DD). Empty the folder first.
* Scripts are dropped as `test.js` + `zdone.js` (as DHgui and Plerion do), one at a time. If a
  script is still there after 10 s, the log says Wavefront is not reading the folder (check the
  auto load option); after 2 minutes Oriole gives up.
* `Pattern_spots.json` next to the masks says which spot each mask is.

**Wavefront folder** (holo *Settings*, `E:\wfdIV_20261022` by default): the scripts go to its
`script\` and the masks are read from its `GeneratedPhasemasks\`; both paths are shown below it.
The 🧹 next to the script folder sweeps it clean at once, the one next to the phase mask folder
after a confirmation (it lists how many files and their dates).

**Manual**: pick a mask number (the `Pattern<n>` found in the phase mask folder) and *Load* it
onto the SLM (the script folder is emptied first, and the same 10 s check applies). *Open* /
*Close* drive the shutter (NI-DAQ output from the holo *Settings*, `Dev1/ao0` by default;
10 V = open, as Plerion). Oriole only holds that output while the shutter is open and closes it
on exit: close it before starting a Plerion protocol.

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
