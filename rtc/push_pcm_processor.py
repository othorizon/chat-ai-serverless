import logging
import threading
import numpy as np

from agora.rtc.audio_pcm_data_sender import AudioPcmDataSender
from agora.rtc.utils.audio_consumer import AudioConsumer
from config import AUDIO_BITS, OUTPUT_SAMPLE_RATE

logger = logging.getLogger(__name__)

AUDIO_BYTES = int(AUDIO_BITS / 8)


class PushPcmProcessor:
    def __init__(self, pcm_data_sender: AudioPcmDataSender):
        self.sample_rate = OUTPUT_SAMPLE_RATE
        self.num_of_channels = 1
        self.audio_consumer = AudioConsumer(
            pcm_data_sender, self.sample_rate, self.num_of_channels
        )
        self.send_interval = 0.06  # 使用60ms的发送间隔
        self.queue = []
        self._timer = None
        self._released = False
        self.last_consume_ret = 0

    def push_pcm_data_from_file(self, audio_file_path):
        with open(audio_file_path, "rb") as audio_file:
            logger.info(f"push_pcm_data_from_file: {audio_file_path}")
            send_size = int(
                self.sample_rate * self.num_of_channels * self.send_interval * AUDIO_BYTES
            )
            frame_buf = bytearray(send_size)

            while True:
                success = audio_file.readinto(frame_buf)
                if not success:
                    # 不再重置文件指针，只返回False表示文件已经读完
                    return False
                self.push_pcm_data(frame_buf)

    def push_pcm_data(self, data: bytes):
        # logger.info("push_pcm_data: %s", len(data))
        if AUDIO_BITS == 16:
            self.audio_consumer.push_pcm_data(data)
        elif AUDIO_BITS == 32:
            int16_data = self.convert_flaot32_to_int16(data)
            self.audio_consumer.push_pcm_data(int16_data)
        else:
            raise ValueError(f"不支持的音频位数: {AUDIO_BITS}")


    def convert_flaot32_to_int16(self, data: bytes):
        """
        将 float32 数据转换为 16 位数据
        Args:
            data: bytes, float32 数据
        Returns:
            bytes, 16 位数据
        """
        
        # 将字节数据转换为 float32 numpy 数组
        float32_data = np.frombuffer(data, dtype=np.float32)
        
        # 将 float32 数据（范围 [-1.0, 1.0]）转换为 int16 数据（范围 [-32768, 32767]）
        # 首先限制数据范围到 [-1.0, 1.0]
        float32_data_clipped = np.clip(float32_data, -1.0, 1.0)
        
        # 转换为 int16
        int16_data = (float32_data_clipped * 32767).astype(np.int16)
        
        # 转换回字节数组
        int16_bytes = int16_data.tobytes()
        
        # 推送转换后的 16 位数据
        return int16_bytes
            
            

    def is_push_to_rtc_completed(self):
        return (
            self.last_consume_ret < 1 or self.audio_consumer.is_push_to_rtc_completed()
        )

    def start_consume_task(self):
        if self._released:
            return
        try:
            self.last_consume_ret = self.audio_consumer.consume()
        except Exception as e:
            logger.error("Error consuming audio data: %s", e, exc_info=True)
        finally:
            if not self._released:
                self._timer = threading.Timer(
                    self.send_interval, self.start_consume_task
                )
                self._timer.start()

    def start_thread(self):
        self.start_consume_task()

    def clear(self):
        self.audio_consumer.clear()

    def release(self):
        self._released = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.audio_consumer.release()
