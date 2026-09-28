import subprocess
import tempfile
from pathlib import Path

from app.config import VideoOptions

# İnce yazı ve diyagram çizgileri, sıradan kamera görüntüsüne göre sıkıştırma
# artefaktlarını çok daha görünür kılar. Bu yüzden varsayılan profil özellikle
# slayt videosuna göre ayarlandı. Kullanıcı isterse arayüzden balanced/fast
# seçerek render süresini azaltabilir.
QUALITY_PRESETS = ("high", "balanced", "fast")
_NVENC_ARGS_BY_QUALITY = {
    "high": ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "20", "-b:v", "0",
             "-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8"],
    "balanced": ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "24", "-b:v", "0"],
    "fast": ["-preset", "p3", "-tune", "hq", "-rc", "vbr", "-cq", "28", "-b:v", "0"],
}
_X264_ARGS_BY_QUALITY = {
    "high": ["-preset", "slow", "-crf", "18", "-tune", "stillimage"],
    "balanced": ["-preset", "medium", "-crf", "20", "-tune", "stillimage"],
    "fast": ["-preset", "veryfast", "-crf", "23", "-tune", "stillimage"],
}
# Geriye dönük test/entegrasyon erişimi: parametre verilmezse high profili.
_NVENC_ARGS = _NVENC_ARGS_BY_QUALITY["high"]
_X264_ARGS = _X264_ARGS_BY_QUALITY["high"]
_VIDEO_OUTPUT_ARGS = [
    "-pix_fmt", "yuv420p",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    "-movflags", "+faststart",
]
# loudnorm tamamen sessiz bir girişte bazı FFmpeg sürümlerinde NaN üretip AAC
# kodlayıcısını düşürebiliyor. dynaudnorm konuşma seviyesini slaytlar arasında
# dengelerken sessiz/boş bir segmenti de güvenle geçirir.
_NARRATION_AUDIO_FILTER = "dynaudnorm=f=150:g=15:p=0.95:m=10"

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


def _quality(value: str) -> str:
    return value if value in QUALITY_PRESETS else "high"


def pick_video_encoder(quality_preset: str = "high") -> tuple[str, list[str]]:
    """Bu render süreci için kullanılacak video encoder'ı ve sabit argümanlarını döndürür.

    Sonuç süreç ömrü boyunca önbelleklenir: her segment için yeniden GPU probe'u yapmak
    (~ her segmentte 100-200ms ekstra ffmpeg başlatma) gereksiz, izole render worker zaten
    her render'da yeni bir süreç olarak başlıyor."""
    global _encoder_cache
    if _encoder_cache is None:
        _encoder_cache = _probe_hardware_encoder()
    quality = _quality(quality_preset)
    if _encoder_cache == "h264_nvenc":
        return "h264_nvenc", _NVENC_ARGS_BY_QUALITY[quality]
    return "libx264", _X264_ARGS_BY_QUALITY[quality]


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
    if opts.fade_transitions and duration > 1.0:
        fd = 0.25
        fade_filter = f"fade=t=in:st=0:d={fd},fade=t=out:st={max(duration - fd, 0):.2f}:d={fd}"

    if reveal_stages:
        inputs = []
        filter_parts = []
        stage_labels = []
        for idx, (stage_path, stage_duration) in enumerate(reveal_stages):
            inputs += ["-loop", "1", "-t", f"{max(stage_duration, 0.05):.3f}", "-i", str(stage_path)]
            label = f"v{idx}"
            filter_parts.append(f"[{idx}:v]fps={fps},scale={w}:{h}:flags=lanczos,setsar=1[{label}]")
            stage_labels.append(f"[{label}]")
        audio_index = len(reveal_stages)
        inputs += ["-i", str(audio_path)]
        filter_parts.append(f"{''.join(stage_labels)}concat=n={len(reveal_stages)}:v=1:a=0[vcat]")

        current = "vcat"
        for name, flt in (("vsub", sub_filter), ("vfade", fade_filter)):
            if flt:
                filter_parts.append(f"[{current}]{flt}[{name}]")
                current = name
        filter_parts.append(
            f"[{current}]format=yuv420p,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[vout]"
        )

        base_cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts),
                    "-map", "[vout]", "-map", f"{audio_index}:a"]
        base_cmd += ["-af", _NARRATION_AUDIO_FILTER, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-shortest", "-t", f"{duration:.3f}"]
    else:
        chain = []
        if opts.ken_burns:
            frames = max(int(duration * fps), 1)
            # Kaynak kareyi önce 2x Lanczos ile büyütmek ve hareketi merkeze
            # sabitlemek, doğrudan 1080p zoompan'in oluşturduğu titreşim/piksel
            # kırılmasını ve eski sol-üst köşe yönelimini giderir.
            chain.append(f"scale={w * 2}:{h * 2}:flags=lanczos")
            chain.append(
                f"zoompan=z='min(zoom+0.00045,1.045)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}"
            )
        else:
            chain.append(f"scale={w}:{h}:flags=lanczos")
            chain.append("setsar=1")
            chain.append(f"fps={fps}")

        if sub_filter:
            chain.append(sub_filter)
        if fade_filter:
            chain.append(fade_filter)

        chain.append("format=yuv420p")
        chain.append("setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
        vf = ",".join(chain)

        base_cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path), "-vf", vf]
        base_cmd += ["-af", _NARRATION_AUDIO_FILTER, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-shortest", "-t", f"{duration:.3f}"]

    encoder, encoder_args = pick_video_encoder(opts.quality_preset)
    cmd = [*base_cmd, "-c:v", encoder, *encoder_args, *_VIDEO_OUTPUT_ARGS, str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and encoder != "libx264":
        # NVENC bir render ortasında başarısız olabilir (sürücü hıçkırığı, eşzamanlı oturum
        # sınırı); tüm render'ı düşürmek yerine bu tek segmenti CPU'da yeniden dener. Sonraki
        # segmentler yine NVENC dener — _encoder_cache burada kalıcı olarak değiştirilmiyor,
        # tek seferlik bir arıza kalıcı bir GPU sorunuymuş gibi tüm render'ı CPU'ya zorlamasın.
        cmd = [*base_cmd, "-c:v", "libx264", *_X264_ARGS_BY_QUALITY[_quality(opts.quality_preset)],
               *_VIDEO_OUTPUT_ARGS, str(out_path)]
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
    cmd += ["-c", "copy", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


def concat_audio(audio_paths: list[Path], out_path: Path, list_file: Path):
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in audio_paths), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-af", _NARRATION_AUDIO_FILTER, "-c:a", "libmp3lame", "-q:a", "2", str(out_path)],
        check=True, capture_output=True,
    )
