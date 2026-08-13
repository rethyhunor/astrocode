# Seestar Solar Timelapse Stabilizer

A command-line tool for stabilizing ZWO Seestar RAW8 solar and eclipse timelapses affected by abrupt `Center Target` re-centering jumps.

The tool reads uncompressed Bayer frames directly from the OpenDML AVI, detects and removes sudden translation discontinuities while preserving slow natural motion, debayers the corrected frames, and encodes the stabilized sequence to MP4 using FFmpeg.

## Compatibility and Testing

> [!IMPORTANT]\
> This tool has currently been tested **only with ZWO Seestar S30 Pro RAW8 timelapse AVI files**.

The tested input format is:

- ZWO **Seestar S30 Pro**
- Solar timelapse recorded in **RAW8**
- Uncompressed Bayer frames stored in an OpenDML `.avi` file
- Optional Seestar `.avi.txt` sidecar file for automatic Bayer pattern detection

Other Seestar models, recording modes, codecs, AVI variants, and image formats have **not been tested** and are not currently guaranteed to work.

If you successfully test the tool with another Seestar model or recording format, feedback and contributions are welcome.

## Requirements

- Python **3.12+**
- `numpy`
- `opencv-python`
- FFmpeg **6+** recommended
- FFmpeg must be available in `PATH`
- FFmpeg must include the `libx264` encoder
- A compatible Seestar RAW8 timelapse AVI

The matching Seestar sidecar file:

```text
<video>.avi.txt
```

is recommended because it allows the script to detect the Bayer pattern automatically.

Without the sidecar file, the Bayer pattern can be specified manually using `--bayer`.

## Installation

### Windows

Python 3.12 or newer must be installed and available from PowerShell.

Clone the repository and enter the project directory:

```powershell
git clone https://github.com/rethyhunor/astrocode.git
cd astrocode\seestar_solar_timelapse_stabilizer
```

Create a Python virtual environment:

```powershell
python -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

Upgrade `pip`:

```powershell
python -m pip install --upgrade pip
```

Install the required Python packages:

```powershell
python -m pip install numpy opencv-python
```

Install FFmpeg separately and make sure `ffmpeg.exe` is available in your system `PATH`.

Verify the installation:

```powershell
python --version
ffmpeg -version
```

### Linux

Clone the repository and enter the project directory:

```bash
git clone https://github.com/rethyhunor/astrocode.git
cd astrocode/seestar_solar_timelapse_stabilizer
```

Create a Python virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Upgrade `pip`:

```bash
python -m pip install --upgrade pip
```

Install the required Python packages:

```bash
python -m pip install numpy opencv-python
```

Install FFmpeg using your distribution's package manager.

For Debian/Ubuntu-based systems, for example:

```bash
sudo apt install ffmpeg
```

Verify the installation:

```bash
python --version
ffmpeg -version
```

### macOS

Clone the repository and enter the project directory:

```bash
git clone https://github.com/rethyhunor/astrocode.git
cd astrocode/seestar_solar_timelapse_stabilizer
```

Create a Python virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Upgrade `pip`:

```bash
python -m pip install --upgrade pip
```

Install the required Python packages:

```bash
python -m pip install numpy opencv-python
```

Install FFmpeg using your preferred package manager. With Homebrew:

```bash
brew install ffmpeg
```

Verify the installation:

```bash
python --version
ffmpeg -version
```

## Usage

### Basic usage

Windows PowerShell:

```powershell
python .\seestar_solar_timelapse_stabilizer.py `
  ".\2026-08-12-192206-Solar-timelapse-RAW.avi"
```

Linux and macOS:

```bash
python seestar_solar_timelapse_stabilizer.py \
  "./2026-08-12-192206-Solar-timelapse-RAW.avi"
```

By default, the stabilized video is written to:

```text
<input-name>-stabilized.mp4
```

The original AVI file is not modified.

## Examples

### Set a different output frame rate

```bash
python seestar_solar_timelapse_stabilizer.py input.avi --output-fps 48
```

### Analyze tracking without rendering a video

```bash
python seestar_solar_timelapse_stabilizer.py input.avi --analyze-only
```

### Export per-frame diagnostics

```bash
python seestar_solar_timelapse_stabilizer.py \
  input.avi \
  --diagnostics-csv tracking.csv
```

### Manually specify the Bayer pattern

If the corresponding `.avi.txt` sidecar file is unavailable:

```bash
python seestar_solar_timelapse_stabilizer.py \
  input.avi \
  --bayer GRBG
```

For all available command-line options:

```bash
python seestar_solar_timelapse_stabilizer.py --help
```

## Disclaimer

This software is provided as a community tool and is used **at your own risk**.

Although the program is designed not to modify the original input AVI, you should always keep a backup of your original recordings before processing them.

No guarantee is made that the software will work correctly with untested Seestar models, recording modes, file formats, operating systems, or future firmware versions.

The author is not responsible for data loss, corrupted output, unexpected behavior, or other damage resulting from the use of this software.

See the repository [LICENSE](../LICENSE) for the applicable license terms and warranty disclaimer.
