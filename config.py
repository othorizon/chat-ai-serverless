import os
import logging
import tempfile
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

ASSETS_DIR = "/oss/assets"

# 服务端RTC token 最大有效期 单位秒
# 服务端token未做刷新机制，可视为服务端最大存活时间的限制
# serveless中也配置了函数执行超时时间，应该要小于token 过期这个时间
RTC_TOKEN_MAX_KEEPLIVE_TIME = 60 * 60 * 3 + 600  # 3小时+10分钟

# 码率 = 采样率 × 位数 × 声道数
# 每秒字节数 = 码率÷ 8

# 16位的字节数,即每一个采样点的位数，默认使用这个
AUDIO_BITS = 16

# tts 输出位 32 位 float 的 pcm时使用这个
# AUDIO_BITS = 32

# SAMPLE_RATE = 44100
# SAMPLE_RATE = 16000
# SAMPLE_RATE = 48000
# SAMPLE_RATE = 8000

# 采集远端的采样率，由 asr服务决定
REMOTE_AUDIO_SAMPLE_RATE = 16000

TTS_ENGINE = "volcengine"
# OUTPUT_SAMPLE_RATE = 48000
#OUTPUT_SAMPLE_RATE = 8000
# OUTPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# 是否写入tts文件
WRITE_TTS_FILE = False

# TTS_ENGINE = "fishaudio"
# OUTPUT_SAMPLE_RATE = 44100
FISH_AUDIO_MODEL_ID = os.getenv("FISH_AUDIO_MODEL_ID")

# TTS_ENGINE = "SiliconFlowTts"
# OUTPUT_SAMPLE_RATE = 44100
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_MODEL_ID = os.getenv("SILICONFLOW_MODEL_ID", "fishaudio/fish-speech-1.4")
SILICONFLOW_VOICE = os.getenv("SILICONFLOW_VOICE", "fishaudio/fish-speech-1.4:diana")

# TTS_ENGINE = "SelfFishAudioTts"
# OUTPUT_SAMPLE_RATE = 44100

# https://www.volcengine.com/docs/6561/1329505
VOLC_AUDIO_PARAMS = {
    "format": "pcm",
    "sample_rate": OUTPUT_SAMPLE_RATE,
    "speech_rate": 0,
}
# max_length_to_filter_parenthesis过滤括号内的内容
VOLC_ADDTIONS_PARAMS = {
    "max_length_to_filter_parenthesis": 100,
    "disable_markdown_filter": True,
}
DEFAULT_VOLC_TTS_SPEAKER = "zh_female_yingyujiaoyu_mars_bigtts"

VOLC_APP_ID=os.getenv("VOLC_APP_ID")
VOLC_SK=os.getenv("VOLC_SK")
VOLC_HOST=os.getenv("VOLC_HOST")
VOLC_RESOURCE_ID=os.getenv("VOLC_RESOURCE_ID")

# mqtt

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
# 使用文件内容而不是文件路径
MQTT_CA_CERTS_CONTENT = os.getenv("MQTT_CA_CERTS_CONTENT", "")


def get_mqtt_ca_certs_filepath(file_content: str):

    if file_content:
        file_content = file_content.replace("[]", os.linesep)
        # 保存为临时文件
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(file_content.encode())
            logger.info(f"write ca_path: {f.name}")
            return f.name
    return None


# rtc

AGORA_APP_ID = os.getenv("AGORA_APP_ID")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE")

# mode

GLOBAL_CONFIG = {
    "IS_MIC_PRESS_MODE": True
}

# asr
XUNFEI_APP_ID = os.getenv("XUNFEI_APP_ID")
XUNFEI_API_KEY = os.getenv("XUNFEI_API_KEY")

# llm
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL")