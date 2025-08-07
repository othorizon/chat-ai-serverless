# 使用图片base64
import asyncio
import logging
import os
import signal
import uuid
import json
from concurrent.futures import Future
from typing import List
import aiohttp
from paho.mqtt.client import MQTTMessage
from agora.rtc.audio_frame_observer import AudioFrame
from config import REMOTE_AUDIO_SAMPLE_RATE, VOLC_AUDIO_PARAMS, GLOBAL_CONFIG, LLM_SERVER_URL
from mqtt.mqtt import MQTTClient

from rtc.push_pcm_processor import PushPcmProcessor
from rtc.audio_file import AudioFile
from rtc.rtc_base import RemoteState, RTCBaseProcess

# from asr.volcengin_rtasr_short import init_short_rtasr
from asr.xunfei_rtasr_short import init_short_rtasr

from tts.tts_processor import TtsProcesser


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RTCProcessIMPL(RTCBaseProcess):
    def __init__(self, device_id: str, pet_persona: str):
        self.user_id = None
        self.future: Future = None
        self.device_id = device_id
        self.pet_persona = pet_persona
        self.is_user_joined = False
        self.tasks: List = []

        # 记录上一次使用的PCM提示语
        self.last_prompt_key = None

        # rtasr 的采样率为 16K
        super().__init__(
            device_id=device_id,
            remote_sample_rate=REMOTE_AUDIO_SAMPLE_RATE,
            # 总是初始化 vad 配置
            enable_vad=True,
        )

        self.push_processor = PushPcmProcessor(self.pcm_data_sender)
        self.rtasr = init_short_rtasr(
            self.clear_run_llm,
            self.clear_push_pcm,
            # 关闭按键对话模式则需要启用 vad
            enable_vad=GLOBAL_CONFIG.get("IS_MIC_PRESS_MODE") is False,
            _exit=self._exit,
        )
        self.mqtt_client = MQTTClient(self.device_id, self.mqtt_on_message)
        # 主事件循环将在run方法中设置
        self._main_loop = None
        self.running_task = None
        # 主动退出时是否需要和用户告别（如果是用户主动退出就不需要）
        self.need_say_byebye = True
        self.last_user_audio_state = 0
        self.current_llm_req_id = None
    
    def stop_running_llm(self):
        if self.running_task:
            self.running_task.cancel()
            self.running_task = None
        self.current_llm_req_id = None

    def start_new_llm_req(self):
        self.current_llm_req_id = str(uuid.uuid4())
        return self.current_llm_req_id
    
    def is_current_llm_req_id(self, req_id):
        return self.current_llm_req_id == req_id

    def get_room_id(self):
        return "Room-" + self.device_id

    def clear_push_pcm(self):
        self.push_processor.clear()

    def on_user_audio_track_state_changed(self, user_id, state, reason):
        logger.info(
            f"on_user_audio_track_state_changed, user_id={user_id},  state={state},reason={reason}"
        )
        # 这里特别要注意，用户第一次进入时会直接发送一次状态 0 的事件
        if state == 0:
            if self.last_user_audio_state != 0:
                logger.info("user stop to talk")
                self.rtasr.put_aduio_frame_end()
        else:
            if self.current_llm_req_id is not None or self.running_task is not None:
                #停止运行中的llm
                self.stop_running_llm()
                # 停止tts
                self.push_processor.clear()

        # 记录上一次用户音频状态
        self.last_user_audio_state = state

    def on_playback_audio_frame_before_mixing(
        self,
        agora_local_user,
        channel_id,
        uid,
        frame: AudioFrame,
        vad_result_state: int,
        vad_result_bytearray: bytearray,
    ):
        self.rtasr.put_audio_frame(frame, vad_result_state)
        # logger.debug("on_playback_audio_frame_before_mixing, file_path=%s, len=%d",file_path,len(frame.buffer),)

        if self.enable_vad and vad_result_state > 0 and self.running_task:
            logger.info("vad_result_state > 0, cancel running_task")
            self.running_task.cancel()
        return 1

    def on_user_join(self, user_id: str):
        self.user_id = user_id
        self.is_user_joined = True

        # 线程安全地清除 _exit 事件
        if self._main_loop and not self._main_loop.is_closed():
            self._main_loop.call_soon_threadsafe(self._exit.clear)
            logger.info("user is join - _exit.clear() scheduled in main loop")
        else:
            # 如果主循环不可用，直接清除（兜底方案）
            self._exit.clear()
            logger.info("user is join - _exit.clear() called directly")

        # 重置asr数据 也重置了静默计时
        self.rtasr.reset_data()

    def on_user_left(self):
        self.user_id = None
        self.is_user_joined = False
        self.remote = RemoteState()

        # 线程安全地设置 _exit 事件
        if self._main_loop and not self._main_loop.is_closed():
            self._main_loop.call_soon_threadsafe(self._exit.set)
            logger.info("user is left - _exit.set() scheduled in main loop")
        else:
            # 如果主循环不可用，直接设置（兜底方案）
            self._exit.set()
            logger.info("user is left - _exit.set() called directly")


    def process_sys_event(self, event: str):
        if event == "exit_chat":
            logger.info("exit_chat")
            self.rtasr.stop_rtasr()
            self.need_say_byebye = False

    # 清除当前的运行中的任务，并重新运行
    async def clear_run_llm(self, user_say: str):
        # 如果有一个运行中的任务，则取消掉
        if self.running_task:
            self.running_task.cancel()
        await self.run_llm(user_say)

    async def run_llm(self, user_say: str = None):
        logger.info(f"run_llm: {user_say}")
        # 清空推送队列
        self.push_processor.clear()

        # 开启一个新的请求，用于让之前请求的 tts 失效
        current_req_id = self.start_new_llm_req()
        # 启动音频采集
        # self.audio_track.set_enabled(1)

        # 只有当user_say不为空时，才执行select_random_prompt逻辑
        # if user_say:
        #     # 随机选择一个PCM提示语
        #     random_prompt_key, pcm_file_path = self.select_random_prompt()
        #     logger.info(f"选择了PCM提示: {random_prompt_key}, 文件: {pcm_file_path}")
        #     self.push_processor.push_pcm_data_from_file(pcm_file_path)
        # else:
        #     # 如果user_say为空，跳过PCM提示逻辑
        #     random_prompt_key = None
        #     logger.info("user_say为空，跳过PCM提示逻辑")

        audio_file = AudioFile(str(uuid.uuid4()))

        # region 初始化TTS
        def audio_callback(data: bytes):
            if self._exit.is_set() or not self.is_current_llm_req_id(current_req_id):
                logger.info("exit or playing,break audio_callback")
                return
            self.push_processor.push_pcm_data(data)
            audio_file.write(data)
            # logger.info("TTS data receive")

        def end_callback():
            if self._exit.is_set() or not self.is_current_llm_req_id(current_req_id):
                logger.info("exit or playing,break end_callback")
                return
            audio_file.close()
            logger.info("TTS session ended")

        tts = TtsProcesser(self._exit, self.remote, audio_callback, end_callback)

        # endregion

        async def run_llm_task():
            try:
                if not user_say:
                    tts.send_to_tts("我没有听到你说什么")
                    tts.put_end()
                    return
                data = {
                    "device_id": self.device_id,
                    "user_say": user_say,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        LLM_SERVER_URL,
                        json=data,
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise Exception("API request failed: %s", error_text)

                        async for line in response.content.iter_any():
                            if self._exit.is_set() or not self.is_current_llm_req_id(
                                current_req_id
                            ):
                                logger.info("exit or playing, break streaming")
                                break
                            txt_chunk = line.decode("utf-8")
                            tts.send_to_tts(txt_chunk)

                # 结束TTS
                tts.put_end()

            except Exception as e:
                logger.error("OpenAI API task error: %s", e, exc_info=True)
                raise

        tts_task = asyncio.create_task(tts.start())
        llm_task = asyncio.create_task(run_llm_task())

        await asyncio.gather(llm_task, tts_task)
        # 清空当前音频队列，避免一些没来得及被静音的内容
        if hasattr(self.rtasr, "clear_audio_queue"):
            self.rtasr.clear_audio_queue()
        else:
            logger.warning("rtasr does not have method clear_audio_queue()")

        # 等待推送完成
        while not self.push_processor.is_push_to_rtc_completed():
            await asyncio.sleep(0.1)

        # 延迟 1 秒，避免客户端最后一个返回的音接收有延迟，影响服务端asr 清空结果的准确性，临时方案
        # await asyncio.sleep(1)

        logger.info("process done")

    def mqtt_on_message(self, client, userdata, msg: MQTTMessage):
        logger.info(
            "on mq message: topic: %s, payload: %s", msg.topic, msg.payload.decode()
        )
        try:
            data = json.loads(msg.payload.decode())
            if data.get("type") == "run_llm":
                # 清除当前的 tts
                self.push_processor.clear()
                # 重置asr数据 也重置了静默计时
                self.rtasr.reset_data()
                # 异步调用run_llm
                text = data.get("user_say")
                if text:
                    if self._main_loop and not self._main_loop.is_closed():
                        if self.running_task:
                            self.running_task.cancel()
                        self.running_task = asyncio.run_coroutine_threadsafe(
                            self.run_llm(text), self._main_loop
                        )
                    else:
                        logger.warning("Main loop not available for TTS task")
            if data.get("type") == "run_tts":
                # 清除当前的 tts
                self.push_processor.clear()
                # 重置asr数据 也重置了静默计时
                self.rtasr.reset_data()
                # 异步调用push_text_to_tts
                text = data.get("text")
                if text:
                    if self._main_loop and not self._main_loop.is_closed():
                        if self.running_task:
                            self.running_task.cancel()
                        self.running_task = asyncio.run_coroutine_threadsafe(
                            self.push_text_to_tts(text), self._main_loop
                        )
                    else:
                        logger.warning("Main loop not available for TTS task")
            if data.get("type") == "update_global_config":
                global_config = data.get("globalConfig", {})
                GLOBAL_CONFIG.update(global_config)
                logger.info(f"UPDATE GLOBAL_CONFIG: {GLOBAL_CONFIG}")
        except Exception as e:
            logging.error("on mq message error: %s", e)

    async def push_text_to_tts(self, text: str):
        logger.info("push_text_to_tts: %s", text)

        # region 初始化TTS
        async def audio_callback(data: bytes):
            self.push_processor.push_pcm_data(data)
            # logger.info("TTS data receive")

        async def end_callback():
            logger.info("TTS session ended")

        tts = TtsProcesser(self._exit, self.remote, audio_callback, end_callback)
        tts.send_to_tts(text)
        tts.put_end()
        await tts.start()

    async def byebye(self):
        await self.run_llm("(sys_byebye)")

    def run(self):
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._main_loop = loop

        try:
            # 启动推送流
            self.push_processor.start_thread()
            # 启动 mqtt
            self.mqtt_client.run()
            # 启动 asr
            loop.run_until_complete(self.rtasr.start_rtasr())
            if self.need_say_byebye:
                # 执行退出语
                loop.run_until_complete(self.byebye())
        except Exception as e:
            logger.error("run_main error: %s", e, exc_info=True)
        finally:
            self.release()
            loop.close()

        logger.info("all task is done")

    def release(self):
        self.push_processor.release()
        super().release()


def force_exit(_signum, _frame):
    """2
    强制退出程序
    """
    logger.info("start exit")

    os._exit(0)  # 使用os._exit()直接终止程序


# https://help.aliyun.com/zh/functioncompute/fc-3-0/user-guide/event-handlers-1-1?spm=a2c4g.11186623.help-menu-2508973.d_2_2_4_0.42532960VJw4Cz
def handler(event, context):
    logger.info("start event: %s", event)
    evt = json.loads(event)
    device_id = evt.get("deviceId")
    pet_persona = evt.get("info", {}).get("petPersona")
    speech_rate = evt.get("info", {}).get("speechRate", 0)
    global_config = evt.get("config", {}).get("globalConfig", {})
    GLOBAL_CONFIG.update(global_config)
    logger.info(f"INIT GLOBAL_CONFIG: {GLOBAL_CONFIG}")

    if speech_rate:
        VOLC_AUDIO_PARAMS["speech_rate"] = int(speech_rate)
    if not device_id or not pet_persona:
        logger.error("deviceId or petPersona is required")
        return
    rtc = RTCProcessIMPL(device_id=device_id, pet_persona=pet_persona)
    rtc.run()


if __name__ == "__main__":
    # 注册信号处理
    signal.signal(signal.SIGINT, force_exit)  # Ctrl+C
    signal.signal(signal.SIGTERM, force_exit)  # kill
    handler(
        '{"deviceId":"xx","info":{},"config":{}}',
        None,
    )
