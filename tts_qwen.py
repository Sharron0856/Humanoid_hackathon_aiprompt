# -*- coding: utf-8 -*-
"""
千问语音合成（Qwen3-TTS-Flash，阿里云国际版）→ Windows 本地播放。

给 ai_viewer 提供"喊操员"旁白：动作开始报动作名、广播体操逐节报节名。
- speak(text)：异步排队播报，不卡渲染循环；同一文本合成结果按内容缓存，
  第二次播放不再调 API。
- prefetch(texts)：后台预热一批文本（如13节节名），播放时零等待。
- set_enabled(flag)：静音开关。

播放实现：wav 用内置 winsound；其他格式（mp3等）走 winmm MCI，都无需额外依赖。
"""
import ctypes
import hashlib
import os
import queue
import tempfile
import threading
import urllib.request

import dashscope

from llm_motion import API_KEY

dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"   # 国际版
TTS_MODEL = os.environ.get("QWEN_TTS_MODEL", "qwen3-tts-flash")   # 千问最新TTS
VOICE = os.environ.get("QWEN_TTS_VOICE", "Cherry")
LANGUAGE_TYPE = os.environ.get("QWEN_TTS_LANGUAGE", "Japanese")

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "g1_tts_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

_q = queue.Queue()
_enabled = True
_lock = threading.Lock()
_worker = None


def _api_key():
    return os.environ.get("DASHSCOPE_API_KEY", "") or API_KEY


def synth(text):
    """文本 → 本地音频文件路径（按内容缓存）。失败抛 RuntimeError。"""
    path = os.path.join(
        _CACHE_DIR,
        hashlib.md5(
            f"{TTS_MODEL}|{VOICE}|{LANGUAGE_TYPE}|{text}".encode("utf-8")
        ).hexdigest() + ".audio")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    resp = dashscope.MultiModalConversation.call(
        model=TTS_MODEL, api_key=_api_key(), text=text,
        voice=VOICE, language_type=LANGUAGE_TYPE, stream=False)
    if getattr(resp, "status_code", None) != 200:
        raise RuntimeError(f"TTS调用失败: {getattr(resp, 'code', '')} "
                           f"{getattr(resp, 'message', resp)}")
    url = resp.output.audio["url"]
    urllib.request.urlretrieve(url, path)
    return path


def _play_blocking(path):
    with open(path, "rb") as f:
        is_wav = f.read(4) == b"RIFF"
    if is_wav:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
    else:   # mp3 等其他格式走 MCI
        alias = f"g1tts{abs(hash(path)) % 100000}"
        mci = ctypes.windll.winmm.mciSendStringW
        mci(f'open "{path}" alias {alias}', None, 0, None)
        try:
            mci(f"play {alias} wait", None, 0, None)
        finally:
            mci(f"close {alias}", None, 0, None)


def _run():
    while True:
        text = _q.get()
        if not _enabled:
            continue
        try:
            _play_blocking(synth(text))
        except Exception as e:
            print(f"（语音播报失败，已跳过: {e}）")


def speak(text):
    """异步播报。多条按顺序排队，不打断也不重叠。"""
    global _worker
    if not _enabled or not text:
        return
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_run, daemon=True)
            _worker.start()
    _q.put(str(text).strip())


def prefetch(texts):
    """后台预合成一批文本（只缓存不播放），播放时零延迟。"""
    def job():
        for t in texts:
            try:
                synth(str(t).strip())
            except Exception:
                pass
    threading.Thread(target=job, daemon=True).start()


def set_enabled(flag):
    global _enabled
    _enabled = bool(flag)
    if not flag:
        while not _q.empty():   # 清空未播的排队项
            try:
                _q.get_nowait()
            except queue.Empty:
                break
    return _enabled


def is_enabled():
    return _enabled
