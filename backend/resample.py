"""Audio resampling - change sample rate and bit depth of audio files."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed


def _build_folder_label(sample_rate: str, bit_depth: str) -> str:
    parts = []
    if bit_depth:
        parts.append(f"{bit_depth}bit")
    rate_map = {
        "44100": "44.1kHz", "48000": "48kHz",
        "96000": "96kHz", "192000": "192kHz",
    }
    if sample_rate:
        parts.append(rate_map.get(sample_rate, f"{sample_rate}Hz"))
    return " ".join(parts) if parts else "Resampled"


def _probe_audio(path: str) -> tuple[int, int]:
    """Return (sample_rate, bits_per_sample) for an audio file via ffprobe."""
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,bits_per_raw_sample,bits_per_sample",
                "-of", "default=noprint_wrappers=0", path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        kv = {}
        for line in r.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        sr = int(kv.get("sample_rate", "0") or 0)
        bits = 0
        raw = kv.get("bits_per_raw_sample", "N/A")
        if raw and raw != "N/A":
            bits = int(raw)
        if not bits:
            bps = kv.get("bits_per_sample", "N/A")
            if bps and bps != "N/A":
                bits = int(bps)
        return sr, bits
    except Exception:
        return 0, 0


def resample_audio(input_files: list[str], sample_rate: str = "",
                   bit_depth: str = "", delete_original: bool = False) -> list[dict]:
    """Resample audio files to the specified sample rate and/or bit depth.
    Returns list of {input_file, output_file, success, error}.
    Uses ffmpeg subprocess directly.
    
    Args:
        input_files: List of input file paths
        sample_rate: Target sample rate (e.g., "44100", "48000")
        bit_depth: Target bit depth (e.g., "16", "24")
        delete_original: If True, delete original files after successful resample
    """
    if not sample_rate and not bit_depth:
        raise ValueError("At least one of sample_rate or bit_depth must be specified")
    return _resample_subprocess(input_files, sample_rate, bit_depth, delete_original)


def _resample_subprocess(input_files: list[str], sample_rate: str,
                          bit_depth: str, delete_original: bool = False) -> list[dict]:
    """Resample via direct ffmpeg subprocess (fallback when ffmpeg-python absent)."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
    except Exception:
        raise RuntimeError("ffmpeg not found — install FFmpeg")

    folder_label = _build_folder_label(sample_rate, bit_depth)

    def process(input_file: str) -> dict:
        result = {
            "input_file": input_file,
            "output_file": "",
            "success": False,
            "skipped": False,
            "skip_reason": "",
            "error": "",
        }
        try:
            input_dir = os.path.dirname(input_file)
            base = os.path.splitext(os.path.basename(input_file))[0]
            ext = os.path.splitext(input_file)[1].lower()
            output_dir = os.path.join(input_dir, folder_label)
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, f"{base}.flac")
            result["output_file"] = output_file

            # Skip files already in the destination resample folder.
            if os.path.basename(os.path.dirname(input_file)) == folder_label:
                result["skipped"] = True
                result["skip_reason"] = "already_in_resampled_folder"
                return result

            # Skip if target output already exists.
            if os.path.exists(output_file):
                result["skipped"] = True
                result["skip_reason"] = "output_already_exists"
                return result

            # Skip FLAC files that already match the requested target format.
            if ext == ".flac":
                in_sr, in_bits = _probe_audio(input_file)
                matches_rate = (not sample_rate) or (in_sr == int(sample_rate))
                matches_bits = (not bit_depth) or (in_bits == int(bit_depth))
                if matches_rate and matches_bits:
                    result["skipped"] = True
                    result["skip_reason"] = "already_target_format"
                    return result

            args = ["ffmpeg", "-i", input_file, "-y"]
            if bit_depth:
                if bit_depth == "16":
                    args += ["-c:a", "flac", "-sample_fmt", "s16"]
                elif bit_depth == "24":
                    args += ["-c:a", "flac", "-sample_fmt", "s32",
                             "-bits_per_raw_sample", "24"]
                else:
                    args += ["-c:a", "flac"]
            else:
                args += ["-c:a", "flac"]
            if sample_rate:
                args += ["-ar", sample_rate]
            args += ["-map_metadata", "0", output_file]

            r = subprocess.run(args, capture_output=True, timeout=600)
            if r.returncode != 0:
                result["error"] = r.stderr.decode()[-500:]
            else:
                result["success"] = True
                # Delete original file if requested and resample was successful
                if delete_original and os.path.isfile(input_file):
                    try:
                        os.remove(input_file)
                        result["original_deleted"] = True
                    except Exception as e:
                        result["delete_error"] = str(e)
        except Exception as e:
            result["error"] = str(e)
        return result

    with ThreadPoolExecutor(max_workers=min(4, len(input_files))) as pool:
        futures = {pool.submit(process, f): f for f in input_files}
        return [fut.result() for fut in as_completed(futures)]


def get_flac_info_batch(paths: list[str]) -> list[dict]:
    """Get sample rate and bit depth for a batch of audio files."""
    return _probe_subprocess(paths)


def _probe_subprocess(paths: list[str]) -> list[dict]:
    """Probe audio files using ffprobe subprocess (fallback)."""
    def probe(path: str) -> dict:
        info = {"path": path, "sample_rate": 0, "bits_per_sample": 0}
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=sample_rate,bits_per_raw_sample,bits_per_sample",
                 "-of", "default=noprint_wrappers=0", path],
                capture_output=True, text=True, timeout=30,
            )
            kv = {}
            for line in r.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()
            if kv.get("sample_rate"):
                info["sample_rate"] = int(kv["sample_rate"])
            bits = 0
            raw = kv.get("bits_per_raw_sample", "N/A")
            if raw and raw != "N/A":
                bits = int(raw)
            if not bits:
                bps = kv.get("bits_per_sample", "N/A")
                if bps and bps != "N/A":
                    bits = int(bps)
            info["bits_per_sample"] = bits
        except Exception:
            pass
        return info

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(paths)))) as pool:
        return list(pool.map(probe, paths))
