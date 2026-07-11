# -*- coding: utf-8 -*-
"""G1 扬声器音频后端（路线B）：Qwen TTS 的 wav → 16kHz 单声道 PCM → AudioClient.PlayStream。

语音链路的调度/台词/缓存完全不变，只替换 tts_qwen 的"播放出口"：
    speaker = RobotSpeaker()          # 必须在 DDS ChannelFactoryInitialize 之后创建
    tts_qwen.set_robot_sink(speaker)  # 此后 speak() 的声音从 G1 胸前扬声器播出

音频走 G1 的独立 RPC 服务，与 rt/arm_sdk 动作链路互不冲突，可以边做操边说话。
现场若 PlayStream 不可用（固件差异等），tts_qwen 会自动回退到本机扬声器。
仅依赖 .venv-real（Python 3.10：audioop 仍在标准库中；unitree_sdk2py 已装）。
"""
import os
import time
import uuid
import wave

TARGET_RATE = 16000     # G1 音频服务要求：16kHz / 16bit / 单声道 PCM
CHUNK_BYTES = 96000     # 每次 PlayStream 的 PCM 字节数（=3秒），现场可按固件表现调整
APP_NAME = "g1_taiso"


def wav_to_pcm16k(path):
    """wav 文件 → (16kHz mono s16le PCM bytes, 时长秒)。非 wav 抛 ValueError。"""
    import audioop  # Python≤3.12 标准库；.venv-real 为 3.10
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sampwidth = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    if sampwidth != 2:
        frames = audioop.lin2lin(frames, sampwidth, 2)
    if nch == 2:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    elif nch != 1:
        raise ValueError(f"不支持的声道数: {nch}")
    if rate != TARGET_RATE:
        frames, _ = audioop.ratecv(frames, 2, 1, rate, TARGET_RATE, None)
    return frames, len(frames) / (TARGET_RATE * 2)


class RobotSpeaker:
    """G1 内置扬声器播放器（unitree AudioClient 封装）。"""

    def __init__(self, volume=None):
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
        self._client = AudioClient()
        self._client.SetTimeout(10.0)
        self._client.Init()
        if volume is None:
            volume = os.environ.get("G1_SPEAKER_VOLUME")   # 0~100，可选
        if volume is not None:
            code = self._client.SetVolume(int(volume))
            if code != 0:
                print(f"（G1音量设置失败 code={code}，沿用当前音量）")

    def play_file(self, path):
        """播放本地音频文件；阻塞到预计播完，保证多条台词顺序不重叠。"""
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                raise ValueError("非wav格式，机器人端无法解码")
        pcm, duration = wav_to_pcm16k(path)
        stream_id = uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        for i in range(0, len(pcm), CHUNK_BYTES):
            ret = self._client.PlayStream(APP_NAME, stream_id,
                                          pcm[i:i + CHUNK_BYTES])
            code = ret[0] if isinstance(ret, tuple) else ret
            if code != 0:
                raise RuntimeError(f"PlayStream 失败 code={code}")
        # RPC 发送先于实际播放结束：补足剩余时长再返回
        remain = duration - (time.monotonic() - t0)
        if remain > 0:
            time.sleep(remain)

    def stop(self):
        """立即停止机器人端当前播放（急停语音时用）。"""
        try:
            self._client.PlayStop(APP_NAME)
        except Exception:
            pass
