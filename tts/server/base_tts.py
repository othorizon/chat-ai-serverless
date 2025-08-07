import abc
import asyncio
import logging
from typing import Callable


from rtc.rtc_base import RemoteState

logger = logging.getLogger(__name__)



class BaseTts(abc.ABC):
    def __init__(
        self,
        _exit: asyncio.Event,
        remote: RemoteState,
        audio_callback: Callable[[bytes], None],
        end_callback: Callable[[], None],
        event_callback: Callable[[dict], None]=None,
    ):
        self._exit = _exit
        self.remote = remote
        self.audio_callback = audio_callback
        self.event_callback = event_callback
        self.end_callback = end_callback

    @abc.abstractmethod
    def put_end(self):
        pass

    @abc.abstractmethod
    def put_data(self, data: str):
        pass

    @abc.abstractmethod
    async def start(self):
        pass
