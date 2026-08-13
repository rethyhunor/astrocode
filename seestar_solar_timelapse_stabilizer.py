#!/usr/bin/env python3
"""Stabilize Seestar RAW8 solar/eclipse timelapse AVI files.

The tool reads uncompressed RAW Bayer frames directly from the OpenDML/AVI
container, detects abrupt re-centering jumps, preserves slow natural motion,
debayers the corrected frames and streams them to FFmpeg for H.264 output.

Tested with a Seestar S30 Pro RAW8 solar-eclipse timelapse (IMX585, 2160x3840,
Bayer=GR -> GRBG). It is intended for solar timelapses where the Sun/crescent
is the dominant bright object in the frame.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

SIDECAR_BAYER_MAP = {
    "RG": "RGGB", "GR": "GRBG", "BG": "BGGR", "GB": "GBRG",
    "RGGB": "RGGB", "GRBG": "GRBG", "BGGR": "BGGR", "GBRG": "GBRG",
}

# OpenCV legacy aliases are not literal CFA names.
SHORT_BAYER_ALIAS = {
    "RGGB": "BG", "GRBG": "GB", "BGGR": "RG", "GBRG": "GR",
}


def u32(data: bytes) -> int:
    return struct.unpack("<I", data)[0]


def i32(data: bytes) -> int:
    return struct.unpack("<i", data)[0]


def parse_sidecar(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def sidecar_for(avi: Path) -> Path:
    return Path(str(avi) + ".txt")


def resolve_bayer(cli_value: str | None, sidecar: dict[str, str]) -> str:
    if cli_value:
        return cli_value.upper()
    value = sidecar.get("Bayer")
    if not value:
        raise RuntimeError(
            "No Bayer pattern found in the sidecar. Use --bayer "
            "RGGB|GRBG|BGGR|GBRG."
        )
    key = value.upper()
    if key not in SIDECAR_BAYER_MAP:
        raise RuntimeError(f"Unsupported Bayer value '{value}'.")
    return SIDECAR_BAYER_MAP[key]


def demosaic_code(pattern: str, mode: str) -> int:
    suffix = {"bilinear": "", "ea": "_EA", "vng": "_VNG"}[mode]
    explicit = f"COLOR_Bayer{pattern}2BGR{suffix}"
    if hasattr(cv2, explicit):
        return getattr(cv2, explicit)
    short = SHORT_BAYER_ALIAS[pattern]
    legacy = f"COLOR_Bayer{short}2BGR{suffix}"
    if hasattr(cv2, legacy):
        return getattr(cv2, legacy)
    fallback = f"COLOR_Bayer{short}2BGR"
    if hasattr(cv2, fallback):
        print(f"Warning: '{mode}' demosaic unavailable; using bilinear.")
        return getattr(cv2, fallback)
    raise RuntimeError(f"No OpenCV Bayer conversion found for {pattern}.")


def find_video_bitmap_header(path: Path) -> dict:
    """Parse the first RIFF AVI segment and return the video DIB header."""
    file_size = path.stat().st_size
    with path.open("rb") as f:
        hdr = f.read(12)
        if len(hdr) != 12 or hdr[:4] != b"RIFF" or hdr[8:12] != b"AVI ":
            raise RuntimeError("Input does not start with a RIFF AVI header.")
        riff_end = min(8 + u32(hdr[4:8]), file_size)

        def iter_chunks(start: int, end: int):
            pos = start
            while pos + 8 <= end:
                f.seek(pos)
                h = f.read(8)
                if len(h) != 8:
                    return
                cid = h[:4]
                size = u32(h[4:8])
                ds = pos + 8
                le = ds + size
                pe = le + (size & 1)
                if le > end or pe <= pos:
                    return
                yield cid, size, ds, le
                pos = pe

        hdrl = None
        for cid, size, ds, le in iter_chunks(12, riff_end):
            if cid == b"LIST" and size >= 4:
                f.seek(ds)
                if f.read(4) == b"hdrl":
                    hdrl = (ds + 4, le)
                    break
        if hdrl is None:
            raise RuntimeError("AVI hdrl LIST not found.")

        stream_index = -1
        for cid, size, ds, le in iter_chunks(*hdrl):
            if cid != b"LIST" or size < 4:
                continue
            f.seek(ds)
            if f.read(4) != b"strl":
                continue
            stream_index += 1
            strh = None
            strf = None
            for scid, ssize, sds, sle in iter_chunks(ds + 4, le):
                f.seek(sds)
                if scid == b"strh":
                    strh = f.read(min(ssize, 64))
                elif scid == b"strf":
                    strf = f.read(ssize)
            if not strh or len(strh) < 8 or strh[:4] != b"vids":
                continue
            if not strf or len(strf) < 40:
                raise RuntimeError("Video stream has no BITMAPINFOHEADER.")
            width = i32(strf[4:8])
            height_signed = i32(strf[8:12])
            _, bit_count = struct.unpack("<HH", strf[12:16])
            compression = u32(strf[16:20])
            height = abs(height_signed)
            stride = ((width * bit_count + 31) // 32) * 4
            return {
                "stream_index": stream_index,
                "width": width,
                "height_signed": height_signed,
                "height": height,
                "bottom_up": height_signed > 0,
                "bit_count": bit_count,
                "compression": compression,
                "stride": stride,
                "frame_bytes": stride * height,
            }
    raise RuntimeError("No video stream found in the AVI header.")


def scan_frame_offsets(path: Path, stream_index: int, frame_bytes: int,
                       block_mb: int = 64) -> list[int]:
    """Find all uncompressed ##db frame chunks across the full OpenDML AVI."""
    signature = f"{stream_index:02d}db".encode("ascii") + struct.pack("<I", frame_bytes)
    overlap = len(signature) - 1
    block_size = max(1, block_mb) * 1024 * 1024
    offsets: list[int] = []
    carry = b""
    absolute_base = 0

    with path.open("rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            data = carry + block
            data_base = absolute_base - len(carry)
            pos = 0
            while True:
                hit = data.find(signature, pos)
                if hit < 0:
                    break
                offsets.append(data_base + hit + 8)
                pos = hit + 8
            carry = data[-overlap:] if len(data) >= overlap else data
            absolute_base += len(block)

    file_size = path.stat().st_size
    return sorted({
        off for off in offsets
        if 0 <= off and off + frame_bytes <= file_size
    })


def read_raw_frame(handle, payload_offset: int, header: dict) -> np.ndarray:
    handle.seek(payload_offset)
    payload = handle.read(header["frame_bytes"])
    if len(payload) != header["frame_bytes"]:
        raise RuntimeError(f"Short frame read at offset {payload_offset}.")
    rows = np.frombuffer(payload, dtype=np.uint8).reshape(
        header["height"], header["stride"]
    )
    raw = rows[:, :header["width"]].copy()
    if header["bottom_up"]:
        raw = np.flipud(raw).copy()
    return raw


def detect_solar_centroid(raw: np.ndarray, scale: float,
                           threshold_fraction: float,
                           min_brightness: float,
                           min_area: float):
    small = cv2.resize(raw, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    mx = float(small.max())
    if mx < min_brightness:
        return np.nan, np.nan, 0.0, mx
    threshold = max(4, int(mx * threshold_fraction))
    _, mask = cv2.threshold(small, threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return np.nan, np.nan, 0.0, mx
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < min_area:
        return np.nan, np.nan, area, mx
    m = cv2.moments(contour)
    if m["m00"] == 0:
        return np.nan, np.nan, area, mx
    cx = (m["m10"] / m["m00"]) / scale
    cy = (m["m01"] / m["m00"]) / scale
    return float(cx), float(cy), area, mx


def analyze_frames(avi: Path, offsets: list[int], header: dict,
                   scale: float, threshold_fraction: float,
                   min_brightness: float, min_area: float):
    centers = np.full((len(offsets), 2), np.nan, dtype=np.float64)
    areas = np.zeros(len(offsets), dtype=np.float64)
    brightness = np.zeros(len(offsets), dtype=np.float64)
    print("Pass 1/2: analyzing RAW frames...")
    with avi.open("rb") as f:
        for i, off in enumerate(offsets):
            raw = read_raw_frame(f, off, header)
            cx, cy, area, mx = detect_solar_centroid(
                raw, scale, threshold_fraction, min_brightness, min_area
            )
            centers[i] = (cx, cy)
            areas[i] = area
            brightness[i] = mx
            if (i + 1) % 100 == 0 or i + 1 == len(offsets):
                print(f"  analyzed {i + 1}/{len(offsets)}")
    return centers, areas, brightness


def moving_median_nan(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    radius = window // 2
    out = np.full_like(values, np.nan, dtype=np.float64)
    for i in range(len(values)):
        lo, hi = max(0, i - radius), min(len(values), i + radius + 1)
        chunk = values[lo:hi]
        for axis in (0, 1):
            valid = chunk[:, axis][np.isfinite(chunk[:, axis])]
            if len(valid):
                out[i, axis] = float(np.median(valid))
    return out


def calculate_stabilization(centers: np.ndarray, jump_threshold: float,
                            smooth_window: int):
    """Remove abrupt jumps, then suppress residual micro-jitter."""
    n = len(centers)
    jump_offsets = np.zeros((n, 2), dtype=np.float64)
    corrected = np.full((n, 2), np.nan, dtype=np.float64)
    jump_flags = np.zeros(n, dtype=np.uint8)
    jump_sizes = np.zeros(n, dtype=np.float64)
    accumulated = np.zeros(2, dtype=np.float64)
    last_valid = None
    last_valid_index = None

    for i, center in enumerate(centers):
        valid = np.all(np.isfinite(center))
        if valid and last_valid is not None:
            consecutive = last_valid_index is not None and i == last_valid_index + 1
            delta = center - last_valid
            magnitude = float(np.linalg.norm(delta))
            if consecutive and magnitude > jump_threshold:
                accumulated -= delta
                jump_flags[i] = 1
                jump_sizes[i] = magnitude
        jump_offsets[i] = accumulated
        if valid:
            corrected[i] = center + accumulated
            last_valid = center.copy()
            last_valid_index = i

    smoothed = moving_median_nan(corrected, smooth_window)
    micro_offsets = np.zeros((n, 2), dtype=np.float64)
    valid = np.isfinite(corrected[:, 0]) & np.isfinite(smoothed[:, 0])
    micro_offsets[valid] = smoothed[valid] - corrected[valid]
    total_offsets = jump_offsets + micro_offsets
    final_centers = centers + total_offsets
    return total_offsets, jump_offsets, micro_offsets, final_centers, jump_flags, jump_sizes


def write_csv(path: Path, centers: np.ndarray, total: np.ndarray,
              jumps: np.ndarray, micro: np.ndarray, final: np.ndarray,
              flags: np.ndarray, sizes: np.ndarray,
              areas: np.ndarray, brightness: np.ndarray):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "frame", "detected_x", "detected_y", "total_dx", "total_dy",
            "jump_dx", "jump_dy", "micro_dx", "micro_dy", "final_x", "final_y",
            "jump", "jump_size_px", "tracking_area_small_px", "max_brightness",
        ])
        for i in range(len(centers)):
            w.writerow([
                i, centers[i, 0], centers[i, 1], total[i, 0], total[i, 1],
                jumps[i, 0], jumps[i, 1], micro[i, 0], micro[i, 1],
                final[i, 0], final[i, 1], int(flags[i]), sizes[i],
                areas[i], brightness[i],
            ])


def require_ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("FFmpeg not found in PATH. Verify with: ffmpeg -version")
    result = subprocess.run(
        [binary, "-hide_banner", "-encoders"],
        capture_output=True, text=True, errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError("Could not query FFmpeg encoders.")
    if "libx264" not in result.stdout:
        raise RuntimeError("FFmpeg was found, but this build has no libx264 encoder.")
    return binary


def encode_video(avi: Path, output: Path, offsets: list[int], header: dict,
                 corrections: np.ndarray, bayer: str, demosaic: str,
                 output_fps: float, crf: int, preset: str, overwrite: bool):
    ffmpeg = require_ffmpeg()
    code = demosaic_code(bayer, demosaic)
    width, height = header["width"], header["height"]
    cmd = [
        ffmpeg, "-y" if overwrite else "-n", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-video_size", f"{width}x{height}",
        "-framerate", f"{output_fps:.6f}", "-i", "pipe:0", "-an",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    print("\nPass 2/2: debayering, stabilizing and encoding...")
    print(f"  output: {output}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    if proc.stdin is None:
        raise RuntimeError("Could not open FFmpeg input pipe.")
    try:
        with avi.open("rb") as f:
            for i, off in enumerate(offsets):
                raw = read_raw_frame(f, off, header)
                bgr = cv2.cvtColor(raw, code)
                dx, dy = corrections[i]
                transform = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
                stabilized = cv2.warpAffine(
                    bgr, transform, (width, height), flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)
                )
                proc.stdin.write(stabilized.tobytes(order="C"))
                if (i + 1) % 50 == 0 or i + 1 == len(offsets):
                    print(f"  encoded {i + 1}/{len(offsets)}")
    except BrokenPipeError as exc:
        raise RuntimeError("FFmpeg closed its input pipe unexpectedly.") from exc
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"FFmpeg failed with exit code {rc}.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stabilize Seestar RAW8 solar/eclipse timelapse AVI files."
    )
    p.add_argument("input", type=Path, help="Input Seestar RAW AVI file")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output MP4 (default: <input-stem>-stabilized.mp4)")
    p.add_argument("--bayer", choices=["RGGB", "GRBG", "BGGR", "GBRG"], default=None,
                   help="Override Bayer pattern; otherwise read .avi.txt sidecar")
    p.add_argument("--output-fps", type=float, default=48.0,
                   help="Output frame rate (default: 48)")
    p.add_argument("--jump-threshold", type=float, default=8.0,
                   help="Abrupt displacement threshold in full-resolution pixels (default: 8)")
    p.add_argument("--smooth-window", type=int, default=9,
                   help="Moving-median micro-jitter window; 1 disables (default: 9)")
    p.add_argument("--analysis-scale", type=float, default=0.25,
                   help="Tracking scale (default: 0.25)")
    p.add_argument("--threshold-fraction", type=float, default=0.22,
                   help="Solar threshold fraction of frame maximum (default: 0.22)")
    p.add_argument("--min-brightness", type=float, default=25.0,
                   help="Minimum frame brightness for tracking (default: 25)")
    p.add_argument("--min-area", type=float, default=80.0,
                   help="Minimum solar contour area at analysis scale (default: 80)")
    p.add_argument("--demosaic", choices=["ea", "vng", "bilinear"], default="ea",
                   help="OpenCV debayer mode (default: ea)")
    p.add_argument("--crf", type=int, default=12,
                   help="libx264 CRF; lower is higher quality (default: 12)")
    p.add_argument("--preset", default="slow", help="libx264 preset (default: slow)")
    p.add_argument("--diagnostics-csv", type=Path, default=None,
                   help="Optional per-frame tracking CSV")
    p.add_argument("--analyze-only", action="store_true",
                   help="Analyze tracking without encoding")
    p.add_argument("--overwrite", action="store_true",
                   help="Allow overwriting the output file")
    return p


def main() -> int:
    args = build_parser().parse_args()
    avi = args.input.expanduser().resolve()
    if not avi.is_file():
        raise RuntimeError(f"Input file not found: {avi}")
    if args.output_fps <= 0 or args.jump_threshold <= 0:
        raise RuntimeError("--output-fps and --jump-threshold must be > 0.")
    if args.smooth_window < 1:
        raise RuntimeError("--smooth-window must be >= 1.")
    if not 0.0 < args.analysis_scale <= 1.0:
        raise RuntimeError("--analysis-scale must be in (0, 1].")

    sidecar_path = sidecar_for(avi)
    sidecar = parse_sidecar(sidecar_path)
    header = find_video_bitmap_header(avi)
    if header["bit_count"] != 8:
        raise RuntimeError(f"Expected RAW8, AVI reports {header['bit_count']} bpp.")
    if header["compression"] != 0:
        raise RuntimeError(
            f"Expected uncompressed BI_RGB AVI, compression={header['compression']}."
        )
    bayer = resolve_bayer(args.bayer, sidecar)
    output = (args.output.expanduser().resolve() if args.output else
              avi.with_name(avi.stem + "-stabilized.mp4"))
    if output.exists() and not args.overwrite and not args.analyze_only:
        raise RuntimeError(f"Output exists: {output} (use --overwrite)")

    print("Seestar Solar Timelapse Stabilizer")
    print("---------------------------------")
    print(f"Input:              {avi}")
    print(f"Sidecar:            {sidecar_path if sidecar_path.exists() else 'not found'}")
    print(f"Dimensions:         {header['width']} x {header['height']}")
    print(f"Orientation:        {'bottom-up' if header['bottom_up'] else 'top-down'}")
    print(f"Bits/pixel:         {header['bit_count']}")
    print(f"Frame bytes:        {header['frame_bytes']:,}")
    print(f"Sidecar Bayer:      {sidecar.get('Bayer', 'n/a')}")
    print(f"Resolved CFA:       {bayer}")
    print(f"Demosaic:           {args.demosaic}")

    print("\nScanning the complete OpenDML AVI for RAW frames...")
    offsets = scan_frame_offsets(avi, header["stream_index"], header["frame_bytes"])
    if not offsets:
        raise RuntimeError("No matching uncompressed RAW video chunks found.")
    print(f"Found RAW frames:    {len(offsets)}")

    centers, areas, brightness = analyze_frames(
        avi, offsets, header, args.analysis_scale, args.threshold_fraction,
        args.min_brightness, args.min_area
    )
    total, jump_offsets, micro, final, flags, sizes = calculate_stabilization(
        centers, args.jump_threshold, args.smooth_window
    )

    valid = np.isfinite(centers[:, 0])
    jump_count = int(flags.sum())
    print("\nTracking summary")
    print("----------------")
    print(f"Valid tracking:      {int(valid.sum())}/{len(offsets)}")
    print(f"Detected jumps:      {jump_count}")
    if jump_count:
        vals = sizes[flags == 1]
        print(f"Median jump:         {float(np.median(vals)):.2f} px")
        print(f"Largest jump:        {float(np.max(vals)):.2f} px")
    print(f"Output duration:     {len(offsets) / args.output_fps:.2f} s at {args.output_fps:g} fps")

    if args.diagnostics_csv:
        csv_path = args.diagnostics_csv.expanduser().resolve()
        write_csv(csv_path, centers, total, jump_offsets, micro, final,
                  flags, sizes, areas, brightness)
        print(f"Diagnostics CSV:     {csv_path}")

    if args.analyze_only:
        print("\nAnalyze-only complete. No video was encoded.")
        return 0

    encode_video(
        avi, output, offsets, header, total, bayer, args.demosaic,
        args.output_fps, args.crf, args.preset, args.overwrite
    )
    print("\nDone.")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
