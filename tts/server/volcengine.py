import asyncio
import os
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Union

import websockets

from config import DEFAULT_VOLC_TTS_SPEAKER, GLOBAL_CONFIG, VOLC_AUDIO_PARAMS, VOLC_ADDTIONS_PARAMS, VOLC_APP_ID, VOLC_SK, VOLC_HOST, VOLC_RESOURCE_ID
from rtc.rtc_base import RemoteState
from tts.server.base_tts import BaseTts

logger = logging.getLogger(__name__)

# Constants
APPID = VOLC_APP_ID
SK = VOLC_SK
HOST = VOLC_HOST
RESOURCE_ID = VOLC_RESOURCE_ID
NAMESPACE = "BidirectionalTTS"
# https://www.volcengine.com/docs/6561/1257544
# SPEAKER_MEILI_NVYOU = "ICL_zh_female_huoponvhai_tob"
# SPEAKER_MEILI_NVYOU = "zh_female_meilinvyou_moon_bigtts"
# TTS_SPEAKER = "zh_female_roumeinvyou_emo_v2_mars_bigtts"


class MsgType(IntEnum):
    INVALID = 0
    FULL_CLIENT = 1
    AUDIO_ONLY_CLIENT = 2
    FULL_SERVER = 9
    AUDIO_ONLY_SERVER = 11
    FRONT_END_RESULT_SERVER = 12
    ERROR = 15


MSG_TYPE_TO_BITS = {
    MsgType.FULL_CLIENT: 0b1 << 4,
    MsgType.AUDIO_ONLY_CLIENT: 0b10 << 4,
    MsgType.FULL_SERVER: 0b1001 << 4,
    MsgType.AUDIO_ONLY_SERVER: 0b1011 << 4,
    MsgType.FRONT_END_RESULT_SERVER: 0b1100 << 4,
    MsgType.ERROR: 0b1111 << 4,
}

BITS_TO_MSG_TYPE = {v: k for k, v in MSG_TYPE_TO_BITS.items()}


class Event(IntEnum):
    EVENT_NONE = 0
    EVENT_START_CONNECTION = 1
    EVENT_FINISH_CONNECTION = 2
    EVENT_CONNECTION_STARTED = 50
    EVENT_CONNECTION_FAILED = 51
    EVENT_CONNECTION_FINISHED = 52
    EVENT_START_SESSION = 100
    EVENT_FINISH_SESSION = 102
    EVENT_SESSION_STARTED = 150
    EVENT_SESSION_FINISHED = 152
    EVENT_SESSION_FAILED = 153
    EVENT_TASK_REQUEST = 200
    EVENT_TTS_SENTENCE_START = 350
    EVENT_TTS_SENTENCE_END = 351
    EVENT_TTS_RESPONSE = 352


# Protocol Constants
VERSION1 = 0b0001 << 4
HEADER_SIZE4 = 1
SERIALIZATION_JSON = 0b0001 << 4
COMPRESSION_NONE = 0
MSG_TYPE_FLAG_WITH_EVENT = 0b100


class BinaryReader:
    def __init__(self, buffer: bytes):
        self.buffer = buffer
        self.offset = 0

    def read_byte(self) -> int:
        value = self.buffer[self.offset]
        self.offset += 1
        return value

    def read_int32(self) -> int:
        value = int.from_bytes(
            self.buffer[self.offset : self.offset + 4], "big", signed=True
        )
        self.offset += 4
        return value

    def read_uint32(self) -> int:
        value = int.from_bytes(self.buffer[self.offset : self.offset + 4], "big")
        self.offset += 4
        return value

    def read_bytes(self, length: int) -> bytes:
        value = self.buffer[self.offset : self.offset + length]
        self.offset += length
        return value


@dataclass
class Message:
    type: MsgType
    type_and_flag_bits: int = 0
    event: int = 0
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: Union[str, bytes] = b""

    def __init__(self, msg_type: MsgType, type_flag: int = MSG_TYPE_FLAG_WITH_EVENT):
        self.type = msg_type
        self.type_and_flag_bits = MSG_TYPE_TO_BITS[msg_type] + type_flag
        self.event = 0
        self.session_id = ""
        self.connect_id = ""
        self.sequence = 0
        self.error_code = 0
        self.payload = b""

    def __str__(self):
        return (
            f"Message(type={self.type}, event={self.event}, "
            f"session_id={self.session_id}, sequence={self.sequence}, "
            f"error_code={self.error_code}, payload_size={len(self.payload)})"
        )


class MsgTypeFlagBits(IntEnum):
    MSG_TYPE_FLAG_NO_SEQ = 0
    MSG_TYPE_FLAG_POSITIVE_SEQ = 0b1
    MSG_TYPE_FLAG_LAST_NO_SEQ = 0b10
    MSG_TYPE_FLAG_NEGATIVE_SEQ = 0b11
    MSG_TYPE_FLAG_WITH_EVENT = 0b100


class BinaryProtocol:
    def __init__(self):
        self.version_and_header_size = VERSION1 | HEADER_SIZE4
        self.serialization_and_compression = SERIALIZATION_JSON | COMPRESSION_NONE

    def marshal(self, msg: Message) -> bytes:
        # 创建一个 BytesIO 对象来构建二进制数据
        buffer = bytearray()

        # 写入头部
        # 1. Version and Header Size byte
        buffer.extend([self.version_and_header_size])

        # 2. Message Type and Flag byte
        buffer.extend([msg.type_and_flag_bits])

        # 3. Serialization and Compression byte
        buffer.extend([self.serialization_and_compression])

        # 4. Padding byte
        buffer.extend([0x00])

        # 如果消息包含事件（检查标志位）
        if msg.type_and_flag_bits & MsgTypeFlagBits.MSG_TYPE_FLAG_WITH_EVENT:
            # 写入事件编号 (4 bytes, big-endian)
            buffer.extend(msg.event.to_bytes(4, "big", signed=True))

            # 对于某些事件，不需要写入 session_id
            skip_session_id = msg.event in [
                Event.EVENT_START_CONNECTION.value,
                Event.EVENT_FINISH_CONNECTION.value,
                Event.EVENT_CONNECTION_STARTED.value,
                Event.EVENT_CONNECTION_FAILED.value,
                Event.EVENT_CONNECTION_FINISHED.value,
            ]

            if not skip_session_id:
                # 写入 session_id 长度 (4 bytes, big-endian)
                session_id_bytes = msg.session_id.encode("utf-8")
                buffer.extend(len(session_id_bytes).to_bytes(4, "big"))
                # 写入 session_id 内容
                buffer.extend(session_id_bytes)

        # 处理不同类型消息的特殊字段
        if msg.type == MsgType.AUDIO_ONLY_CLIENT:
            if self.contains_sequence(msg.type_and_flag_bits):
                # 写入序列号 (4 bytes, big-endian)
                buffer.extend(msg.sequence.to_bytes(4, "big", signed=True))

        elif msg.type == MsgType.ERROR:
            # 写入错误码 (4 bytes, big-endian)
            buffer.extend(msg.error_code.to_bytes(4, "big"))

        # 写入 payload
        if isinstance(msg.payload, str):
            payload_bytes = msg.payload.encode("utf-8")
        else:
            payload_bytes = msg.payload

        # 写入 payload 长度 (4 bytes, big-endian)
        buffer.extend(len(payload_bytes).to_bytes(4, "big"))
        # 写入 payload 内容
        buffer.extend(payload_bytes)

        return bytes(buffer)

    @staticmethod
    def contains_sequence(msg_type_flag: int) -> bool:
        """检查消息类型标志是否包含序列号"""
        return msg_type_flag in [
            MsgTypeFlagBits.MSG_TYPE_FLAG_POSITIVE_SEQ,
            MsgTypeFlagBits.MSG_TYPE_FLAG_NEGATIVE_SEQ,
        ]

    def unmarshal(self, data: bytes) -> Message:
        reader = BinaryReader(data)

        # 读取头部 (4 bytes)
        # pylint: disable=unused-variable
        version_size = reader.read_byte()
        type_and_flag = reader.read_byte()
        # pylint: disable=unused-variable
        serialization_compression = reader.read_byte()
        reader.read_byte()  # padding

        # 解析消息类型
        msg_type_bits = type_and_flag & 0xF0
        msg_type = BITS_TO_MSG_TYPE.get(msg_type_bits, MsgType.INVALID)

        # 创建消息对象
        msg = Message(msg_type)
        msg.type_and_flag_bits = type_and_flag

        # 根据标志位判断是否包含事件
        if type_and_flag & MsgTypeFlagBits.MSG_TYPE_FLAG_WITH_EVENT:
            msg.event = reader.read_int32()

            # 对于某些事件，不需要读取 session_id
            skip_session_id = msg.event in [
                Event.EVENT_START_CONNECTION.value,
                Event.EVENT_FINISH_CONNECTION.value,
                Event.EVENT_CONNECTION_STARTED.value,
                Event.EVENT_CONNECTION_FAILED.value,
                Event.EVENT_CONNECTION_FINISHED.value,
            ]

            if not skip_session_id:
                session_id_length = reader.read_uint32()
                if session_id_length > 0:
                    msg.session_id = reader.read_bytes(session_id_length).decode(
                        "utf-8"
                    )

        # 处理不同类型消息的特殊字段
        if msg.type == MsgType.AUDIO_ONLY_CLIENT:
            if self.contains_sequence(msg.type_and_flag_bits):
                msg.sequence = reader.read_int32()

        elif msg.type == MsgType.ERROR:
            msg.error_code = reader.read_uint32()

        # 读取 payload
        payload_size = reader.read_uint32()
        if payload_size > 0:
            msg.payload = reader.read_bytes(payload_size)
            # 对于某些消息类型，尝试解码 payload
            if msg.type in [MsgType.FULL_CLIENT, MsgType.FULL_SERVER, MsgType.ERROR]:
                try:
                    msg.payload = msg.payload.decode("utf-8")
                except UnicodeDecodeError:
                    pass  # 保持原始字节

        return msg


def _gen_log_id() -> str:
    timestamp = int(time.time() * 1000)
    random_num = uuid.uuid4().int & ((1 << 24) - 1)
    return f"02{timestamp}{'0' * 32}{random_num:06x}"


class VolcEngineTts(BaseTts):
    def __init__(
        self,
        _exit: asyncio.Event,
        remote: RemoteState,
        audio_callback: Callable[[bytes], None],
        end_callback: Callable[[], None],
        event_callback: Callable[[dict], None] = None,
    ):
        super().__init__(_exit, remote, audio_callback, end_callback, event_callback)
        self.queue: asyncio.Queue[Message] = asyncio.Queue()
        self.session = None
        self.ws = None
        self.protocol = BinaryProtocol()
        self.session_id = str(uuid.uuid4())
        self.audio_params = VOLC_AUDIO_PARAMS
        self.additions = VOLC_ADDTIONS_PARAMS


    def get_tts_speaker(self):
        return GLOBAL_CONFIG.get("VOLC_TTS_SPEAKER", DEFAULT_VOLC_TTS_SPEAKER)
    
    def put_end(self):
        # end session
        msg = Message(MsgType.FULL_CLIENT)
        msg.event = Event.EVENT_FINISH_SESSION
        msg.session_id = self.session_id
        msg.payload = b"{}"
        self.queue.put_nowait(msg)

        # end connection
        msg = Message(MsgType.FULL_CLIENT)
        msg.event = Event.EVENT_FINISH_CONNECTION
        msg.payload = b"{}"
        self.queue.put_nowait(msg)

    def put_data(self, data: str):
        req = {
            "event": Event.EVENT_TASK_REQUEST,
            "namespace": NAMESPACE,
            "req_params": {
                "text": data,
                "speaker": self.get_tts_speaker(),
                "audio_params": self.audio_params,
                "additions": json.dumps(self.additions),
            },
        }

        msg = Message(MsgType.FULL_CLIENT)
        msg.event = Event.EVENT_TASK_REQUEST
        msg.session_id = self.session_id
        msg.payload = json.dumps(req)
        self.queue.put_nowait(msg)

    async def start(self):
        logger.info("start VolcTTS")
        sender_task = None
        try:
            await self._connect()
            await self._start_tts_session()
            sender_task = asyncio.create_task(self._sender())
            # rece
            while (not self._exit.is_set()) and (not self.remote.is_playing()):
                msg = await self.ws.recv()
                msg = self.protocol.unmarshal(msg)
                if msg.type == MsgType.AUDIO_ONLY_SERVER:
                    self.audio_callback(msg.payload)
                elif msg.type == MsgType.FULL_SERVER:
                    if msg.event == Event.EVENT_TTS_SENTENCE_END:
                        if self.event_callback:
                            self.event_callback(
                                {
                                    "type": "tts_sentence_end",
                                    "text": msg.payload,
                                }
                            )
                    elif msg.event == Event.EVENT_SESSION_FINISHED:
                        break
                elif msg.type == MsgType.ERROR:
                    logger.error(f"Error received: {msg.payload}")
                    break
        finally:
            # 接收结束后不再关心发送
            if sender_task:
                sender_task.cancel()
            if self.ws:
                await self.ws.close()
        self.end_callback()
        logger.info("end VolcTTS")

    async def _sender(self):
        try:
            while (not self._exit.is_set()) and (not self.remote.is_playing()):
                msg = await self.queue.get()
                await self.ws.send(self.protocol.marshal(msg))
                if msg.event == Event.EVENT_FINISH_SESSION:
                    break
        except asyncio.CancelledError:
            logger.info("sender task cancelled")

    async def _connect(self):
        conn_id = str(uuid.uuid4())
        log_id = _gen_log_id()
        url = f"wss://{HOST}/api/v3/tts/bidirection"

        headers = {
            "X-Tt-Logid": log_id,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-App-Key": APPID,
            "X-Api-Access-Key": SK,
            "X-Api-Connect-Id": conn_id,
        }

        self.ws = await websockets.connect(url, additional_headers=headers)
        logger.info("WebSocket connection established")

        # Send start connection message
        start_conn_msg = Message(MsgType.FULL_CLIENT)
        start_conn_msg.event = Event.EVENT_START_CONNECTION
        start_conn_msg.payload = b"{}"
        await self.ws.send(self.protocol.marshal(start_conn_msg))

        # # Wait for connection started response
        response = await self.ws.recv()
        msg = self.protocol.unmarshal(response)
        if msg.event != Event.EVENT_CONNECTION_STARTED:
            raise RuntimeError(f"Unexpected response event: {msg.event}")

    async def _start_tts_session(self):
        params = {
            "speaker": self.get_tts_speaker(),
            "audio_params": self.audio_params,
            "additions": json.dumps(self.additions),
        }
        req = {
            "event": Event.EVENT_START_SESSION,
            "namespace": NAMESPACE,
            "req_params": params,
        }

        msg = Message(MsgType.FULL_CLIENT)
        msg.event = Event.EVENT_START_SESSION
        msg.session_id = self.session_id
        msg.payload = json.dumps(req)

        await self.ws.send(self.protocol.marshal(msg))

        # Wait for session started response
        response = await self.ws.recv()
        msg = self.protocol.unmarshal(response)
        if msg.event != Event.EVENT_SESSION_STARTED:
            raise RuntimeError(f"Unexpected response event: {msg.event}")

        logger.info("TTS session started")
