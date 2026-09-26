import subprocess
from pathlib import Path

from app.config import MODELS_DIR
from app.models import SynthResult
from app.tts.base import TTSProvider, local_engine_unavailable

SPEAKERS_DIR = MODELS_DIR / "coqui_speakers"

# XTTS v2'nin hazır konuşmacıları belirli bir dile bağlı değildir. Aşağıdaki
# kısa liste, aynı Türkçe teknik cümleyle üretilip erkek ses adayı olarak
# dinlemeye sunulan konuşmacılardır. Damien Black; düşük temel frekansı,
# anlaşılır seviyesi ve ders anlatımına uygun daha tok tınısı nedeniyle ilk
# öneri olarak gösterilir. Bu yalnızca arayüz sırasıdır; kullanıcı diğer
# adayları veya kendi klonlanmış WAV dosyasını seçmeye devam edebilir.
XTTS_RECOMMENDED_MALE_SPEAKER = "Damien Black"
XTTS_RECOMMENDED_FEMALE_SPEAKER = "Claribel Dervla"
XTTS_CURATED_MALE_SPEAKERS = (
    "Damien Black",
    "Luis Moray",
    "Baldur Sanjin",
    "Ilkin Urbano",
    "Kumar Dahl",
    "Ludvig Milivoj",
    "Royston Min",
    "Torcull Diarmuid",
    "Viktor Eka",
    "Craig Gutsy",
    "Marcos Rudaski",
    "Andrew Chipper",
)
XTTS_CURATED_FEMALE_SPEAKERS = (
    "Claribel Dervla",
    "Ana Florence",
    "Tanja Adelina",
    "Tammy Grit",
    "Sofia Hellen",
)


def list_xtts_builtin_voices(available: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """Modeli yüklemeden gösterilebilen, denenmiş erkek XTTS seslerini döndür.

    ``available`` verilirse yalnız gerçekten yüklü checkpoint'te bulunan
    konuşmacılar listelenir. Bu sayede statik web listesi hızlı kalırken model
    yüklendiğindeki sağlayıcı listesi de checkpoint ile tutarlı olur.
    """
    allowed = set(available) if available is not None else None
    voices = []
    ordered = (
        XTTS_RECOMMENDED_MALE_SPEAKER,
        XTTS_RECOMMENDED_FEMALE_SPEAKER,
        *(name for name in XTTS_CURATED_MALE_SPEAKERS if name != XTTS_RECOMMENDED_MALE_SPEAKER),
        *(name for name in XTTS_CURATED_FEMALE_SPEAKERS if name != XTTS_RECOMMENDED_FEMALE_SPEAKER),
    )
    for name in ordered:
        if allowed is not None and name not in allowed:
            continue
        if name == XTTS_RECOMMENDED_MALE_SPEAKER:
            suffix = "önerilen · tok erkek · ders anlatımı"
        elif name == XTTS_RECOMMENDED_FEMALE_SPEAKER:
            suffix = "önerilen · sıcak kadın · ders anlatımı"
        elif name in XTTS_CURATED_FEMALE_SPEAKERS:
            suffix = "kadın ders sesi"
        else:
            suffix = "erkek aday"
        voices.append({"id": f"builtin:{name}", "label": f"{name} (XTTS {suffix})"})

    # Eski ayarlar ve API istemcileri için geriye dönük varsayılanı koruyoruz;
    # ancak yeni seçimlerde önerilen erkek ses ilk sırada olduğundan arayüz onu
    # otomatik seçer.
    voices.insert(2 if len(voices) >= 2 else len(voices), {
        "id": "builtin:default",
        "label": "XTTS varsayılan konuşmacı (geriye dönük uyumluluk)",
    })
    return voices


def _patch_xtts_audio_loading() -> None:
    """XTTS'in klonlanmış-ses referans dosyasını okumak için kullandığı
    TTS.tts.models.xtts.load_audio, içeride torchaudio.load()'u çağırıyor.
    Bu ortamdaki torchaudio (2.11+) artık HER ZAMAN TorchCodec (FFmpeg tabanlı)
    decoder'ı kullanıyor — `backend=` parametresi bile görmezden geliniyor —
    ve TorchCodec'in derlenmiş DLL'leri (libtorchcodec_core4-9.dll) bu venv'de
    denenen hiçbir FFmpeg sürümüyle yüklenemiyor. Sonuç: klonlanmış bir ses her
    kullanılmaya çalışıldığında OSError ile patlıyor — builtin:default hiç
    etkilenmiyor çünkü o hiç dosya decode etmiyor. soundfile (zaten TTS'in
    kendi bağımlılığı) aynı dosyayı TorchCodec'e hiç uğramadan okuyabiliyor,
    bu yüzden load_audio'yu import anında bununla değiştiriyoruz.
    """
    import torch
    import torchaudio
    from TTS.tts.models import xtts as xtts_module

    if getattr(xtts_module.load_audio, "_ders_video_patched", False):
        return

    def load_audio(audiopath, sampling_rate):
        import soundfile as sf

        data, sr = sf.read(str(audiopath), dtype="float32", always_2d=True)
        audio = torch.from_numpy(data.T)  # (kanal, örnek)

        if audio.size(0) != 1:
            audio = torch.mean(audio, dim=0, keepdim=True)
        if sr != sampling_rate:
            audio = torchaudio.functional.resample(audio, sr, sampling_rate)

        audio.clip_(-1, 1)
        return audio

    load_audio._ders_video_patched = True
    xtts_module.load_audio = load_audio


class CoquiTTSProvider(TTSProvider):
    """Coqui XTTS v2: offline, çok dilli, ses klonlama destekli ama ağır (torch + ~2GB model).
    Kurulu değilse açık bir hata verir; kurulum için gui/install_coqui.py kullan.

    Performans notu (RTX 4060 üzerinde ölçüldü): tekil çağrıda VRAM'in tamamının
    dolmaması ve GPU kullanımının dalgalı görünmesi BEKLENEN bir durumdur — XTTS
    v2'nin GPT tabanlı kod çözücüsü otomatik-regresif çalışır (token token, küçük
    ardışık işlemler). `torch.backends.cudnn.benchmark`, TF32 ve `autocast(fp16)`
    bu depoda gerçek bir seste ölçülüp denendi: cudnn.benchmark ve autocast(fp16)
    süreyi ~5 kat KÖTÜLEŞTİRDİ, TF32 ölçülebilir bir fark yaratmadı — hiçbiri
    kullanılmıyor.

    Aynı GPU çağrısına birden fazla slaytı paketlemek (tensor-batching) de
    denendi ve KALDIRILDI: kısa sentetik cümlelerde hızlanma ölçülse de gerçek
    (uzun) slayt anlatımlarında GPU zaten sürekli meşgul kaldığından ölçülebilir
    bir fayda sağlamadı (RTF sıralıyla aynı, ~0.87-0.89). Gerçek kazanç
    BAĞIMSIZ PROCESS'lerde aynı anda birden fazla model çalıştırmaktan geliyor
    — bkz. app.tts.coqui_parallel.synthesize_parallel. Bu sağlayıcı kendisi
    her zaman sıralı/tekil üretim yapar; paralellik bir üst katmanın işidir.
    """
    name = "coqui"

    def __init__(self, gpu: bool | None = None):
        try:
            from TTS.api import TTS
        except ImportError as e:
            raise RuntimeError(local_engine_unavailable(
                "XTTS v2",
                "Coqui TTS kurulu değil. GUI'deki 'Coqui XTTS Kur' butonunu kullan "
                "veya: venv\\Scripts\\pip install TTS torch --index-url https://download.pytorch.org/whl/cpu",
                remote_ok=True,
            )) from e

        _patch_xtts_audio_loading()

        if gpu is None:
            try:
                import torch
                gpu = torch.cuda.is_available()
            except ImportError:
                gpu = False

        self.gpu = gpu
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=gpu)
        # Klonlanmış bir ses (speaker_wav) kullanıldığında, coqui-tts'in yüksek
        # seviyeli tts_to_file() yolu HER çağrıda referans wav'ı yeniden kodlayıp
        # konuşmacı koşullandırmasını (conditioning latents) baştan hesaplıyor
        # (bkz. TTS.utils.voices.CloningMixin.clone_voice). Bir derste onlarca
        # slayt aynı klonlanmış sesi kullandığından bu, slayt başına gereksiz bir
        # GPU/CPU encoder geçişi demek. Koşullandırmayı ses başına BİR KEZ
        # hesaplayıp bu sağlayıcı örneğinin (bir render işi boyunca yaşar) ömrü
        # süresince bellekte tutuyor, konuşmacı tablosuna elle ekliyoruz.
        self._cloned_speaker_ids: dict[str, str] = {}

    def list_voices(self) -> list[dict]:
        voices = list_xtts_builtin_voices(self.tts.speakers or [])
        if SPEAKERS_DIR.exists():
            for wav in sorted(SPEAKERS_DIR.glob("*.wav")):
                voices.append({"id": str(wav), "label": f"Klonlanmış: {wav.stem}"})
        return voices

    def _resolve_speaker_id(self, voice: str) -> str:
        """Bu ses için model.speaker_manager.speakers içinde bir giriş bulunmasını
        sağlar (yerleşikse doğrudan, klonlanmışsa önbellekten ya da bir kez
        hesaplayıp) ve o girişin anahtarını döndürür."""
        if not voice or voice == "builtin:default":
            default = self.tts.speakers[0] if getattr(self.tts, "speakers", None) else None
            if default is None:
                raise RuntimeError("Kullanılabilir konuşmacı yok.")
            return default

        if voice.startswith("builtin:"):
            name = voice[len("builtin:"):]
            if name not in (self.tts.speakers or []):
                raise RuntimeError(f"'{name}' adında dahili bir XTTS konuşmacısı yok.")
            return name

        speaker_id = self._cloned_speaker_ids.get(voice)
        if speaker_id is None:
            model = self.tts.synthesizer.tts_model
            gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(audio_path=voice)
            speaker_id = f"cloned::{voice}"
            model.speaker_manager.speakers[speaker_id] = {
                "gpt_conditioning_latents": gpt_cond_latent,
                "speaker_embedding": speaker_embedding,
            }
            self._cloned_speaker_ids[voice] = speaker_id
        return speaker_id

    def synthesize(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> SynthResult:
        kwargs = dict(text=text, language="tr", file_path=str(out_path), speaker=self._resolve_speaker_id(voice))
        self.tts.tts_to_file(**kwargs)

        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(result.stdout.strip())
        return SynthResult(duration=duration, words=None)
