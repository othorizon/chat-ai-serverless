import asyncio
import logging

import aiohttp
from config import SILICONFLOW_API_KEY, SILICONFLOW_MODEL_ID, SILICONFLOW_VOICE
from rtc.rtc_base import RemoteState
from tts.server.base_tts import BaseTts

logger = logging.getLogger(__name__)


class SiliconFlowTts(BaseTts):
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
        logger.info("start SiliconFlowTts")
        async for text in self.stream():
            if text is None:
                break
            if self._exit.is_set() or self.remote.is_playing():
                logger.info("SelfFishAudioTTs: receive break")
                return
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.siliconflow.cn/v1/audio/speech",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                    },
                    json={
                        "input": text,
                        "model": SILICONFLOW_MODEL_ID,
                        "voice": SILICONFLOW_VOICE,
                        "response_format": "pcm",
                        # "stream": True,
                    },
                ) as response:
                    if not response.ok:
                        error_text = await response.text()
                        logger.error("[SiliconFlowTts] request fail: %s", error_text)
                        return
                    async for chunk in response.content.iter_any():
                        # logger.info("SiliconFlowTts: receive audio chunk")
                        if self._exit.is_set() or self.remote.is_playing():
                            logger.info("SiliconFlowTts: receive break")
                            return
                        await self.audio_callback(chunk)

        await self.end_callback()
        logger.info("SiliconFlowTts: end")
