import subprocess
from pathlib import Path

from app.config import VideoOptions


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _escape_subtitle_path(path: Path) -> str:
    p = str(path.resolve()).replace("\\", "/")
    p = p.replace(":", "\\:")
    return p


def build_segment(image_path: Path, audio_path: Path, duration: float, out_path: Path,
                   opts: VideoOptions, srt_path: Path | None = None):
    w, h, fps = opts.width, opts.height, opts.fps
    chain = []

    if opts.ken_burns:
        frames = max(int(duration * fps), 1)
        chain.append(
            f"zoompan=z='min(zoom+0.0008,1.07)':d={frames}:s={w}x{h}:fps={fps}"
        )
    else:
        chain.append(f"scale={w}:{h}")
        chain.append(f"fps={fps}")

    if opts.subtitles and srt_path and srt_path.exists() and srt_path.stat().st_size > 0:
        sub = _escape_subtitle_path(srt_path)
        chain.append(f"subtitles='{sub}'")

    af = None
    if opts.fade_transitions and duration > 1.0:
        fd = 0.4
        chain.append(f"fade=t=in:st=0:d={fd}")
        chain.append(f"fade=t=out:st={max(duration - fd, 0):.2f}:d={fd}")
        af = f"afade=t=in:st=0:d={fd},afade=t=out:st={max(duration - fd, 0):.2f}:d={fd}"

    chain.append("format=yuv420p")
    vf = ",".join(chain)

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path)]
    cmd += ["-vf", vf]
    if af:
        cmd += ["-af", af]
    cmd += ["-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-t", f"{duration:.3f}", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg segment hatası ({out_path.name}):\n{proc.stderr[-3000:]}")


def concat_videos(segment_paths: list[Path], out_path: Path, list_file: Path):
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in segment_paths), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        check=True, capture_output=True,
    )


def concat_audio(audio_paths: list[Path], out_path: Path, list_file: Path):
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in audio_paths), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:a", "libmp3lame", "-q:a", "2", str(out_path)],
        check=True, capture_output=True,
    )
