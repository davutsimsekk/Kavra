import json
import shlex
import subprocess
import uuid
from pathlib import Path

from app.llm.base import NarrationGenerator, extract_json_array
from app.llm.prompt_template import build_prompt
from app.models import RawSection, Slide

DEFAULT_COMMAND = "claude -p --output-format json --restricted"


class AgentCliNarrationGenerator(NarrationGenerator):
    """API kotası/anahtarı olmadan, elde hazır bir CLI ajanını (Claude Code, Gemini CLI,
    vb.) alt süreç olarak çağırıp ders script'i ürettirir. Prompt stdin'den verilir,
    ajan -p/print (non-interactive) modda çalışıp bir JSON zarfı döndürür; zarftaki
    "result" alanı bizim beklediğimiz JSON dizisidir. --restricted, ajanın dosya/komut
    araçlarına hiç ihtiyaç duymayan bu saf metin-dönüştürme görevinde onları devre dışı
    bırakır (daha güvenli ve biraz daha ucuz)."""

    def __init__(self, command: str = DEFAULT_COMMAND, timeout: int = 900,
                 reuse_session: bool = True, session_id: str | None = None):
        self.command = command
        self.timeout = timeout
        self.reuse_session = reuse_session
        self.total_cost_usd = 0.0
        self.session_id: str | None = session_id if reuse_session else None

    def _manages_claude_session(self) -> bool:
        """Whether this command supports our automatically managed session flags."""
        cmd = shlex.split(self.command)
        if not cmd or Path(cmd[0]).stem.lower() != "claude":
            return False
        has_manual_session = any(
            token in {"--resume", "-r", "--session-id"} for token in cmd
        )
        return self.reuse_session and not has_manual_session

    def _command_for_next_call(self) -> list[str]:
        cmd = shlex.split(self.command)
        if not self._manages_claude_session():
            return cmd
        if self.session_id:
            return [*cmd, "--resume", self.session_id]
        self.session_id = str(uuid.uuid4())
        return [*cmd, "--session-id", self.session_id]

    def _call(self, prompt: str) -> str:
        cmd = self._command_for_next_call()
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Komut bulunamadı: '{self.command}'. Agent CLI'nin (ör. Claude Code) "
                f"PATH'te olduğundan ve bu makinede kurulu olduğundan emin ol."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Agent CLI {self.timeout} saniye içinde yanıt vermedi. "
                "Tek istekte çok bölüm gönderiyorsan bu seçeneği kapatıp bölüm başına "
                "parçalama kullanmayı dene."
            ) from e

        if proc.returncode != 0:
            raise RuntimeError(f"Agent CLI hata verdi (exit {proc.returncode}):\n{proc.stderr[:1000]}")

        out = proc.stdout.strip()
        try:
            envelope = json.loads(out)
            if isinstance(envelope, dict) and "result" in envelope:
                if envelope.get("is_error"):
                    raise RuntimeError(f"Agent hata döndürdü: {envelope.get('result')}")
                self.total_cost_usd += float(envelope.get("total_cost_usd") or 0.0)
                if envelope.get("session_id"):
                    self.session_id = str(envelope["session_id"])
                out = envelope["result"]
        except json.JSONDecodeError:
            pass
        return out

    def generate(self, sections: list[RawSection], style_note: str = "") -> list[Slide]:
        prompt = build_prompt(sections, style_note)
        if self._manages_claude_session() and self.session_id:
            prompt = (
                "Bu, aynı ders scriptinin bir sonraki içerik parçasıdır. Önceki yanıttaki "
                "terminolojiyi ve anlatım akışını koru; önceki slaytları tekrarlama. "
                "Yalnızca aşağıdaki yeni içerik için JSON dizisi üret.\n\n" + prompt
            )
        out = self._call(prompt)
        try:
            data = extract_json_array(out)
        except (ValueError, TypeError):
            # Model bazen dizinin ortasında geçersiz bir kaçış/virgül bırakabiliyor
            # (ör. kod örneğindeki tırnak/yeni satır). Gemini/OpenAI sağlayıcılarındaki
            # gibi, bozuk çıktıyı modelin kendisine gösterip düzelttiriyoruz.
            fix_prompt = (
                "Aşağıdaki metni SADECE geçerli bir JSON dizisine çevir, "
                "başka hiçbir şey yazma:\n\n" + out
            )
            out = self._call(fix_prompt)
            data = extract_json_array(out)
        return [Slide.from_dict(d) for d in data]
