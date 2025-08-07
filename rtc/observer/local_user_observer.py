#!env python
from agora.rtc.local_user_observer import IRTCLocalUserObserver
import logging
logger = logging.getLogger(__name__)


# https://doc.shengwang.cn/api-ref/rtc-server-sdk/cpp/classagora_1_1rtc_1_1_i_local_user_observer.html
class ExampleLocalUserObserver(IRTCLocalUserObserver):
    def __init__(self,state_changed_cb):
        self.state_changed_cb=state_changed_cb
        super(ExampleLocalUserObserver, self).__init__()

    def on_stream_message(self, local_user, user_id, stream_id, data, length):
        logger.info(f"on_stream_message, user_id={user_id}, stream_id={stream_id}, data={data}, length={length}")
        return 0

    def on_user_info_updated(self, local_user, user_id, msg, val):
        # https://doc.shengwang.cn/api-ref/rtc-server-sdk/python/python-api/localuserobserver#onuserinfoupdated
        logger.info(f"on_user_info_updated, user_id={user_id}, msg={msg}, val={val}")
        return 0
    # https://doc.shengwang.cn/api-ref/rtc-server-sdk/cpp/classagora_1_1rtc_1_1_i_local_user_observer.html#onUserAudioTrackStateChanged()
    # 状态：https://doc.shengwang.cn/api-ref/rtc-server-sdk/cpp/namespaceagora_1_1rtc#aa83ef274510d1063e840d6104fd12516
    def on_user_audio_track_state_changed(self, agora_local_user, user_id, agora_remote_audio_track, state, reason, elapsed):
        logger.info(f"on_user_audio_track_state_changed, user_id={user_id},  state={state},reason={reason}")
        self.state_changed_cb(user_id,state, reason)
        return 1
