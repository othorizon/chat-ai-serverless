import logging

from agora.rtc.rtc_connection_observer import IRTCConnectionObserver

logger = logging.getLogger(__name__)

# pylint: disable=logging-fstring-interpolation,line-too-long
class ExampleConnectionObserver(IRTCConnectionObserver):
    def __init__(self, user_join, user_left):
        super(ExampleConnectionObserver, self).__init__()
        self.user_join = user_join
        self.user_left = user_left

    def on_connected(self, agora_rtc_conn, conn_info, reason):
        logger.info(
            f"on_connected, agora_rtc_conn={agora_rtc_conn}, local_user_id={conn_info.local_user_id}, state={conn_info.state}, internal_uid={conn_info.internal_uid} ,reason={reason}"
        )

    def on_disconnected(self, agora_rtc_conn, conn_info, reason):
        logger.info(
            f"on_disconnected, agora_rtc_conn={agora_rtc_conn}, local_user_id={conn_info.local_user_id}, state={conn_info.state}, internal_uid={conn_info.internal_uid} ,reason={reason}"
        )

    def on_connecting(self, agora_rtc_conn, conn_info, reason):
        logger.info(
            f"on_connecting, agora_rtc_conn={agora_rtc_conn}, local_user_id={conn_info.local_user_id}, state={conn_info.state}, internal_uid={conn_info.internal_uid} ,reason={reason}"
        )

    def on_user_joined(self, agora_rtc_conn, user_id):
        logger.info(
            f"on_user_joined, agora_rtc_conn={agora_rtc_conn}, user_id={user_id}"
        )
        self.user_join(user_id)

    def on_user_left(self, agora_rtc_conn, user_id, reason):
        logger.info(f"on_user_left, agora_rtc_conn={agora_rtc_conn}, user_id={user_id}")
        self.user_left()
