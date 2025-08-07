import asyncio
import base64
import hashlib
import hmac
import json
import string
from asyncio import Queue
from datetime import datetime
import logging
import time
from time import mktime
from typing import Dict, List, Optional, Callable, Awaitable
from urllib.parse import quote

import websockets
import websockets.protocol
from agora.rtc.audio_frame_observer import AudioFrame

from config import GLOBAL_CONFIG, XUNFEI_APP_ID, XUNFEI_API_KEY


logger = logging.getLogger(__name__)

# https://www.xfyun.cn/doc/asr/rtasr/API.html#%E6%8E%A5%E5%8F%A3%E8%B0%83%E7%94%A8%E6%B5%81%E7%A8%8B

# 如果时间过短的话，注意当执行函数调用时，等待时间会比较长，要注意特殊处理才行
TIME_SINCE_LAST_RECOGNITION = 5 * 60  # n分钟静后终止服务

# 定义中英文标点符号（全局变量）
ENGLISH_PUNCTUATION = string.punctuation  # !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
CHINESE_PUNCTUATION = (
    "。，！？；：" "''（）【】｛｝—·…、《》〈〉「」『』〔〕〖〗〘〙〚〛"
)
ALL_PUNCTUATION = ENGLISH_PUNCTUATION + CHINESE_PUNCTUATION


class RtAsProcessorShort:

    def __init__(
        self,
        app_id: str,
        api_key: str,
        clear_run_llm: Callable[[str], Awaitable[None]],
        clear_push_pcm: Callable[[], None],
        enable_vad: bool,
        _exit: asyncio.Event,
    ):
        self.app_id = app_id
        self.api_key = api_key
        self.clear_push_pcm = clear_push_pcm
        self.enable_vad = enable_vad
        self._exit = _exit
        # 是否是麦克风按压模式
        self.is_mic_press_mode = GLOBAL_CONFIG.get("IS_MIC_PRESS_MODE", True)

        self.audio_queue: Queue = Queue()
        self.monitor_task: Optional[asyncio.Task] = None
        self.result: List[str] = []
        self.running = False
        self.end_tag = {"end": True}
        self.clear_run_llm = clear_run_llm
        self.last_recognition_time = time.time()
        self.is_processing_llm = False
        self.silence_threshold = 1.0  # 2 seconds of silence threshold

        # 当前运行的LLM任务
        self.current_llm_task: Optional[asyncio.Task] = None

        # 角色分离跟踪
        self.current_role = None

        # 跟踪上一次推送音频帧的时间
        self.last_audio_frame_time = time.time()

        # 事件循环引用，用于跨线程安全操作
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.asr_task_is_running = False

        # 音频缓冲区，用于确保每次发送1280字节
        self.audio_buffer = bytearray()

    async def _handle_message(self, message: str) -> None:
        """处理WebSocket服务器返回的消息"""
        result_dict = json.loads(message)

        if result_dict["action"] == "started":
            logger.info(f"握手成功: {message}")

        elif result_dict["action"] == "result":
            text_result = result_dict["data"]
            # {"seg_id":1,"cn":{"st":{"rt":[{"ws":[{"cw":[{"sc":0.00,"w":"你","wp":"n","rl":"0","wb":1,"wc":0.00,"we":8}],"wb":1,"we":8},{"cw":[{"sc":0.00,"w":"所","wp":"n","rl":"0","wb":13,"wc":0.00,"we":24}],"wb":13,"we":24},{"cw":[{"sc":0.00,"w":"说","wp":"n","rl":"0","wb":25,"wc":0.00,"we":40}],"wb":25,"we":40},{"cw":[{"sc":0.00,"w":"1234","og":"一二三四","wp":"n","rl":"0","wb":41,"wc":0.00,"we":96}],"wb":41,"we":96}]}],"bg":"0","type":"0","ed":"1180"}},"ls":false}
            # logger.info(f"识别结果: {text_result}")

            # 从JSON结构中提取文字内容
            extracted_text = self._extract_text_from_result(json.loads(text_result))
            if extracted_text:
                logger.info(f"提取的文本: {extracted_text}")
                self.result.append(extracted_text)
                # 更新最后识别结果的时间
                current_time = time.time()
                self.last_recognition_time = current_time

            # else:
            # 如果无法解析，保存原始数据
            # self.result.append(text_result)

        elif result_dict["action"] == "error":
            logger.error(f"识别错误: {message}")
        else:
            logger.info(f"未知消息: {message}")

    def _extract_text_from_result(self, data: Dict) -> Optional[str]:
        """
        从讯飞实时语音转写结果中提取文字内容
        结构: data -> cn -> st -> rt -> ws -> cw -> w
        根据角色分离规则过滤内容
        """
        try:
            result_text = ""

            # 处理结构: data -> cn -> st -> rt -> ws -> cw -> w
            if "cn" in data and "st" in data["cn"]:
                # 如果 0-最终结果；1-中间结果
                result_type = data["cn"]["st"].get("type", "0")
                if result_type == "1":
                    # 忽略中间结果
                    return None

                rt_array = data["cn"]["st"].get("rt", [])

                for rt in rt_array:
                    ws_array = rt.get("ws", [])

                    for ws in ws_array:
                        cw_array = ws.get("cw", [])

                        for cw in cw_array:
                            if "w" in cw and "rl" in cw:
                                rl_value = cw["rl"]

                                # 检查角色标识
                                if rl_value == "1":
                                    # 角色1开始说话，我们要保留这个角色的内容
                                    self.current_role = "1"
                                    result_text += cw["w"]
                                elif rl_value == "0" and self.current_role == "1":
                                    # 角色1继续说话
                                    result_text += cw["w"]
                                elif rl_value != "0" and rl_value != "1":
                                    # 其他角色开始说话，记录当前角色但不保留文本
                                    self.current_role = rl_value
                                # 忽略其他角色的内容(rl不为1且不为0，或rl为0但当前角色不是1)

            return result_text
        except Exception as e:
            logger.error(f"从结果中提取文本时出错: {e}", exc_info=True)
            return ""

    def _count_effective_characters(self, text: str) -> int:
        """
        计算文本中除去中英文标点符号之外的字数
        排除中英文标点符号，统计其他所有字符（包括汉字、数字、英文字母等）
        """
        count = 0
        for char in text:
            # 排除空格和标点符号
            if char not in ALL_PUNCTUATION and not char.isspace():
                count += 1
        return count

    async def _process_llm(self, result_text: str) -> None:
        """处理LLM调用的封装方法"""
        self.is_processing_llm = True

        try:
            # 创建LLM任务并保存引用以便可以取消
            self.current_llm_task = asyncio.create_task(self.clear_run_llm(result_text))
            # 等待LLM任务完成
            await self.current_llm_task
            logger.info("LLM处理完成，已清空识别结果")
        except asyncio.CancelledError:
            logger.info("LLM任务被取消")
        except Exception as e:
            logger.error(f"调用LLM处理时出错: {e}", exc_info=True)
        finally:
            # 处理完成后清空结果 即使报错了也要清空
            self.result = []

            # 更新时间戳和处理状态
            self.last_recognition_time = time.time()
            self.is_processing_llm = False
            self.current_llm_task = None

    def reset_data(self):
        """重置处理器的数据"""
        self.result = []
        self.last_recognition_time = time.time()
        self.last_audio_frame_time = time.time()
        self.audio_buffer.clear()  # 清空音频缓冲区

    def is_running(self):
        """
        是否正在运行
        """
        return self.running

    def _create_url(self) -> str:
        """创建带鉴权参数的WebSocket URL"""
        base_url = "ws://rtasr.xfyun.cn/v1/ws"
        ts = str(int(mktime(datetime.now().timetuple())))

        # 生成baseString
        tt = (self.app_id + ts).encode("utf-8")
        md5 = hashlib.md5()
        md5.update(tt)
        baseString = md5.hexdigest()
        baseString = bytes(baseString, encoding="utf-8")

        # 生成签名
        apiKey = self.api_key.encode("utf-8")
        signa = hmac.new(apiKey, baseString, hashlib.sha1).digest()
        signa = base64.b64encode(signa)
        signa = str(signa, "utf-8")

        # roleType-是否开角色分离，默认不开启，传2开启 仅支持中文
        return f"{base_url}?appid={self.app_id}&ts={ts}&signa={quote(signa)}&vadMdn=2&roleType=2"

    async def _send_remaining_buffer(self, ws: websockets.ClientConnection) -> None:
        """
        发送剩余的音频缓冲区数据
        如果buffer大于1280字节，按40ms频率分批发送1280字节
        """
        if len(self.audio_buffer) == 0:
            return

        logger.info(f"开始发送剩余buffer: {len(self.audio_buffer)} 字节")

        try:
            # 如果buffer大于1280字节，需要分批发送
            if len(self.audio_buffer) > 1280:
                while len(self.audio_buffer) >= 1280:
                    # 取出前1280字节发送
                    data_to_send = bytes(self.audio_buffer[:1280])
                    await asyncio.wait_for(ws.send(data_to_send), timeout=5.0)
                    logger.info(
                        f"分批发送1280字节，剩余: {len(self.audio_buffer) - 1280} 字节"
                    )

                    # 从buffer中移除已发送的数据
                    self.audio_buffer = self.audio_buffer[1280:]

                    # 如果还有剩余数据需要发送，等待40ms
                    if len(self.audio_buffer) >= 1280:
                        await asyncio.sleep(0.04)  # 40ms

            # 发送最后剩余的不足1280字节的数据
            if len(self.audio_buffer) > 0:
                await asyncio.wait_for(ws.send(bytes(self.audio_buffer)), timeout=5.0)
                logger.info(f"发送最后剩余数据: {len(self.audio_buffer)} 字节")
                self.audio_buffer.clear()
        except asyncio.TimeoutError:
            logger.error("发送剩余buffer时超时")
            raise
        except Exception as e:
            logger.error(f"发送剩余buffer时出错: {e}", exc_info=True)
            raise

    async def _process_audio_frames(self, ws: websockets.ClientConnection) -> None:
        """处理音频帧数据的异步任务"""
        try:
            while self.running and ws and ws.state != websockets.protocol.State.CLOSED:
                # 每次从队列获取一帧数据
                try:
                    frame_data = await asyncio.wait_for(
                        self.audio_queue.get(), timeout=0.04
                    )

                    if isinstance(frame_data, dict) and frame_data.get("end", False):
                        # 发送剩余的buffer（如果有）
                        await self._send_remaining_buffer(ws)

                        # 发送结束标记，添加超时
                        await asyncio.wait_for(
                            ws.send(json.dumps(self.end_tag)), timeout=5.0
                        )
                        logger.info("已发送结束标记")
                        break

                    # 将新的frame_data添加到buffer中
                    self.audio_buffer.extend(frame_data)

                    # 如果buffer中的数据大于等于1280字节，则发送1280字节
                    while len(self.audio_buffer) >= 1280:
                        # 取出前1280字节发送
                        data_to_send = bytes(self.audio_buffer[:1280])
                        # 添加发送超时（5秒）
                        await asyncio.wait_for(ws.send(data_to_send), timeout=5.0)

                        # 从buffer中移除已发送的数据
                        self.audio_buffer = self.audio_buffer[1280:]

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"处理音频帧时出错: {e}", exc_info=True)
                    break
        except Exception as e:
            logger.error(f"音频处理任务异常: {e}", exc_info=True)
        finally:
            self.clear_audio_queue()
            logger.info("音频处理任务结束")

    async def _websocket_handler(self, ws: websockets.ClientConnection) -> None:
        """WebSocket连接处理的异步任务"""
        try:
            while self.running and ws and ws.state != websockets.protocol.State.CLOSED:
                try:
                    # 添加接收消息的超时（30秒）
                    message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    await self._handle_message(message)
                except asyncio.TimeoutError:
                    # 超时不是错误，继续循环
                    logger.debug("WebSocket接收消息超时，继续等待")
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.info("WebSocket连接已关闭")
                    break
                except Exception as e:
                    logger.error(f"WebSocket处理异常: {e}", exc_info=True)
                    break
        except Exception as e:
            logger.error(f"WebSocket处理任务异常: {e}", exc_info=True)
        finally:
            logger.info("WebSocket处理任务结束")

    async def _monitor_recognition_results(self) -> None:
        """监控识别结果并在超过静默阈值时调用LLM"""
        try:
            while self.running:
                # 每500ms检查一次
                await asyncio.sleep(0.5)

                # 检查是否超过静默阈值且不在处理LLM中
                current_time = time.time()
                time_since_last_recognition = current_time - self.last_recognition_time

                if time_since_last_recognition >= TIME_SINCE_LAST_RECOGNITION:
                    logger.info("超过静默阈值，终止服务")
                    self.running = False
                    break

                # 检查识别结果并决定是否调用LLM
                if not self.result or self.is_processing_llm:
                    continue

                result_text = self.get_result_text()
                if not result_text:
                    continue

                effective_char_count = self._count_effective_characters(result_text)

                # 小于3个汉字则忽略
                if effective_char_count < 3:
                    logger.debug(
                        f"识别结果有效字数太少({effective_char_count}个)，忽略: {result_text}"
                    )
                    self.result = []
                    continue

                # 判断是否需要调用LLM
                should_trigger = False

                # 检查汉字数量是否超过50个
                if effective_char_count >= 50:
                    logger.debug(
                        f"识别结果有效字数超过50个({effective_char_count}个)，强制调用LLM处理: {result_text}"
                    )
                    should_trigger = True

                # 检查是否达到静默阈值
                if time_since_last_recognition >= self.silence_threshold:
                    logger.debug(
                        f"检测到{self.silence_threshold}秒静默({effective_char_count}个有效字)，调用LLM处理: {result_text}"
                    )
                    should_trigger = True

                if should_trigger:
                    self.put_aduio_frame_end()
                    await self._process_llm(result_text)

        except Exception as e:
            logger.error(f"监控识别结果任务异常: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("监控识别结果任务结束")

    async def start_llm_task(self) -> None:
        logger.info("开始按键模式的LLM任务")
        if self.current_llm_task and not self.current_llm_task.done():
            self.current_llm_task.cancel()
            try:
                await self.current_llm_task
            except asyncio.CancelledError:
                pass
        self.current_llm_task = asyncio.create_task(
            self.clear_run_llm(self.get_result_text())
        )
        # 启动后就清空结果
        self.result = []

    async def _start_asr_task(self):
        """启动ASR任务，增加超时和错误处理"""
        if self.asr_task_is_running:
            logger.warning("ASR任务已在运行中，跳过")
            return

        self.asr_task_is_running = True
        _ws = None
        _ws_task = None
        _process_task = None

        try:
            # 启动前清空推送
            self.clear_push_pcm()

            # 创建WebSocket连接，添加超时
            _ws_url = self._create_url()
            logger.info(f"正在连接WebSocket: {_ws_url}")

            # 添加连接超时（10秒）
            _ws = await asyncio.wait_for(websockets.connect(_ws_url), timeout=10.0)
            logger.info("WebSocket连接建立成功")

            # 创建处理任务
            _ws_task = asyncio.create_task(self._websocket_handler(_ws))
            _process_task = asyncio.create_task(self._process_audio_frames(_ws))

            logger.info("ASR启动成功")

            # 等待任务完成，添加整体超时
            await asyncio.wait_for(
                asyncio.gather(_ws_task, _process_task, return_exceptions=True),
                timeout=300.0,  # 5分钟超时
            )

        except asyncio.TimeoutError:
            logger.error("ASR任务超时")
        except asyncio.CancelledError:
            logger.info("ASR任务被取消")
        except Exception as e:
            logger.error(f"ASR任务出错: {e}", exc_info=True)
        finally:
            if self.is_mic_press_mode:
                await self.start_llm_task()
            # 确保任务被正确取消
            if _ws_task and not _ws_task.done():
                _ws_task.cancel()
                try:
                    await asyncio.wait_for(_ws_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

            if _process_task and not _process_task.done():
                _process_task.cancel()
                try:
                    await asyncio.wait_for(_process_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

            # 确保WebSocket连接被关闭
            if _ws and _ws.state != websockets.protocol.State.CLOSED:
                try:
                    await asyncio.wait_for(_ws.close(), timeout=2.0)
                except Exception as e:
                    logger.error(f"关闭WebSocket连接时出错: {e}")

            self.asr_task_is_running = False
            logger.info("ASR任务结束，资源已清理")

    async def start_rtasr(self):
        """
        启动RTASR
        """
        if self.running:
            logger.warning("RTASR已在运行中")
            return

        self.running = True
        # 保存当前事件循环的引用
        self.loop = asyncio.get_running_loop()

        try:
            if not self.is_mic_press_mode:
                # 启动处理任务
                self.monitor_task = asyncio.create_task(
                    self._monitor_recognition_results()
                )

                # 等待任务完成
                await self.monitor_task
            else:
                await self._exit.wait()
        except Exception as e:
            logger.error(f"启动RTASR失败: {e}", exc_info=True)
            self.running = False
        finally:
            # if self.ws:
            #     try:
            #         await self.ws.close()
            #     except Exception as e:
            #         logger.error(f"关闭WebSocket连接时出错: {e}", exc_info=True)
            self.running = False
            self.loop = None  # 清除事件循环引用
            if self.monitor_task and not self.monitor_task.done():
                self.monitor_task.cancel()
            logger.info("RTASR任务已取消")

    def put_audio_frame(self, frame: AudioFrame, vad_result_state: int) -> None:
        """
        发送音频帧 - 线程安全版本
        """
        if not self.running or not self.loop:
            logger.warning("RTASR未启动或事件循环未设置，无法发送音频帧")
            return

        # 更新上一次推送音频帧的时间
        self.last_audio_frame_time = time.time()

        # 使用 call_soon_threadsafe 安全地从外部线程向队列中添加数据
        try:
            self.loop.call_soon_threadsafe(
                self.audio_queue.put_nowait, bytes(frame.buffer)
            )
        except Exception as e:
            logger.error(f"添加音频帧到队列时出错: {e}", exc_info=True)

        try:
            if not self.asr_task_is_running:
                # 如果不是麦克风模式则不允许用户说话直到当前 llm 结束
                if self.is_mic_press_mode or not self.is_processing_llm:
                    if self.enable_vad:
                        if vad_result_state > 0:
                            # 使用更安全的任务创建方式
                            def safe_create_task():
                                try:
                                    if not self.asr_task_is_running:  # 双重检查
                                        asyncio.create_task(self._start_asr_task())
                                except Exception as e:
                                    logger.error(f"创建ASR任务失败: {e}", exc_info=True)

                            self.loop.call_soon_threadsafe(safe_create_task)
                    else:
                        # 使用更安全的任务创建方式
                        def safe_create_task():
                            try:
                                if not self.asr_task_is_running:  # 双重检查
                                    asyncio.create_task(self._start_asr_task())
                            except Exception as e:
                                logger.error(f"创建ASR任务失败: {e}", exc_info=True)

                        self.loop.call_soon_threadsafe(safe_create_task)
        except Exception as e:
            logger.error(f"启动ASR任务时出错: {e}", exc_info=True)

    def put_aduio_frame_end(self) -> None:
        if not self.running or not self.loop or not self.asr_task_is_running:
            logger.warning("RTASR未启动或事件循环未设置或ASR任务未启动，无法发送音频帧")
            return
        data = {
            "end": True,
        }
        logger.info("put_aduio_frame_end")
        self.loop.call_soon_threadsafe(self.audio_queue.put_nowait, data)

    def stop_rtasr(self) -> None:
        """停止RTASR处理"""
        # 标记为 false 后，在 _monitor_recognition_results 函数结束后就会退出了（_monitor_recognition_results会在 run_llm 结束后结束）
        self.running = False

    # def stop_rtasr(self) -> None:
    #     """停止RTASR处理"""
    #     if not self.running:
    #         return

    #     # 发送结束标记
    #     self.audio_queue.put_nowait({"end": True})

    def get_result_text(self) -> str:
        """
        获取最终的识别结果文本
        """
        return "".join(self.result)

    def clear_audio_queue(self) -> None:
        """安全地清空音频队列"""
        if not hasattr(self, "audio_queue") or not self.audio_queue:
            self.audio_queue = asyncio.Queue()
            return

        # 尝试清空现有队列中的所有数据
        try:
            while True:
                self.audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass  # 队列已空

        # 重新创建队列以确保完全清空
        self.audio_queue = asyncio.Queue()

        # 清空音频缓冲区
        if hasattr(self, "audio_buffer"):
            self.audio_buffer.clear()


def init_short_rtasr(
    clear_run_llm: Callable[[str], Awaitable[None]],
    clear_push_pcm: Callable[[], None],
    enable_vad: bool,
    _exit: asyncio.Event,
) -> RtAsProcessorShort:
    """初始化ASR处理器"""
    return RtAsProcessorShort(
        app_id=XUNFEI_APP_ID,
        api_key=XUNFEI_API_KEY,
        clear_run_llm=clear_run_llm,
        clear_push_pcm=clear_push_pcm,
        enable_vad=enable_vad,
        _exit=_exit,
    )
