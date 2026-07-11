# -*- coding: utf-8 -*-
"""demo 语音教练：元気な挨拶 + 逐节コツ报幕 + 视觉互动接口预留。

台词与触发时机（参考 语音功能skill.txt）：
    挨拶       通し开始时（与挥手/胸前礼动作同步，14s窗口）
    各节コツ   在上一节结束（=节间缓冲开始）时开口，节名短句落在缓冲+节首拍内，
               コツ长句自然压在动作上（NHK原版旁白也是边做边说）
    节拍报数   ②~⑤コツ说完后逐拍数「いち、に、さん、し…」（每拍一个提示点，
               TTS忙则该拍跳过不排队，コツ结束即从当前正确拍接上，永不漂移；
               ①不报数，DEMO_COUNT=0 可整体关闭）
    结尾       ⑤收尾站姿定格时

同步机制：VoiceCoach.on_tick(motion_key, sim_t) 挂进 sim_viewer 的每帧回调，
提示点按播放时钟触发——暂停不抢跑、快退/重播会重新武装提示点、切动作即重置。
真机联调时在执行循环里以同一协议接入即可。

TTS：走 tts_qwen（异步队列+按文本缓存+预取，不卡渲染）。缺 dashscope 依赖或
API key 时自动降级为控制台字幕模式，方便无网排练和核对时机。

视觉互动（预留，未来接摄像头识别）：
    coach.coach_feedback([{"name": "A", "advice": "もう少し腕を上げましょう"}])
    观察列表为空时不触发任何语音（=没识别到人就不出声）。

用法：$env:PYTHONUTF8=1; python demo_voice.py
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import demo_viewer

# ---- TTS 优雅降级：没依赖/没key就用控制台字幕 ----
try:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("未设置 DASHSCOPE_API_KEY")
    from tts_qwen import (is_busy as _tts_busy, prefetch as _tts_prefetch,
                          speak as _tts_speak)
    TTS_READY = True
except Exception as _e:  # noqa: N816
    TTS_READY = False
    _TTS_WHY = str(_e)

    def _tts_speak(text):
        pass

    def _tts_prefetch(texts):
        pass

    def _tts_busy():
        return False


def say(text):
    """播报一句（异步不阻塞）；同时在控制台打字幕，方便核对时机。"""
    print(f"🔊 {text}")
    _tts_speak(text)


# ---- 台词表 ----
GREETING_LINE = ("みなさん、こんにちは！ラジオ体操の時間です！"
                 "今日も元気に、いっしょに体を動かしましょう！")
CLOSING_LINE = "お疲れ様でした！この調子で、今日も一日、頑張りましょう！"

KOTSU = {
    "1_senobi": ("背伸びの運動！腕をよく伸ばして、ゆっくり高く上げ、"
                 "背すじを伸ばしましょう。"),
    "2_ude": ("腕を振って脚を曲げ伸ばす運動！かかとの上下運動は、"
              "腕の振りに合わせてリズミカルに行いましょう。"),
    "3_udemawashi": ("腕を回す運動！腕や肩の力を抜き、"
                     "遠心力を使って大きく回しましょう。"),
    "4_mune": "胸を反らす運動！深い呼吸を心がけ、顔が上を向きすぎないように。",
    "5_yokomage": ("体を横に曲げる運動！前かがみにならないように、"
                   "腕は真横から上げましょう。"),
}

# 视觉互动模板（示例，未来由识别结果填充；参考 语音功能skill.txt）
FEEDBACK_EXAMPLES = [
    "もう少し腕を上げましょう",
    "呼吸をより落ち着いて",
    "いい感じですね！頑張りましょう",
]

# ---- 日语节拍报数（②~⑤；①的コツ是"ゆっくり"，报数会催，不加）----
# 逐拍触发+忙则跳过：每拍一个提示点，触发时TTS若正忙（コツ未说完/上个数字
# 未播完）则该拍直接跳过——コツ一结束，报数从当前拍的正确数字接上，永不漂移。
# 可用环境变量 DEMO_COUNT=0 整体关闭（如真机扬声器逐字RPC延迟过大时）。
COUNTS = ["いち", "に", "さん", "し", "ご", "ろく", "しち", "はち"]
COUNT_SECTIONS = {"2_ude", "3_udemawashi", "4_mune", "5_yokomage"}
COUNTING_ON = os.environ.get("DEMO_COUNT", "1") != "0"


class VoiceCoach:
    """按播放时钟触发台词；支持暂停/快退/重播/切动作。"""

    def __init__(self):
        # 台词提示表 {motion_key: [(触发秒, 台词), ...]}（串行排队，保证顺序）
        timeline = demo_viewer.section_timeline()
        cues = [(0.3, GREETING_LINE)]
        for key, _name, start, _end in timeline:
            if key in KOTSU:
                # 上一节结束=缓冲开始时开口；①前是挨拶收尾，同样提前一个缓冲量
                cues.append((max(0.0, start - demo_viewer.SECTION_GAP_S),
                             KOTSU[key]))
        cues.append((timeline[-1][3] - 0.1, CLOSING_LINE))  # 播放停在末姿态时可触发
        self._cues = {"0_demo_full": sorted(cues)}
        for key, text in KOTSU.items():
            self._cues[key] = [(0.1, text)]
        self._cues["6_aisatsu"] = [(0.3, GREETING_LINE)]

        # 报数提示表（逐拍；忙则跳过不排队）
        beat = demo_viewer.BEAT
        self._count_cues = {"0_demo_full": []}
        if COUNTING_ON:
            for key, _name, start, _end in timeline:
                if key not in COUNT_SECTIONS:
                    continue
                self._count_cues["0_demo_full"] += [
                    (start + i * beat, COUNTS[i % 8]) for i in range(16)]
                # 单节播放：拍号网格整体后移1拍起手
                self._count_cues[key] = [
                    ((i + 1) * beat, COUNTS[i % 8]) for i in range(16)]
            self._count_cues["0_demo_full"].sort()

        self._fired = set()      # {(motion_key, 触发秒, 类型)}
        self._key = None
        self._last_t = 0.0

    def prefetch_all(self):
        """预热全部台词+报数词的TTS合成（后台进行），到点播放零等待。"""
        texts = {text for cue in self._cues.values() for _t, text in cue}
        texts |= set(COUNTS) if COUNTING_ON else set()
        _tts_prefetch(sorted(texts))

    def add_alias(self, src_key, alias_key):
        """让另一个动作名共享同一张提示点表（如真机预设 7_demo_full=通し）。"""
        self._cues[alias_key] = self._cues[src_key]
        if src_key in self._count_cues:
            self._count_cues[alias_key] = self._count_cues[src_key]

    def on_tick(self, key, t):
        """sim_viewer 每帧回调：到点未播则播；报数忙则跳过。"""
        if key != self._key:
            self._key = key      # 切动作：该动作的提示点全部重新武装
            self._fired = {c for c in self._fired if c[0] != key}
        elif t + 0.5 < self._last_t:   # 快退/重播：回跳点之后的提示点重新武装
            self._fired = {c for c in self._fired if c[0] != key or c[1] < t}
        self._last_t = t
        for ct, text in self._cues.get(key, ()):
            if ct <= t and (key, ct, "line") not in self._fired:
                self._fired.add((key, ct, "line"))
                say(text)
        for ct, word in self._count_cues.get(key, ()):
            if ct <= t and (key, ct, "count") not in self._fired:
                self._fired.add((key, ct, "count"))   # 过点即标记：跳过不补
                if not _tts_busy() and t - ct < 0.4:  # 已过大半拍的也不追
                    print(f"  ♪ {word}")
                    _tts_speak(word)

    # ---- 视觉互动接口（预留） ----
    def coach_feedback(self, observations):
        """未来接视觉识别：observations=[{"name":..., "advice":...}, ...]。

        列表为空/None 时不触发任何语音（=没识别到人就不出声）。
        """
        for obs in observations or []:
            say(f"{obs['name']}さん、{obs['advice']}")


def main():
    demo_viewer.install()
    coach = VoiceCoach()
    if TTS_READY:
        print("TTS就绪（Qwen3-TTS-Flash），正在后台预热台词……")
        coach.prefetch_all()
    else:
        print(f"⚠ TTS不可用（{_TTS_WHY}），本次以控制台字幕模式运行。")
    import sim_viewer
    sim_viewer.main(on_tick=coach.on_tick)


if __name__ == "__main__":
    main()
