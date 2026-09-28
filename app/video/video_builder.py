import subprocess
import tempfile
from pathlib import Path

from app.config import VideoOptions

# NVENC (h264_nvenc) ile ölçülen gerçek CQ 34, statik slayt + altyazı + Ken Burns
# senaryosunda mevcut libx264 stillimage çıktısına görsel olarak eşdeğer (yan yana karşılaştırıldı)
# ve dosya boyutu benzer/daha küçük çıkıyor (bkz. commit notu / KAVRA_PROJECT_HANDOFF.md).
_NVENC_ARGS = ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "34", "-b:v", "0"]
_X264_ARGS = ["-tune", "stillimage"]

_encoder_cache: str | None = None  # süreç başına bir kez tespit edilir; "h264_nvenc" ya da "libx264"


def _probe_hardware_encoder() -> str:
    """NVENC gerçekten bu makinede çalışıyor mu (yalnızca `ffmpeg -encoders` listesinde
    görünmesi yeterli değil; sürücü/donanım sorunuysa gerçek bir encode denemesi gerekir).

    Başarısız olursa (GPU yok, sürücü sorunu, VPS gibi donanım hızlandırmasız bir makine)
    sessizce `libx264`'e döner — hiçbir zaman render'ı bu yüzden durdurmaz."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.mp4"
            # 64x64 gibi çok küçük bir kare NVENC'in minimum boyut sınırını aşıp
            # "Frame Dimension less than the minimum supported value" ile başarısız oluyor;
            # 256x256 tüm GPU nesillerinde güvenli.
            proc = subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-frames:v", "1",
                 "-c:v", "h264_nvenc", str(out)],
                capture_output=True, timeout=15,
            )
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return "h264_nvenc"
    except Exception:
        pass
    return "libx264"


def pick_video_encoder() -> tuple[str, list[str]]:
    """Bu render süreci için kullanılacak video encoder'ı ve sabit argümanlarını döndürür.

    Sonuç süreç ömrü boyunca önbelleklenir: her segment için yeniden GPU probe'u yapmak
    (~ her segmentte 100-200ms ekstra ffmpeg başlatma) gereksiz, izole render worker zaten
    her render'da yeni bir süreç olarak başlıyor."""
    global _encoder_cache
    if _encoder_cache is None:
        _encoder_cache = _probe_hardware_encoder()
    if _encoder_cache == "h264_nvenc":
        return "h264_nvenc", _NVENC_ARGS
    return "libx264", _X264_ARGS


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
                   opts: VideoOptions, srt_path: Path | None = None,
                   reveal_stages: list[tuple[Path, float]] | None = None):
    """reveal_stages verilirse (bkz. app/pipeline.py _build_bullet_reveal_stages), `image_path`
    yoksayılır: onun yerine sırayla gösterilecek (aşama_görseli, süre) listesi kullanılır —
    her aşama kendi süresi kadar `-loop 1 -t` ile decode edilip filter_complex'te concat
    filtresiyle TEK bir video akışında birleştirilir, sonra altyazı/fade/format normal
    tek-görsel yolundaki gibi bu birleşik akışa uygulanır. Ken Burns bu yolda desteklenmiyor
    (her aşama kendi zoompan'ıyla ayrı bir görsel olurdu, geçişte göze batan bir sıçrama
    yaratır) — reveal aktifken o slaytta Ken Burns sessizce devre dışı kalır."""
    w, h, fps = opts.width, opts.height, opts.fps

    sub_filter = None
    if opts.subtitles and srt_path and srt_path.exists() and srt_path.stat().st_size > 0:
        sub_filter = f"subtitles='{_escape_subtitle_path(srt_path)}'"

    fade_filter = None
    af = None
    if opts.fade_transitions and duration > 1.0:
        fd = 0.4
        fade_filter = f"fade=t=in:st=0:d={fd},fade=t=out:st={max(duration - fd, 0):.2f}:d={fd}"
        af = f"afade=t=in:st=0:d={fd},afade=t=out:st={max(duration - fd, 0):.2f}:d={fd}"

    if reveal_stages:
        inputs = []
        filter_parts = []
        stage_labels = []
        for idx, (stage_path, stage_duration) in enumerate(reveal_stages):
            inputs += ["-loop", "1", "-t", f"{max(stage_duration, 0.05):.3f}", "-i", str(stage_path)]
            label = f"v{idx}"
            filter_parts.append(f"[{idx}:v]fps={fps},scale={w}:{h}[{label}]")
            stage_labels.append(f"[{label}]")
        audio_index = len(reveal_stages)
        inputs += ["-i", str(audio_path)]
        filter_parts.append(f"{''.join(stage_labels)}concat=n={len(reveal_stages)}:v=1:a=0[vcat]")

        current = "vcat"
        for name, flt in (("vsub", sub_filter), ("vfade", fade_filter)):
            if flt:
                filter_parts.append(f"[{current}]{flt}[{name}]")
                current = name
        filter_parts.append(f"[{current}]format=yuv420p[vout]")

        base_cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts),
                    "-map", "[vout]", "-map", f"{audio_index}:a"]
        if af:
            base_cmd += ["-af", af]
        base_cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", "-t", f"{duration:.3f}"]
    else:
        chain = []
        if opts.ken_burns:
            frames = max(int(duration * fps), 1)
            chain.append(
                f"zoompan=z='min(zoom+0.0008,1.07)':d={frames}:s={w}x{h}:fps={fps}"
            )
        else:
            chain.append(f"scale={w}:{h}")
            chain.append(f"fps={fps}")

        if sub_filter:
            chain.append(sub_filter)
        if fade_filter:
            chain.append(fade_filter)

        chain.append("format=yuv420p")
        vf = ",".join(chain)

        base_cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path), "-vf", vf]
        if af:
            base_cmd += ["-af", af]
        base_cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest", "-t", f"{duration:.3f}"]

    encoder, encoder_args = pick_video_encoder()
    cmd = [*base_cmd, "-c:v", encoder, *encoder_args, str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and encoder != "libx264":
        # NVENC bir render ortasında başarısız olabilir (sürücü hıçkırığı, eşzamanlı oturum
        # sınırı); tüm render'ı düşürmek yerine bu tek segmenti CPU'da yeniden dener. Sonraki
        # segmentler yine NVENC dener — _encoder_cache burada kalıcı olarak değiştirilmiyor,
        # tek seferlik bir arıza kalıcı bir GPU sorunuymuş gibi tüm render'ı CPU'ya zorlamasın.
        cmd = [*base_cmd, "-c:v", "libx264", *_X264_ARGS, str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg segment hatası ({out_path.name}):\n{proc.stderr[-3000:]}")


def _escape_ffmetadata_value(text: str) -> str:
    """bkz. https://ffmpeg.org/ffmpeg-formats.html#Metadata-1 — ffmetadata formatı
    `\\`, `=`, `;`, `#` ve satır sonunu ters taksiyle kaçırılmasını istiyor, aksi halde
    bölüm başlığı ya da dosyanın geri kalanı yanlış ayrıştırılır."""
    result = []
    for ch in text:
        if ch in "\\=;#\n":
            result.append("\\")
        result.append(ch)
    return "".join(result)


def write_chapters_file(chapters: list[tuple[float, str]], total_duration: float, path: Path) -> None:
    """`chapters`: artan (başlangıç_saniye, başlık) listesi. `total_duration` son bölümün
    bitiş zamanını belirlemek için gerekir. VLC/mpv gibi oynatıcılar bu bölümleri video
    içinde atlanabilir işaretler olarak gösterir; içerik/render mantığını hiç etkilemez."""
    lines = [";FFMETADATA1"]
    for idx, (start, title) in enumerate(chapters):
        end = chapters[idx + 1][0] if idx + 1 < len(chapters) else total_duration
        end = max(end, start + 0.01)
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={int(start * 1000)}")
        lines.append(f"END={int(end * 1000)}")
        lines.append(f"title={_escape_ffmetadata_value(title)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def concat_videos(segment_paths: list[Path], out_path: Path, list_file: Path,
                   chapters_file: Path | None = None):
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in segment_paths), encoding="utf-8"
    )
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if chapters_file is not None and chapters_file.exists():
        cmd += ["-f", "ffmetadata", "-i", str(chapters_file), "-map", "0", "-map_metadata", "1"]
    cmd += ["-c", "copy", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


def concat_audio(audio_paths: list[Path], out_path: Path, list_file: Path):
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in audio_paths), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:a", "libmp3lame", "-q:a", "2", str(out_path)],
        check=True, capture_output=True,
    )
