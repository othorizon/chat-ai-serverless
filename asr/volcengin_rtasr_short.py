import asyncio
import gzip
import json
import string
import uuid
from asyncio import Queue
import logging
import time
from typing import Dict, List, Optional, Callable, Awaitable

import websockets
from agora.rtc.audio_frame_observer import AudioFrame

from config import REMOTE_AUDIO_SAMPLE_RATE


logger = logging.getLogger(__name__)

# 火山引擎双向流式语音识别服务
# API参考文档：https://www.volcengine.com/docs/6561/1354869

# 协议相关常量
PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

# Message Type:
FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Message Type Specific Flags
NO_SEQUENCE = 0b0000  # no check sequence
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_WITH_SEQUENCE = 0b0011
NEG_SEQUENCE_1 = 0b0011

# Message Serialization
NO_SERIALIZATION = 0b0000
JSON = 0b0001

# Message Compression
NO_COMPRESSION = 0b0000
GZIP = 0b0001

# 如果时间过短的话，注意当执行函数调用时，等待时间会比较长，要注意特殊处理才行
TIME_SINCE_LAST_RECOGNITION = 5 * 60  # n分钟静后终止服务

# 定义中英文标点符号（全局变量）
ENGLISH_PUNCTUATION = string.punctuation  # !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
CHINESE_PUNCTUATION = (
    "。，！？；：" "''（）【】｛｝—·…、《》〈〉「」『』〔〕〖〗〘〙〚〛"
)
ALL_PUNCTUATION = ENGLISH_PUNCTUATION + CHINESE_PUNCTUATION


def generate_header(
    message_type=FULL_CLIENT_REQUEST,
    message_type_specific_flags=NO_SEQUENCE,
    serial_method=JSON,
    compression_type=GZIP,
    reserved_data=0x00,
):
    """
    生成火山引擎协议头部
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    """
    header = bytearray()
    header_size = 1
    header.append((PROTOCOL_VERSION << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    return header


def generate_before_payload(sequence: int):
    """生成序列号部分"""
    before_payload = bytearray()
    before_payload.extend(sequence.to_bytes(4, "big", signed=True))  # sequence
    return before_payload


def parse_response(res):
    """
    解析火山引擎服务器响应
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    header_extensions 扩展头(大小等于 8 * 4 * (header_size - 1) )
    payload 类似与http 请求体
    """
    protocol_version = res[0] >> 4
    header_size = res[0] & 0x0F
    message_type = res[1] >> 4
    message_type_specific_flags = res[1] & 0x0F
    serialization_method = res[2] >> 4
    message_compression = res[2] & 0x0F
    reserved = res[3]
    header_extensions = res[4 : header_size * 4]
    payload = res[header_size * 4 :]
    result = {
        "is_last_package": False,
    }
    payload_msg = None
    payload_size = 0
    if message_type_specific_flags & 0x01:
        # receive frame with sequence
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result["payload_sequence"] = seq
        payload = payload[4:]

    if message_type_specific_flags & 0x02:
        # receive last package
        result["is_last_package"] = True

    if message_type == FULL_SERVER_RESPONSE:
        payload_size = int.from_bytes(payload[:4], "big", signed=True)
        payload_msg = payload[4:]
    elif message_type == SERVER_ACK:
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result["seq"] = seq
        if len(payload) >= 8:
            payload_size = int.from_bytes(payload[4:8], "big", signed=False)
            payload_msg = payload[8:]
    elif message_type == SERVER_ERROR_RESPONSE:
        code = int.from_bytes(payload[:4], "big", signed=False)
        result["code"] = code
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:]
    if payload_msg is None:
        return result
    if message_compression == GZIP:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON:
        payload_msg = json.loads(str(payload_msg, "utf-8"))
    elif serialization_method != NO_SERIALIZATION:
        payload_msg = str(payload_msg, "utf-8")
    result["payload_msg"] = payload_msg
    result["payload_size"] = payload_size
    return result


class VolcStreamAsrProcessorShort:

    def __init__(
        self,
        app_id: str,
        access_key: str,
        resource_id: str,
        clear_run_llm: Callable[[str], Awaitable[None]],
        clear_push_pcm: Callable[[], None],
        enable_vad:bool,
    ):
        self.app_id = app_id
        self.access_key = access_key
        self.resource_id = resource_id
        self.clear_push_pcm = clear_push_pcm
        self.enable_vad = enable_vad
        
        self.audio_queue: Queue = Queue()
        self.monitor_task: Optional[asyncio.Task] = None
        self.result: List[str] = []
        self.running = False
        self.end_tag = {"end": True}
        self.clear_run_llm = clear_run_llm
        self.last_recognition_time = time.time()
        self.is_processing_llm = False
        self.llm_task = None
        self.silence_threshold = 1.0  # 2 seconds of silence threshold

        # 当前运行的LLM任务
        self.current_llm_task: Optional[asyncio.Task] = None

        # 跟踪上一次推送音频帧的时间
        self.last_audio_frame_time = time.time()

        # 事件循环引用，用于跨线程安全操作
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.asr_task_is_running = False
        self.asr_task_pushed_end = False

        # 火山引擎相关配置
        self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
        self.uid = "rtc_user"
        self.format = "pcm"
        self.rate = REMOTE_AUDIO_SAMPLE_RATE
        self.bits = 16
        self.channel = 1
        self.codec = "raw"
        self.success_code = 1000

        # 序列号管理
        self.seq = 1
        self.reqid = None

    async def _handle_message(self, message: bytes) -> None:
        """处理火山引擎WebSocket服务器返回的消息"""
        try:
            result = parse_response(message)
            # logger.info(f"解析的响应: {result}")

            # 处理初始握手响应
            if "payload_msg" in result and isinstance(result["payload_msg"], dict):
                payload = result["payload_msg"]

                # 检查是否有错误
                if "code" in result and result["code"] != self.success_code:
                    raise Exception(f"服务器错误: {result}")

                # 检查payload中的code
                if "code" in payload and payload["code"] != self.success_code:
                    raise Exception(f"识别错误: {payload}")

                # 提取识别结果
                if "result" in payload:
                    asr_result = payload["result"]
                    # logger.info(f"xxxxxx: {asr_result}")
                    if "utterances" in asr_result and asr_result["utterances"]:
                        if (
                            len(asr_result["utterances"]) > 0
                            and bool(asr_result["utterances"][0]["definite"]) is True
                        ):
                            logger.info(
                                f"识别结果: {asr_result['utterances'][0]['text']}"
                            )
                            self.result.append(asr_result["utterances"][0]["text"])
                            self.last_recognition_time = time.time()
                        # logger.info(f"utterances length: {len(asr_result['utterances'])}")
                        # if len(asr_result['utterances'])>0 and bool(asr_result['utterances'][0]['definite']) is True:
                        #     logger.info(f"识别结果: {asr_result['utterances'][0]['text']}")
                        #     self.result.append(asr_result['utterances'][0]['text'])
                        #     self.last_recognition_time = time.time()
                    # if 'text' in asr_result and asr_result['text']:
                    #     extracted_text = asr_result['text'].strip()
                    #     if extracted_text:
                    #         # logger.info(f"识别结果: {extracted_text}")
                    #         # self.result=[extracted_text]
                    #         # 更新最后识别结果的时间
                    #         self.last_recognition_time = time.time()

                # 处理会话结束
                if "is_last_package" in result and result["is_last_package"]:
                    logger.info("会话结束")

        except Exception as e:
            logger.error(f"处理消息时出错: msg:{message}, {e}", exc_info=True)
            raise e

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

    def is_running(self):
        """
        是否正在运行
        """
        return self.running

    def _create_headers(self) -> Dict[str, str]:
        """创建火山引擎WebSocket连接头部"""
        self.reqid = str(uuid.uuid4())
        headers = {
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-App-Key": self.app_id,
            "X-Api-Request-Id": self.reqid,
        }
        return headers

    # 当长度超过音频force_to_speech_time后，然后静音时间超过end_window_size。就会判停。并设置分句为definite=true
    def _construct_request(self) -> Dict:
        """构造火山引擎识别请求参数"""
        req = {
            "user": {
                "uid": self.uid,
            },
            "audio": {
                "format": self.format,
                "sample_rate": self.rate,
                "bits": self.bits,
                "channel": self.channel,
                "codec": self.codec,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_punc": True,
                "show_utterances": True,
                "result_type": "single",  # 增量返回
                # "vad_segment_duration":1000,
                "end_window_size": 800,
                "force_to_speech_time": 1,
            },
        }
        return req

    async def _process_audio_frames(self, ws: websockets.ClientConnection) -> None:
        """处理音频帧数据的异步任务 - 火山引擎协议"""
        try:
            # 首先发送初始化请求
            request_params = self._construct_request()
            payload_bytes = str.encode(json.dumps(request_params))
            payload_bytes = gzip.compress(payload_bytes)

            # 构造初始请求
            full_client_request = bytearray(
                generate_header(message_type_specific_flags=POS_SEQUENCE)
            )
            full_client_request.extend(generate_before_payload(sequence=self.seq))
            full_client_request.extend((len(payload_bytes)).to_bytes(4, "big"))
            full_client_request.extend(payload_bytes)

            await ws.send(full_client_request)
            logger.info("已发送初始化请求")

            # 处理音频数据
            audio_buffer = bytearray()
            segment_size = int(
                self.rate * 2 * self.channel * 100 / 1000
            )  # 100ms的音频数据

            while self.running and ws.state != websockets.protocol.State.CLOSED:
                try:
                    # timeout 和火山的 ws的等包超时时间有关系
                    frame_data = await asyncio.wait_for(
                        self.audio_queue.get(), timeout=1
                    )

                    if isinstance(frame_data, dict) and frame_data.get("end", False):
                        # 发送剩余的音频数据（如果有）并标记结束
                        if audio_buffer:
                            await self._send_audio_chunk(
                                ws, bytes(audio_buffer), is_last=True
                            )
                        logger.info("已发送结束标记")
                        break

                    # 将音频数据添加到缓冲区
                    audio_buffer.extend(frame_data)

                    # 当缓冲区达到分片大小时发送
                    while len(audio_buffer) >= segment_size:
                        chunk = audio_buffer[:segment_size]
                        audio_buffer = audio_buffer[segment_size:]
                        await self._send_audio_chunk(ws, bytes(chunk), is_last=False)

                except asyncio.TimeoutError:
                    logger.info("音频等包超时，推送结束包")
                    self.put_aduio_frame_end()
                    continue
                except Exception as e:
                    logger.error(f"处理音频帧时出错: {e}", exc_info=True)
                    break
        except Exception as e:
            logger.error(f"音频处理任务异常: {e}", exc_info=True)
        finally:
            logger.info("音频处理任务结束")

    async def _send_audio_chunk(
        self, ws: websockets.ClientConnection, chunk_data: bytes, is_last: bool
    ) -> None:
        """发送音频数据块"""
        try:
            self.seq += 1
            if is_last:
                seq = -self.seq
                message_flags = NEG_WITH_SEQUENCE
            else:
                seq = self.seq
                message_flags = POS_SEQUENCE

            # 压缩音频数据
            payload_bytes = gzip.compress(chunk_data)

            # 构造音频请求
            audio_request = bytearray(
                generate_header(
                    message_type=AUDIO_ONLY_REQUEST,
                    message_type_specific_flags=message_flags,
                )
            )
            audio_request.extend(generate_before_payload(sequence=seq))
            audio_request.extend((len(payload_bytes)).to_bytes(4, "big"))
            audio_request.extend(payload_bytes)

            await ws.send(audio_request)

        except Exception as e:
            logger.error(f"发送音频块时出错: {e}", exc_info=True)

    async def _websocket_handler(self, ws: websockets.ClientConnection) -> None:
        """WebSocket连接处理的异步任务"""
        try:
            while self.running and ws.state != websockets.protocol.State.CLOSED:
                try:
                    message = await ws.recv()
                    await self._handle_message(message)
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

    async def clear_audio_queue(self) -> None:
        """安全地清空音频队列"""
        if not hasattr(self, 'audio_queue') or not self.audio_queue:
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

    async def _monitor_recognition_results(self) -> None:
        """监控识别结果并在超过静默阈值时调用LLM"""
        try:
            while self.running:
                # 每500ms检查一次
                await asyncio.sleep(0.5)
                if self.llm_task and not self.llm_task.done():
                    continue

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
                    logger.info(
                        f"识别结果有效字数太少({effective_char_count}个)，忽略: {result_text}"
                    )
                    self.result = []
                    continue

                # 判断是否需要调用LLM
                should_trigger = False

                # 检查汉字数量是否超过50个
                # if effective_char_count >= 50:
                #     logger.info(
                #         f"识别结果有效字数超过50个({effective_char_count}个)，强制调用LLM处理: {result_text}"
                #     )
                #     should_trigger = True

                # 检查是否达到静默阈值
                if time_since_last_recognition >= self.silence_threshold:
                    logger.info(
                        f"检测到{self.silence_threshold}秒静默({effective_char_count}个有效字)，调用LLM处理: {result_text}"
                    )
                    should_trigger = True

                if should_trigger:
                    self.put_aduio_frame_end()
                    self.llm_task = asyncio.create_task(self._process_llm(result_text))

        except Exception as e:
            logger.error(f"监控识别结果任务异常: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("监控识别结果任务结束")

    async def _start_asr_task(self):
        self.asr_task_is_running = True
        self.asr_task_pushed_end = False
        self.result = []
        # 初始化序号
        self.seq = 1
        # 创建火山引擎WebSocket连接
        headers = self._create_headers()
        try:
            _ws = await websockets.connect(self.ws_url, additional_headers=headers)
            _ws_task = asyncio.create_task(self._websocket_handler(_ws))
            _process_task = asyncio.create_task(self._process_audio_frames(_ws))
            logger.info("火山引擎ASR启动成功")
            await _process_task
            try:
                await asyncio.wait_for(_ws_task, timeout=2.0)
            except asyncio.TimeoutError:
                logger.error("_ws_task等待超时，取消任务")
                # 取消任务
                _ws_task.cancel()
        except asyncio.CancelledError:
            logger.info("ASR任务被取消")
        except Exception as e:
            logger.error(f"ASR任务出错: {e}", exc_info=True)
        finally:
            self.asr_task_is_running = False
            logger.info("ASR任务结束")

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
            # 启动处理任务
            self.monitor_task = asyncio.create_task(self._monitor_recognition_results())

            # 等待任务完成
            await self.monitor_task
            if self.llm_task and not self.llm_task.done():
                await self.llm_task
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

    def put_audio_frame(self, frame: AudioFrame,vad_result_state:int) -> None:
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
            if not self.asr_task_is_running and not self.is_processing_llm:
                if self.enable_vad:
                    if vad_result_state>0:
                        logger.info("vad_result_state>0, start_asr_task")
                        self.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self._start_asr_task())
                        )
                else:
                    logger.info("start_asr_task")
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._start_asr_task())
                    )
        except Exception as e:
            logger.error(f"启动ASR任务时出错: {e}", exc_info=True)

    def put_aduio_frame_end(self) -> None:
        if not self.running or not self.loop:
            logger.warning("RTASR未启动或事件循环未设置，无法发送音频帧")
            return
        data = {
            "end": True,
        }
        if self.asr_task_pushed_end:
            return
        self.asr_task_pushed_end = True
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


# https://www.volcengine.com/docs/6561/1354869#full-server-response
def init_short_rtasr(
    clear_run_llm: Callable[[str], Awaitable[None]],
    clear_push_pcm: Callable[[], None],
    enable_vad:bool,
) -> VolcStreamAsrProcessorShort:
    """初始化火山引擎ASR处理器"""
    return VolcStreamAsrProcessorShort(
        app_id="",  # 请替换为你的火山引擎应用ID
        access_key="",  # 请替换为你的火山引擎访问密钥
        resource_id="",
        clear_run_llm=clear_run_llm,
        clear_push_pcm=clear_push_pcm,
        enable_vad=enable_vad,
    )
