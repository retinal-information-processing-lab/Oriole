# Oriole — visual & holographic pattern generation

Desktop tool to turn a microscope image (epi / 2p, e.g. GFP cells) into stimulation patterns.
You drop spots (or draw outlines) on the image once; the mode decides what they become:

* **Visual (DMD)**: DMD frames for patterned optogenetic stimulation, written as a small
  standalone `.bin` and mergeable into an existing stimulus `.bin`. For every pattern the tool
  produces the **pattern frame** (light on the selected cells, to be shown with the activating
  LED) and its **negative** (light everywhere else, to be shown with the inactive LED) so that
  the total luminance is uniform over the retina.
* **Holo (SLM)**: one phase mask per spot, computed and saved by Wavefront IV from scripts
  Oriole drops in its script folder (replaces the MATLAB DHgui), plus a manual panel to show
  any phase mask on the SLM and open / close the shutter. Protocols themselves run in Plerion.

Spots are shared by the two modes; each mode has its own calibration. Power is always 100 %
(no intensity modulation).

Formerly named **Aule**: on first start the settings in `~/.aule/config.json` are picked up and
saved from then on to `~/.oriole/`.

## Install

One-click launchers (they create the `oriole` conda env from `environment.yml` on first
use, then start the GUI detached, so no console stays open): double-click `Oriole.bat` on
Windows, run `./Oriole.sh` on Linux (GUI output goes to `~/.oriole/oriole.log`). `Oriole.desktop` can be copied to the desktop / `~/.local/share/applications` for a
menu entry (its `Exec` / `Path` lines hold the absolute path of this folder: edit them if it moves).

Manual install, dedicated conda environment (Python 3.14, numpy 2, PyQt6; Linux / Windows / macOS):

```
conda env create -f environment.yml
conda activate oriole
```

Or with pip in any Python 3.11+ (virtualenv recommended):

```
pip install -r requirements.txt
```

Run everything from this folder:

```
python -m oriole gui                      # the application
python -m oriole calib-bin --out calibration_target_MEA3.bin
python -m oriole merge --bin orig.bin --patterns 2026_10_23_3Frames.bin --out orig_cells.bin
python -m oriole info some.bin
python -m pytest tests                    # self-tests
```

Rig defaults (rig 3, 672x672 frame, 2.5 µm/px; Wavefront folders `E:\wfdIV_20261022\script`
and `E:\wfdIV_20261022\GeneratedPhasemasks`; shutter on `Dev1/ao0`, 10 V = open) are editable in
*Settings > Rig settings* and stored in `~/.oriole/config.json` (or `--config` / `--rig` /
`--dmd-size` / `--pixel-size` on the CLI). The shutter needs `nidaqmx` and the NI-DAQmx driver;
without them Oriole runs normally and the shutter buttons report the error.

## Workflow

The application opens on **1. Select spots**. The mode (*Visual (DMD)* / *Holo (SLM)*) is chosen
in the toolbar and remembered; it switches the calibration and the second tab. The spots stay
when switching. Each calibration lives in its own tab and only needs redoing after the optics
changed (or at regular intervals). The date of the last calibration is shown at the top right
of the Select tab (green = recent, orange = older than *warn if calibration older than* in the
settings or not saved, red = none).

### Visual mode (DMD)

1. **Calibration** (tab *Calibration*)
   * Display frame 0 of the calibration bin (`calibration_target_MEA3.bin`, kept in the common
     stimulus folder; *Export calibration bin* regenerates it, or `python -m oriole calib-bin`).
     The target is a cross at the centre (centring), a ring (scale), a bar towards +x (rotation)
     and a dot in the upper-right quadrant (mirror check).
   * Image the projected target with the camera in the same configuration as the images used to
     select cells, then *Load camera image*.
   * Align the magenta template on the projected light: drag it to centre the cross, corner
     handles scale + rotate, the right-edge handle rotates only, tick *mirror* if the dot lands on
     the wrong side of the bar. Fine-tune with the centre / scale / angle boxes or *Refine
     automatically* (local optimisation of the placement on the image intensity; the *template
     signal* should get close to 1).
   * *Save calibration*: the placement (centre, scale, angle, mirror) is the calibration, saved
     as JSON with its date. It reloads automatically next time. *Use identity* if the image is
     already in DMD frame coordinates.
2. **Select spots** (live)
   * *Load image* (TIFF 8/16-bit, PNG, JPG; channels selectable, z-stacks max-projected).
     Contrast with the histogram on the right of the image, zoom with the wheel, pan with the
     right mouse button or in *Select* mode.
   * `C` circle: click drops a spot of the chosen diameter (µm; with a spot selected, the box
     resizes it). `F` freehand: drag around a cell.
     `S` select: drag shapes, handles to resize / move vertices. `Del` removes, `Ctrl+Z` undoes.
   * Shapes can be renamed (double-click) and disabled (untick) in the list. *margin* dilates
     every shape by that many µm in the DMD frame.
   * The preview shows the real DMD frame (image warped into DMD space, pattern in red, toggle
     *show negative*). Shapes leaving the DMD field are flagged.
   * *Export*: choose a folder; writes
     * `YYYY_MM_DD_<N>Frames.bin`, a small standalone bin like the classic stimulus bins:
       pos then neg frame for each of the N patterns (2N frames), with
       `<bin>.indices.json` listing the frame indices. A second export the same day with the
       same N gets `_2`, `_3`... instead of overwriting.
     * `<name>.patterns.npz` (the masks — source of truth), `<name>.patterns.json` (shapes,
       calibration, image path; reloadable with *Load pattern set*) and `<name>_previews/` PNGs.

     Tick *combined* for one pattern with all shapes and/or *per shape* for one pattern per shape.
   * Everything is autosaved to `~/.oriole/autosave.patterns.json`.
3. **Merge into bin** (GUI tab or CLI, usable on any number of bins with the exact same masks)
   * Pick the original (linked) bin, add one or more small pattern bins
     (`YYYY_MM_DD_NFrames.bin`) or `.patterns.npz`, pick an output path.
   * The output is the original frames byte for byte, followed by `pos, neg` for each mask
     (a small bin is copied byte for byte, so merging it or its `.npz` gives the same file).
     The original bin is never modified, so all existing vec indices stay valid.
   * `<output>.indices.json` lists `pos_idx` / `neg_idx` for every frame — use these in the vec
     generation script in place of the full-field background index.

### Holo mode (SLM)

Wavefront IV must be running with *script auto-load* on (its `script` folder is the one in the
settings). Oriole drives it the way DHgui and Plerion do: it writes `test.js`, then an empty
`zdone.js`, and waits for Wavefront to take the script before sending the next one.

1. **Calibration** (tab *Calibration*, holo version): typed transform from camera pixels to
   Wavefront µm, as in `DHparams.m`: pixel size (µm / camera px), ΔX, ΔY, θ, mirror y (on by
   default) / x. Spots are centred on the image, mirrored, rotated, scaled, then shifted. Edits
   apply immediately. To calibrate: drop a few spots, *Send spots to SLM (live)*, open the
   shutter, image the spots, adjust, send again; *Save* when they match (reloaded at start-up).
   Until a calibration is saved, the `DHparams.m` values are used (0.67 µm/px, ΔX 5, ΔY -8, θ 0).
2. **Select spots**: same image and spots as in visual mode (freehand outlines are listed as not
   used). The table gives each spot in Wavefront µm; the magenta cross marks where Wavefront
   (0, 0) — the zero order — falls on the image.
   * *Send spots to SLM (live)*: one pattern with all enabled spots, computed and shown but not
     saved (calibration, quick manual stimulation).
   * *Generate phase masks…*: one mask per enabled spot, saved by Wavefront as
     `Pattern1_000.algoPhp.png` … `PatternN_000.algoPhp.png` (the names Plerion's DH tab counts) in
     the phase mask folder. If old `Pattern<n>` files are there, Oriole shows their date
     (`YYYY MM DD`) and offers to delete them first, since Plerion would count them as spots. A
     record `<name>.holo.json` (spots in camera px and µm, calibration, image, masks) is written
     to the folder you choose.
3. **Phase masks & shutter**: lists the saved masks; *Load to SLM* (or double-click) shows one on
   the SLM. *Open* / *Close* / *Pulse* drive the shutter (same controls under the spot table).
   Oriole holds the NI-DAQ output only while the shutter is open and closes it on exit: close it
   before starting a Plerion protocol.

## Frame conventions

* Frames are written through the same `BinFile.append` path as the grating stimuli (rig 3:
  polarity inverted then flipped horizontally), so a pattern mask value of 1 means light on.
* `oriole/binfile.py` is the lab reader/writer updated for numpy 2 (`np.float` and
  `np.fromstring` were removed) with `read_frame_uint8()` to get back the logical frame.

## Layout

```
oriole/
  config.py       rig settings (DMD, Wavefront folders, shutter)
  binfile.py      bin reader / writer (+ raw byte copy)
  calibration.py  calibration target, similarity fit / refinement, Calibration JSON
  geometry.py     Circle / Polygon shapes, simplification, rasterisation to DMD masks
  patterns.py     PatternSet: frames (combined / per shape), export / load, dated bin name
  merge.py        masks as pos/neg frames: small standalone bin, merge into a bin, index JSON
  holo.py         HoloCalibration (camera -> Wavefront µm, as DHgui), spots, .holo.json record
  wavefront.py    Wavefront IV scripts (live / one mask / load) and the file-drop client
  shutter.py      NI-DAQ analog-output shutter
  images.py       image loading, warp to DMD space
  cli.py          gui | calib-bin | merge | info
  gui/            PyQt6 + pyqtgraph application (select / merge / phase masks / calibration tabs)
tests/            pytest (core + offscreen GUI end-to-end, Wavefront and NI-DAQ simulated)
```

## Later

Micro-Manager as the image source (snap, then live feed, e.g. with pycromanager): frames go in
through `SelectTab.set_channels`, the same path as a loaded file.
