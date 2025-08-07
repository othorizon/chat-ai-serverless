import os
import logging

from config import WRITE_TTS_FILE

logger = logging.getLogger(__name__)

OUTPUT_DIR = "temp/tts_output"

class AudioFile:
    def __init__(self, filename: str):
        self.write_tts_file = WRITE_TTS_FILE
        if self.write_tts_file:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            self.filename = f"{filename}.pcm"
            self.filepath = os.path.join(OUTPUT_DIR, self.filename)
            self.file = open(self.filepath, "wb")
            logger.info("create audio file, name: %s", self.filepath)
        

    def write(self, data: bytes):
        if self.write_tts_file:
            self.file.write(data)

    def close(self):
        if self.write_tts_file:
            self.file.close()
            logger.info("Audio saved to: %s", self.filepath)
