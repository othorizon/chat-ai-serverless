import asyncio
import logging

import aiohttp
from config import AUDIO_BITS, OUTPUT_SAMPLE_RATE
from rtc.rtc_base import RemoteState
from tts.server.base_tts import BaseTts

logger = logging.getLogger(__name__)


class SelfFishAudioTts(BaseTts):
    def __init__(
        self, _exit: asyncio.Event, remote: RemoteState, audio_callback, end_callback
    ):
        super().__init__(_exit, remote, audio_callback, end_callback)
        self.queue = asyncio.Queue()

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
        logger.info("start SelfFishAudioTts")
        async for text in self.stream():
            if text is None:
                break
            if self._exit.is_set() or self.remote.is_playing():
                logger.info("SelfFishAudioTTs: receive break")
                return
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://xxx.com/v1/tts",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer YOUR_TOKEN",
                    },
                    json={
                        "text": text,
                        "reference_id": None,
                        "normalize": True,
                        "format": "wav",
                        # "opus_bitrate": 8000,
                        "chunk_length": OUTPUT_SAMPLE_RATE*AUDIO_BITS/8,
                        "top_p": 0.7,
                        "repetition_penalty": 1.2,
                        "temperature": 0.7,
                        "streaming": True,
                        "use_memory_cache": "never",
                        "seed": 1,
                    },
                ) as response:
                    if not response.ok:
                        error_text = await response.text()
                        logger.error("[SelfFishAudioTts] request fail: %s", error_text)
                        return
                    async for chunk in response.content.iter_any():
                        logger.info("SelfFishAudioTTs: receive audio chunk")
                        if self._exit.is_set() or self.remote.is_playing():
                            logger.info("SelfFishAudioTTs: receive break")
                            return
                        await self.audio_callback(chunk)

        await self.end_callback()
        logger.info("SelfFishAudioTTs: end")
