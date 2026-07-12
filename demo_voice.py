# -*- coding: utf-8 -*-
"""demo 语音教练：元気な挨拶 + 逐节コツ报幕 + 视觉互动接口预留。

台词与触发时机（参考 语音功能skill.txt）：
    挨拶       通し开始时（与挥手/胸前礼动作同步，14s窗口）
    各节コツ   在上一节结束（=节间缓冲开始）时开口，节名短句落在缓冲+节首拍内，
               コツ长句自然压在动作上（NHK原版旁白也是边做边说）
    节拍报数   默认关闭（语音通道留给未来的视觉互动提示）；$env:DEMO_COUNT=1
               可开启②~⑤逐拍报数（忙则跳过不排队，不漂移）
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
import json
import os
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import demo_viewer

_DIR = Path(__file__).resolve().parent
VISION_CLOCK = _DIR / "vision_clock.json"    # 播放时钟广播给视觉进程
OBS_PATH = _DIR / "observations.json"        # 视觉进程写的观察结果
REPORT_PATH = _DIR / "session_report.json"   # 视觉进程写的班后汇报数据

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
CLOSING_LINE = ("これで、ラジオ体操を終わります。お疲れ様でした！"
                "みんな、ありがとう！また一緒に踊りましょう！")

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

# ---- 日语节拍报数（②~⑤；①不加）——默认关闭，把语音通道留给互动提示 ----
# 需要时 $env:DEMO_COUNT=1 开启。机制：每拍一个提示点，TTS忙则该拍跳过不排队，
# コツ一结束从当前正确拍接上，不漂移。
COUNTS = ["一", "二", "サン", "し", "ゴ", "ろく", "しち", "はち"]
COUNT_SECTIONS = {"2_ude", "3_udemawashi", "4_mune", "5_yokomage"}
COUNTING_ON = os.environ.get("DEMO_COUNT", "0") == "1"


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

        # 视觉互动播报窗 {motion_key: [(节key, 窗开始秒, 窗结束秒), ...]}
        # 每节第9~14拍(コツ长句已落,报数默认关):读 observations 播报,
        # 忙则顺延到下一拍,窗结束还没播出去就放弃本节(不漂移不堆积)。
        # 评哪几节由 vision_rules.SECTIONS 决定(当前③④)。
        try:
            import vision_rules
            eval_keys = tuple(vision_rules.SECTIONS)
        except ImportError:
            eval_keys = ("3_udemawashi", "4_mune")
        self._fb_windows = {"0_demo_full": []}
        for key, _name, start, _end in timeline:
            if key in eval_keys:
                self._fb_windows["0_demo_full"].append(
                    (key, start + 9 * beat, start + 14 * beat))
        for key in eval_keys:   # 单节播放:拍号网格有1拍起手偏移
            self._fb_windows[key] = [(key, 10 * beat, 15 * beat)]

        self._fired = set()      # {(motion_key, 触发秒, 类型)}
        self._key = None
        self._last_t = 0.0
        self._clock_wall = 0.0   # 上次写时钟文件的墙钟

    def prefetch_all(self):
        """预热全部台词+报数词+互动台词的TTS合成（后台进行），到点零等待。"""
        texts = {text for cue in self._cues.values() for _t, text in cue}
        texts |= set(COUNTS) if COUNTING_ON else set()
        try:
            import vision_rules
            texts |= set(vision_rules.all_feedback_texts())
        except ImportError:
            pass
        _tts_prefetch(sorted(texts))

    def add_alias(self, src_key, alias_key):
        """让另一个动作名共享同一张提示点表（如真机预设 7_demo_full=通し）。"""
        self._cues[alias_key] = self._cues[src_key]
        if src_key in self._count_cues:
            self._count_cues[alias_key] = self._count_cues[src_key]
        if src_key in self._fb_windows:
            self._fb_windows[alias_key] = self._fb_windows[src_key]

    def _broadcast_clock(self, key, t):
        """把播放时钟广播给视觉进程(节流0.2s,原子替换)。"""
        now = time.time()
        if now - self._clock_wall < 0.2:
            return
        self._clock_wall = now
        try:
            tmp = VISION_CLOCK.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "t": round(t, 3),
                                       "wall": now}), "utf-8")
            os.replace(tmp, VISION_CLOCK)
        except OSError:
            pass                      # 时钟写不出去不影响演示本体

    def _try_feedback(self, key, t):
        """在本节互动窗内尝试播报视觉观察结果(每节最多1句)。"""
        for sec, ws, we in self._fb_windows.get(key, ()):
            # 标记第二项必须是数字(与line/count标记同构):回绕清理用 c[1]<t
            marker = (key, ws, f"fb_{sec}")
            if not (ws <= t <= we) or marker in self._fired:
                continue
            if _tts_busy():
                return                # 忙则顺延到下一帧再试
            try:
                data = json.loads(OBS_PATH.read_text("utf-8"))
            except (OSError, ValueError):
                return                # 还没有结果:窗内继续等
            if data.get("section") != sec or \
                    time.time() - data.get("wall", 0) > 30:
                return                # 旧节/陈旧数据不播
            self._fired.add(marker)
            self.coach_feedback(data.get("observations"))

    def on_tick(self, key, t):
        """sim_viewer 每帧回调：到点未播则播；报数忙则跳过。"""
        self._broadcast_clock(key, t)
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
                if text == CLOSING_LINE:     # 感谢语之后:班后汇报(排队顺播)
                    self._speak_report()
        for ct, word in self._count_cues.get(key, ()):
            if ct <= t and (key, ct, "count") not in self._fired:
                self._fired.add((key, ct, "count"))   # 过点即标记：跳过不补
                if not _tts_busy() and t - ct < 0.4:  # 已过大半拍的也不追
                    print(f"  ♪ {word}")
                    _tts_speak(word)
        self._try_feedback(key, t)

    def _speak_report(self):
        """班后汇报:人数、需要关照的人、已存档。数据不新鲜/没有就整段跳过。"""
        try:
            import vision_rules as vr
            data = json.loads(REPORT_PATH.read_text("utf-8"))
        except (ImportError, OSError, ValueError):
            return
        if time.time() - data.get("wall", 0) > 600:   # 只播本场的
            return
        say(vr.REPORT_END)
        participants = data.get("participants") or []
        if participants:
            say(vr.report_count_line(len(participants)))
        for name in data.get("small") or []:
            say(f"{name}{vr.REPORT_CALLOUT}")
        if data.get("record_file"):
            say(vr.REPORT_SAVED)

    # ---- 视觉互动接口（vision_coach.py 经 observations.json 喂入） ----
    def coach_feedback(self, observations):
        """observations=[{"name":..., "advice":...} 或 {"text":...}, ...]。

        列表为空/None 时不触发任何语音（=没识别到人就不出声）。
        """
        for obs in observations or []:
            if "text" in obs:
                say(obs["text"])
            else:
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
