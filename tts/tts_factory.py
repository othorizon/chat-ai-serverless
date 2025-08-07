import asyncio
import logging
from typing import Callable

from config import TTS_ENGINE
from rtc.rtc_base import RemoteState
from tts.server.base_tts import BaseTts
from tts.server.fishaudio_util import FishAudioTts
from tts.server.self_fishaudio import SelfFishAudioTts
from tts.server.siliconflow_tts import SiliconFlowTts
from tts.server.volcengine import VolcEngineTts



logger = logging.getLogger(__name__)


class TtsFactory:
    """TTS 工厂类，负责根据配置创建不同的 TTS 实例"""
    
    @staticmethod
    def create_tts(
        _exit: asyncio.Event,
        remote_state: RemoteState,
        audio_callback: Callable[[bytes], None],
        end_callback: Callable[[], None],
    ) -> BaseTts:
        """
        根据 TTS_ENGINE 配置创建对应的 TTS 实例
        
        Args:
            _exit: 退出事件
            remote_state: 远程状态
            audio_callback: 音频回调函数
            end_callback: 结束回调函数
            
        Returns:
            BaseTts: TTS 实例
        """
        logger.info(f"Creating TTS instance for engine: {TTS_ENGINE}")
        if TTS_ENGINE == "volcengine":
            return VolcEngineTts(
                _exit,
                remote_state,
                audio_callback,
                end_callback,
            )
        elif TTS_ENGINE == "fishaudio":
            return FishAudioTts(
                _exit,
                remote_state,
                audio_callback,
                end_callback,
            )
        elif TTS_ENGINE == "SiliconFlowTts":
            return SiliconFlowTts(
                _exit, 
                remote_state, 
                audio_callback, 
                end_callback
            )
        elif TTS_ENGINE == "SelfFishAudioTts":
            return SelfFishAudioTts(
                _exit, 
                remote_state, 
                audio_callback, 
                end_callback
            )
        else:
            raise ValueError(f"Unsupported TTS engine: {TTS_ENGINE}")