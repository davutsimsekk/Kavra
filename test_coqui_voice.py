"""Coqui XTTS v2'nin kurulu ve çalışır durumda olduğunu doğrular.
Model D:\\...\\_cache\\tts_home altında hazır (indirme gerekmez); sadece belleğe
yüklenir. XTTS v2 yüklenirken ~3-5GB boş RAM ister. Diğer ağır uygulamaları
(tarayıcı, IDE'ler) kapatıp tekrar dene: venv\\Scripts\\python.exe test_coqui_voice.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.tts.coqui_provider import CoquiTTSProvider

provider = CoquiTTSProvider()
result = provider.synthesize(
    "Merhaba, bu Coqui XTTS ile üretilmiş tamamen offline çalışan bir Türkçe ses testidir.",
    "builtin:default",
    Path(__file__).parent / "_cache" / "coqui_test.wav",
)
print("OK, süre:", result.duration)
