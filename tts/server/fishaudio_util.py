import asyncio
import logging


from config import FISH_AUDIO_MODEL_ID
from rtc.rtc_base import RemoteState
from tts.server.base_tts import BaseTts

logger = logging.getLogger(__name__)


class FishAudioTts(BaseTts):
    def __init__(
        self,
        _exit: asyncio.Event,
        remote: RemoteState,
        audio_callback,
        end_callback,
    ):
        super().__init__(_exit, remote, audio_callback, end_callback)
        from fish_audio_sdk import AsyncWebSocketSession
        self.async_websocket = AsyncWebSocketSession(
            apikey="xxx",
            base_url="https://direct.api.fish-audio.cn",
        )
        self.queue = asyncio.Queue()
        self.model_id = FISH_AUDIO_MODEL_ID

    def put_data(self, data: str):
        self.queue.put_nowait(data)

    def put_end(self):
        self.queue.put_nowait(None)

    async def stream(self):
        while True:
            line = await self.queue.get()
            if line is None:
                break
            yield line

    async def start(self):
        logger.info("start FishAudioTTS")
        from fish_audio_sdk import TTSRequest
        async for chunk in self.async_websocket.tts(
            TTSRequest(
                latency="balanced",
                text="",
                format="pcm",
                reference_id=self.model_id,
            ),
            self.stream(),
        ):
            logger.info("FishAudioTTs: receive audio chunk")
            if self._exit.is_set() or self.remote.is_playing():
                logger.info("FishAudioTTs: receive break")
                return
            await self.audio_callback(chunk)

        await self.end_callback()
        logger.info("FishAudioTTs: end")
