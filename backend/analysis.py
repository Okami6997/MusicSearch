"""Audio file analysis using FFprobe."""

import math
import os
import subprocess


def get_track_metadata(filepath: str) -> dict:
    """Analyze an audio file and return its technical metadata."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    return _analyze_with_ffprobe(filepath)


def _analyze_with_ffprobe(filepath: str) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries",
             "stream=sample_rate,channels,bits_per_raw_sample,"
             "bits_per_sample,duration,bit_rate,codec_name",
             "-of", "default=noprint_wrappers=0", filepath],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("ffprobe not found — install FFmpeg")

    info = {}
    for line in r.stdout.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    file_size = os.path.getsize(filepath)
    sample_rate = int(info.get("sample_rate", 0) or 0)
    channels = int(info.get("channels", 0) or 0)
    duration = float(info.get("duration", 0) or 0)
    bit_rate = int(info.get("bit_rate", 0) or 0) if info.get("bit_rate", "N/A") != "N/A" else 0

    bits = 0
    raw = info.get("bits_per_raw_sample", "N/A")
    if raw and raw != "N/A":
        bits = int(raw)
    if not bits:
        bps = info.get("bits_per_sample", "N/A")
        if bps and bps != "N/A":
            bits = int(bps)

    return {
        "file_path": filepath,
        "file_name": os.path.basename(filepath),
        "file_size": file_size,
        "codec": info.get("codec_name", ""),
        "sample_rate": sample_rate,
        "channels": channels,
        "bits_per_sample": bits,
        "bit_depth": f"{bits}-bit" if bits else "Unknown",
        "duration": round(duration, 2),
        "bit_rate": bit_rate,
    }


def get_audio_duration(filepath: str) -> float:
    """Get duration in seconds of an audio file."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip()) if r.stdout.strip() else 0.0
    except Exception:
        return 0.0


def validate_download_duration(filepath: str, expected_seconds: int) -> tuple[bool, str]:
    """Validate downloaded file duration against expected.
    Returns (valid, error_message)."""
    if not filepath or expected_seconds <= 0:
        return True, ""

    actual = get_audio_duration(filepath)
    if actual <= 0:
        return True, ""

    actual_sec = round(actual)

    # Detect preview/sample
    if expected_seconds >= 60 and actual_sec <= 35:
        return False, (
            f"Detected preview/sample: file is {actual_sec}s, "
            f"expected ~{expected_seconds}s"
        )

    # Large mismatch check
    if expected_seconds >= 90:
        allowed = max(15, round(expected_seconds * 0.25))
        diff = abs(actual_sec - expected_seconds)
        if diff > allowed:
            return False, (
                f"Duration mismatch: file is {actual_sec}s, "
                f"expected ~{expected_seconds}s"
            )

    return True, ""
