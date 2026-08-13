# Seestar Solar Timelapse Stabilizer

A small CLI tool for stabilizing Seestar RAW8 solar/eclipse timelapses affected by abrupt `Center Target` re-centering jumps. It reads the uncompressed Bayer frames directly from the OpenDML AVI, removes sudden translation discontinuities while preserving slow natural motion, debayers the corrected frames, and encodes the result with FFmpeg.

## Requirements

- Python **3.12+** (64-bit recommended)
- `numpy`
- `opencv-python`
- FFmpeg **6+** recommended, available in `PATH`, with the `libx264` encoder enabled (tested with FFmpeg 9.0)
- A Seestar RAW timelapse AVI containing uncompressed **RAW8** frames
- The matching Seestar sidecar file (`<video>.avi.txt`) is recommended so the Bayer pattern can be detected automatically

Install the Python dependencies:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install numpy opencv-python
```

Verify FFmpeg:

```powershell
ffmpeg -version
```

## Usage

```powershell
python .\seestar_solar_timelapse_stabilizer.py `
  ".\2026-08-12-192206-Solar-timelapse-RAW.avi"
```

The default output is `<input-name>-stabilized.mp4`. Useful options:

```powershell
# Different output frame rate
python .\seestar_solar_timelapse_stabilizer.py input.avi --output-fps 48

# Analyze tracking without rendering
python .\seestar_solar_timelapse_stabilizer.py input.avi --analyze-only

# Optional per-frame diagnostics
python .\seestar_solar_timelapse_stabilizer.py input.avi --diagnostics-csv tracking.csv

# If no .avi.txt sidecar exists
python .\seestar_solar_timelapse_stabilizer.py input.avi --bayer GRBG
```

Run `python .\seestar_solar_timelapse_stabilizer.py --help` for all options.
