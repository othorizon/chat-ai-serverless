import asyncio
import logging
from typing import Callable

from rtc.rtc_base import RemoteState
from tts.tts_factory import TtsFactory

logger = logging.getLogger(__name__)

class TtsProcesser:

    def __init__(
        self,
        _exit: asyncio.Event,
        remote_state: RemoteState,
        audio_callback: Callable[[bytes], None],
        end_callback: Callable[[], None],
    ):
        # 使用工厂类创建 TTS 实例
        self.tts = TtsFactory.create_tts(
            _exit,
            remote_state,
            audio_callback,
            end_callback,
        )

    def send_to_tts(self, txt: str):
        logger.info("TTS message sent: %s", txt)
        if txt:
            self.tts.put_data(txt)

    def put_end(self):
        self.tts.put_end()

    async def start(self):
        await self.tts.start()
