import asyncio
import logging
import os
import threading
import uuid

from agora.rtc.agora_base import ClientRoleType, ChannelProfileType, AudioScenarioType ,AudioEncoderConfiguration, AudioProfileType
from agora.rtc.agora_service import AgoraService, AgoraServiceConfig, RTCConnConfig
from agora.rtc.voice_detection import AudioVadConfigV2

from config import (
    AGORA_APP_ID,
    AGORA_APP_CERTIFICATE,
    REMOTE_AUDIO_SAMPLE_RATE,
    RTC_TOKEN_MAX_KEEPLIVE_TIME,
)
from rtc.common.path_utils import get_log_path_with_filename
from rtc.common.RtcTokenBuilder2 import Role_Publisher, RtcTokenBuilder
from rtc.observer.audio_frame_observer import ExampleAudioFrameObserver
from rtc.observer.connection_observer import ExampleConnectionObserver
from rtc.observer.local_user_observer import ExampleLocalUserObserver


logger = logging.getLogger(__name__)


class RemoteState:
    def __init__(self):
        # 正在接收录音
        self.remote_playing_req_id = None
        # 正在发送llm
        self.is_sending = False

    def start_playing(self):
        self.remote_playing_req_id = str(uuid.uuid4())

    def current_playing_req_id(self):
        return self.remote_playing_req_id

    def end_playing(self):
        self.remote_playing_req_id = None

    def is_playing(self):
        return self.remote_playing_req_id is not None

    def is_playing_req_id(self, req_id):
        return self.remote_playing_req_id == req_id


class RtcConfig:
    def __init__(self):
        self.app_id = None
        self.token = None
        self.channel_id = None
        self.connection_number: int = 1
        self.user_id = "9999"  # 固定服务端 uid
        self.low_delay = False
        self.video_file = None
        self.remote_sample_rate: int = None
        self.num_of_channels: int = None
        self.fps = None
        self.width = None
        self.height = None
        self.bitrate = None
        self.save_to_disk = 1
        self.hours = 0
        self.mode = 1
        self.value = 0


class RTCBaseProcess:
    def __init__(
        self, device_id: str, remote_sample_rate: int, enable_vad: bool
    ):
        """
        enable_vad: 是否开启vad，开启后，asr 将只在vad 检测到之后才启动。这一般不会影响 asr 速度，因为 asr 识别速度是大于人说话的速度的，只要提前把音频保存，asr 启动后再进行识别是没问题的。
        remote_sample_rate: 远端采样率
        device_id: 设备id
        """
        self.remote = RemoteState()
        self.device_id = device_id
        self._exit = asyncio.Event()
        self.enable_vad = enable_vad
        self._conn_config = RTCConnConfig(
            client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
            channel_profile=ChannelProfileType.CHANNEL_PROFILE_COMMUNICATION,
        )
        # 订阅远程的麦克风
        self._conn_config.auto_subscribe_audio = True
        self._serv_config = AgoraServiceConfig()
        self._serv_config.audio_scenario = AudioScenarioType.AUDIO_SCENARIO_CHATROOM
        self.agora_service = AgoraService()
        # 初始化配置
        self.token = None
        self.set_serv_config()
        self.agora_service.initialize(self._serv_config)

        self.config.remote_sample_rate = remote_sample_rate or REMOTE_AUDIO_SAMPLE_RATE
        # 创建连接
        try:
            self.create_connections()
        except Exception as err:
            logger.error("connection err %s", err)
            self.release()
            raise err

    def create_connections(self):
        # ---------------2. Create Connection
        logger.info(
            "connect_and_release: %s, auto_subscribe_audio: %s",
            self._conn_config.auto_subscribe_video,
            self._conn_config.auto_subscribe_audio,
        )
        self.connection = self.agora_service.create_rtc_connection(self._conn_config)

        conn_observer = ExampleConnectionObserver(self.on_user_join, self.on_user_left)
        self.connection.register_observer(conn_observer)
        self.connection.connect(
            self.config.token, self.config.channel_id, self.config.user_id
        )

        self.local_user = self.connection.get_local_user()
        
        #音频设置
        # https://doc.shengwang.cn/api-ref/rtc-server-sdk/python/python-api/apidatatype#audioprofiletype
        # self.local_user.set_audio_encoder_configuration(AudioEncoderConfiguration(
        #     audioProfile=AudioProfileType.AUDIO_PROFILE_IOT
        # ))
        # https://doc.shengwang.cn/api-ref/rtc-server-sdk/python/python-api/apidatatype#audioscenariotype
        # self.local_user.set_audio_scenario(AudioScenarioType.AUDIO_SCENARIO_DEFAULT)
        # self.local_user.set_audio_scenario(AudioScenarioType.AUDIO_SCENARIO_GAME_STREAMING)
        self.local_user.set_audio_scenario(AudioScenarioType.AUDIO_SCENARIO_CHATROOM)

        local_user_observer = ExampleLocalUserObserver(
            state_changed_cb=self.on_user_audio_track_state_changed
        )
        self.local_user.register_local_user_observer(local_user_observer)

        # 监听远程音频
        self.local_user.set_playback_audio_frame_before_mixing_parameters(
            1, self.config.remote_sample_rate
        )
        audio_frame_observer = ExampleAudioFrameObserver(
            _on_playback_audio_frame_before_mixing=self.on_playback_audio_frame_before_mixing
        )

        if self.enable_vad:
            logger.info("---- enable vad")

            # Recommended  configurations:
            # For not-so-noisy environments, use this configuration: (16, 30, 50, 0.7, 0.5, 70, 70, -50)
            # For noisy environments, use this configuration: (16, 30, 50, 0.7, 0.5, 70, 70, -40)
            # For high-noise environments, use this configuration: (16, 30, 50, 0.7, 0.5, 70, 70, -30)

            vad_configure = AudioVadConfigV2(16, 30, 50, 0.7, 0.5, 70, 70, -40)
            ret = self.local_user.register_audio_frame_observer(
                audio_frame_observer, 1, vad_configure
            )
        else:
            ret = self.local_user.register_audio_frame_observer(
                audio_frame_observer, 0, None
            )

        # ret = self.local_user.register_audio_frame_observer(audio_frame_observer,0,None)
        if ret < 0:
            raise RuntimeError("register_audio_frame_observer failed")

        # 发布音频
        self.media_node_factory = self.agora_service.create_media_node_factory()
        self.pcm_data_sender = self.media_node_factory.create_audio_pcm_data_sender()
        self.audio_track = self.agora_service.create_custom_audio_track_pcm(
            self.pcm_data_sender
        )
        self.audio_track.set_enabled(1)
        self.local_user.publish_audio(self.audio_track)

    def on_user_audio_track_state_changed(self, user_id, state, reason):
        pass

    def on_playback_audio_frame_before_mixing(
        self,
        agora_local_user,
        channel_id,
        uid,
        frame,
        vad_result_state: int,
        vad_result_bytearray: bytearray,
    ):
        return 1

    def on_user_join(self, user_id: str):
        pass

    def on_user_left(self):
        pass

    def get_room_id(self):
        return self.device_id

    def release(self):
        logger.info("start rtc release")

        self.local_user.unpublish_audio(self.audio_track)
        if self.audio_track:
            self.audio_track.set_enabled(0)
        self.local_user.unregister_audio_frame_observer()
        self.local_user.unregister_local_user_observer()

        self.connection.disconnect()
        self.connection.unregister_observer()

        self.local_user.release()
        self.connection.release()
        logger.info("rtc connection release")

        if self.audio_track:
            self.audio_track.release()
        if self.pcm_data_sender:
            self.pcm_data_sender.release()
        if self.media_node_factory:
            self.media_node_factory.release()
        self.agora_service.release()
        logger.info("rtc release done")

    def set_serv_config(self):
        self.config = RtcConfig()
        self.config.num_of_channels = 1
        self.config.app_id = AGORA_APP_ID

        self.config.channel_id = self.get_room_id()
        logger.info("join room %s", self.config.channel_id)

        self.config.token = self.get_token()

        self._serv_config.appid = self.config.app_id
        self._serv_config.log_path = get_log_path_with_filename(
            self.config.channel_id, os.path.splitext(__file__)[0]
        )

    def handle_signal(self):
        self._exit.set()

    def get_token(self):
        if self.device_id is None:
            raise ValueError("deviceId is none")
        # Token 的有效时间，单位秒
        token_expiration_in_seconds = RTC_TOKEN_MAX_KEEPLIVE_TIME
        # 所有的权限的有效时间，单位秒，声网建议你将该参数和 Token 的有效时间设为一致
        privilege_expiration_in_seconds = RTC_TOKEN_MAX_KEEPLIVE_TIME
        # 生成 Token
        self.token = RtcTokenBuilder.build_token_with_uid(
            AGORA_APP_ID,
            AGORA_APP_CERTIFICATE,
            self.get_room_id(),
            "*",
            Role_Publisher,
            token_expiration_in_seconds,
            privilege_expiration_in_seconds,
        )
        return self.token
