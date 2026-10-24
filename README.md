# Oriole — visual & holographic stimulation spots

Click spots on a microscope image; Oriole turns them into
* **visual (DMD)** frames: a small bin to merge into a stimulus bin, and
* **holographic (SLM)** phase masks, computed and saved by Wavefront IV (replaces the MATLAB DHgui).

Power is always 100 % (no intensity modulation). Protocols run in Plerion.

Formerly named **Aule**: the settings in `~/.aule/config.json` are picked up on first start.

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

**GENERATE PHASE MASKS** (Wavefront IV open with *Tools → Script engine → auto load (script
folder)* selected): one mask per spot, saved by Wavefront as `Pattern1_000.algoPhp.png` …
`PatternN…` (DHgui's names, the ones Plerion counts).
* The button is **yellow** while the script or the phase mask folder is not empty: pressing it
  then shows a warning listing what is left (scripts Wavefront did not take, the masks' files and
  dates, YYYY MM DD) and sweeps both folders only if you say so.
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
* Scripts are dropped as `test.js` + `zdone.js` (as DHgui and Plerion do), one at a time, and
  each mask is checked: its png must be written, not empty, after its script was sent.
* While it runs, the button is a red **STOP**.
* A script still there after 10 s (Wavefront not reading the folder: check the auto load option),
  or a mask not saved 10 s after Wavefront took its script, is erased and sent again; the log
  counts the attempts (1/3, 2/3). At 3/3 the generation is aborted (with a missing mask: restart
  Wavefront).
* After STOP or an abort Oriole sends nothing more and waits 2 s (Wavefront may still be
  computing; the button counts down), then empties both Wavefront folders, every file whatever
  its name (they were empty when the generation started), and again every second while a file is
  left (still being saved or open), 10 sweeps at most: Generate is ready again.
* `Pattern_spots.json` next to the masks says which spot each mask is.

**Wavefront folder** (holo *Settings*, `E:\wfdIV_20261022` by default): the scripts go to its
`script\` and the masks are read from its `GeneratedPhasemasks\`; both paths are shown below it.
The 🧹 next to the script folder sweeps it clean at once, the one next to the phase mask folder
after a confirmation (it lists how many files and their dates). 📂 opens the phase mask folder in
the file explorer.

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
onto the SLM (the script folder is emptied first; a script not taken within 10 s is erased and
sent again, 3 attempts in all). Its spot
gets a purple *SLM* ring on the image, where it was clicked (read from `Pattern_spots.json`; a
mask with no record there, or made again since, e.g. by DHgui, is loaded without a ring). *Open* /
*Close* drive the shutter (NI-DAQ output from the holo *Settings*, `Dev1/ao0` by default;
10 V = open, as Plerion). Oriole only holds that output while the shutter is open and closes it
on exit: close it before starting a Plerion protocol.

## Layout

```
oriole/
  calibration.py  the 5-number manual calibration, the expected epi image size
  visual.py       spots -> DMD discs, spot bin + json, merge into a linked bin
  holo.py         Wavefront scripts and field, script drop, mask checks, folder sweeps, spot record
  shutter.py      NI-DAQ analog-output shutter
  binfile.py      lab bin reader / writer (numpy 2)
  images.py       image loading (grey)
  config.py       settings + calibrations (~/.oriole/config.json)
  theme.py        colours and styles (dark theme from Plerion)
  widgets.py      the image view (spots, SLM ring, Wavefront's field), calibration box, small factories
  gui.py          the window: settings, spots, extract / merge, Wavefront jobs (generate, Load), shutter
tests/            pytest (Wavefront and NI-DAQ simulated)
```

Later: Micro-Manager as the image source (snap, then live) through `MainWindow.set_image`.
