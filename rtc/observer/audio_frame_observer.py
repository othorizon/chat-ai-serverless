#!env python
import datetime
import logging
import os

from agora.rtc.audio_frame_observer import AudioFrame, IAudioFrameObserver

logger = logging.getLogger(__name__)


source_dir = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
filename, _ = os.path.splitext(os.path.basename(__file__))
log_folder = os.path.join(
    source_dir, "logs", filename, datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
)
os.makedirs(log_folder, exist_ok=True)


class ExampleAudioFrameObserver(IAudioFrameObserver):
    def __init__(self, _on_playback_audio_frame_before_mixing):
        super(ExampleAudioFrameObserver, self).__init__()
        self._on_playback_audio_frame_before_mixing = _on_playback_audio_frame_before_mixing

    # def on_get_playback_audio_frame_param(self, agora_local_user):
    #     audio_params_instance = AudioParams()
    #     return audio_params_instance

    def on_record_audio_frame(self, agora_local_user, channelId, frame):
        logger.info("on_record_audio_frame")
        return 0

    def on_playback_audio_frame(self, agora_local_user, channelId, frame):
        logger.info("on_playback_audio_frame")
        return 0

    def on_ear_monitoring_audio_frame(self, agora_local_user, frame):
        logger.info("on_ear_monitoring_audio_frame")
        return 0

    def on_playback_audio_frame_before_mixing(
        self, agora_local_user, channelId, uid, frame: AudioFrame, vad_result_state:int, vad_result_bytearray:bytearray
    ):
        # logger.info(f"on_playback_audio_frame_before_mixing")
        return self._on_playback_audio_frame_before_mixing(agora_local_user, channelId, uid, frame, vad_result_state, vad_result_bytearray)
        # logger.info(f"on_playback_audio_frame_before_mixing, channelId={channelId}, uid={uid}, type={audio_frame.type}, samples_per_sec={audio_frame.samples_per_sec}, samples_per_channel={audio_frame.samples_per_channel}, bytes_per_sample={audio_frame.bytes_per_sample}, channels={audio_frame.channels}, len={len(audio_frame.buffer)}")
        # logger.info(f"on_playback_audio_frame_before_mixing, file_path={file_path}, len={len(audio_frame.buffer)}")
        # logger.debug(f"on_playback_audio_frame_before_mixing,uid:{uid}")

        # logger.debug(
        #     f"on_playback_audio_frame_before_mixing, far_field_flag={frame.far_field_flag}, rms={frame.rms}, voice_prob={frame.voice_prob}, music_prob={frame.music_prob} ,pitch={frame.pitch}, len={len(frame.buffer)}"
        # )

    def on_get_audio_frame_position(self, agora_local_user):
        logger.info("on_get_audio_frame_position")
        return 0

    # def on_get_audio_frame_position(self, agora_local_user):
    #     logger.info("CCC on_get_audio_frame_position")
    #     return 0

    # def on_get_playback_audio_frame_param(self, agora_local_user):
    #     logger.info("CCC on_get_playback_audio_frame_param")
    #     return 0
    # def on_get_record_audio_frame_param(self, agora_local_user):
    #     logger.info("CCC on_get_record_audio_frame_param")
    #     return 0
    # def on_get_mixed_audio_frame_param(self, agora_local_user):
    #     logger.info("CCC on_get_mixed_audio_frame_param")
    #     return 0
    # def on_get_ear_monitoring_audio_frame_param(self, agora_local_user):
    #     logger.info("CCC on_get_ear_monitoring_audio_frame_param")
    #     return 0
