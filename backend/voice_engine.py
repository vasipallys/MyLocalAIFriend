import asyncio
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

from backend.config import Settings

logger = logging.getLogger(__name__)


class VoiceEngine:
    """Lazy CPU speech engine. Heavy models never block the FastAPI event loop."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._whisper = None
        self._whisper_lock = Lock()
        self._tts_lock = asyncio.Lock()

    def _load_whisper(self):
        if self._whisper is not None:
            return self._whisper
        with self._whisper_lock:
            if self._whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise RuntimeError(
                        "Voice support is not installed. Run: pip install -e \".[voice]\""
                    ) from exc
                self._whisper = WhisperModel(
                    self.settings.whisper_model,
                    device="cpu",
                    compute_type=self.settings.whisper_compute_type,
                    cpu_threads=self.settings.cpu_threads or 0,
                )
        return self._whisper

    def _transcribe_sync(self, audio_path: Path) -> str:
        model = self._load_whisper()
        segments, _ = model.transcribe(
            str(audio_path), beam_size=1, vad_filter=True, condition_on_previous_text=False
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    async def transcribe(self, audio_path: Path) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_path)

    def _synthesize_sync(self, text: str, destination: Path) -> None:
        try:
            import pyttsx3
        except ImportError as exc:
            raise RuntimeError(
                "Local TTS is not installed. Run: pip install -e \".[voice]\""
            ) from exc
        engine = pyttsx3.init()
        engine.setProperty("rate", self.settings.tts_rate)
        engine.setProperty("volume", 1.0)
        engine.save_to_file(text, str(destination))
        engine.runAndWait()
        engine.stop()
        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError("The local TTS engine did not create an audio file")

    async def synthesize(self, text: str) -> str:
        name = f"speech-{uuid4()}.wav"
        destination = self.settings.generated_dir / name
        async with self._tts_lock:
            await asyncio.to_thread(self._synthesize_sync, text, destination)
        return f"/generated/{name}"

