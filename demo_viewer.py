# -*- coding: utf-8 -*-
"""デモ用查看器：ラジオ体操第一 ①~⑤ 核心环节，节拍对齐版。

所有关键姿态都落在八拍节拍网格上：每节 = 2×8拍，通し = 前奏8拍 + 5节×16拍
（共88拍）。节奏由 BPM 控制，默认 77（接近NHK原版的呼间速度），可用环境变量
DEMO_BPM 覆盖，如：$env:DEMO_BPM=72

真机速度约束（arm_sdk 硬上限 45°/s）决定了编排取舍：
  - ②腕の運動：摆幅收小(前-60/后+20)，一个乐句摆2个来回（真人版4个）
  - ③腕を回す：一个乐句画1圈，且侧放位收窄（真人版每拍换位×4圈做不到）
  - ⑤横曲げ：举臂高度-135°（-150°在4拍内回位会超速），每侧弯1次
  当前编排在 BPM=77 时最大关节速度 43.3°/s；BPM 再快就会突破 45°/s 上限，
  真机会自动拉伸时间轴导致脱拍——想更快需进一步减小动作幅度。

数字键：1 = ①~⑤ 通し　2~6 = 各单节
其余按键同 sim_viewer.py：空格暂停 / ←→快退快进 / R重播 / L循环 / -=调速。
用法：$env:PYTHONUTF8=1; python demo_viewer.py
"""
import os

import motions
from motions import _arms

BPM = float(os.environ.get("DEMO_BPM", "77"))
BEAT = 60.0 / BPM          # 一拍的秒数
PRELUDE_BEATS = 8          # 通し开头的站姿前奏（音乐前奏一个八拍）
SECTION_BEATS = 16         # 每节 2×8拍

# ---- 姿态（在 motions.py 已验证姿态基础上按速度上限微调）----
_F = _arms(-90, 15, 85)                    # 前平举
_OH = _arms(-165, 25, 85)                  # 举过头顶（备用）
# ① 只动肩pitch：roll/肘在上下两姿态完全一致，纯直臂上举↔放下
_UP_HEAD = _arms(-150, 20, 85)             # 举起：直臂高举过头
_DOWN_V = _arms(-15, 20, 85)               # 放下：直臂近垂直下垂
_F2 = _arms(-90, 15, 85)                   # ② 前摆（前平举，直臂）
_B2 = _arms(35, 12, 75)                    # ② 后摆
_DN3 = _arms(-25, 15, 85)                  # ②③共用的下垂位（微前倾，给圈顶留限速余量）
# ③ 圈顶双手交叉：肩roll内收会压到头/躯干，所以交叉靠「肘弯40°+肩yaw内旋50°」
#    实现（模型网格搜索得到的无碰撞姿态：左手过中线7cm、右手9cm、高度头顶上方）。
#    分两步：先平行举到 _MID3（yaw/肘走一半），最后1拍收拢交叉成 _TOP_X。
_MID3 = {("left", "shoulder", "pitch"): -100, ("left", "shoulder", "roll"): 10,
         ("left", "shoulder", "yaw"): -25, ("left", "elbow"): 60,
         ("right", "shoulder", "pitch"): -112, ("right", "shoulder", "roll"): -10,
         ("right", "shoulder", "yaw"): 25, ("right", "elbow"): 60}
_TOP_X = {("left", "shoulder", "pitch"): -120, ("left", "shoulder", "roll"): 8,
          ("left", "shoulder", "yaw"): -50, ("left", "elbow"): 40,
          ("right", "shoulder", "pitch"): -138, ("right", "shoulder", "roll"): -8,
          ("right", "shoulder", "yaw"): 50, ("right", "elbow"): 40}
_SIDE3 = _arms(-88, 40, 85)                # ③ 画圈的侧扫位
_ARCH = _arms(-120, 55, 85, {("waist", "pitch"): -15})   # ④ 扩胸后仰
_BEND_A = {("waist", "roll"): -22,         # ⑤ 举右臂斜上、向左弯（对侧）
           ("right", "shoulder", "pitch"): -60,
           ("right", "shoulder", "roll"): -25,
           ("right", "elbow"): 85,
           ("left", "shoulder", "roll"): 30,
           ("left", "elbow"): 85}
_BEND_B = {("waist", "roll"): 22,          # ⑤ 举左臂斜上、向右弯
           ("left", "shoulder", "pitch"): -60,
           ("left", "shoulder", "roll"): 25,
           ("left", "elbow"): 85,
           ("right", "shoulder", "roll"): -30,
           ("right", "elbow"): 85}

# ---- 各节编排：[(节内拍号, 姿态), ...]，拍号必须递增且 ≤ SECTION_BEATS ----
_SEC_BEATS = {
    "1_senobi": ("①背伸びの運動（8拍1回×2：4拍举至-150°,4拍放下）",
                 [(0, _DOWN_V), (4, _UP_HEAD), (8, _DOWN_V),
                  (12, _UP_HEAD), (16, _DOWN_V)]),
    "2_ude": ("②腕の運動（8拍1来回×2，末拍收回下垂）",
              [(4, _F2), (8, _B2), (12, _F2), (16, _DN3)]),
    "3_udemawashi": ("③腕を回す運動（8拍1圈×2：拍4顶点双手交叉）",
                     [(3, _MID3), (4, _TOP_X), (6, _SIDE3), (8, _DN3),
                      (11, _MID3), (12, _TOP_X), (14, _SIDE3), (16, _DN3)]),
    "4_mune": ("④胸をそらす運動（8拍1回×2）",
               [(4, _ARCH), (8, {}), (12, _ARCH), (16, {})]),
    "5_yokomage": ("⑤体を横曲げする運動（左右交替×2）",
                   [(2, _BEND_A), (4, {}), (6, _BEND_B), (8, {}),
                    (10, _BEND_A), (12, {}), (14, _BEND_B), (16, {})]),
}

SECTIONS = [(key, name, beats) for key, (name, beats) in _SEC_BEATS.items()]


def _beats_to_wps(beats, offset_beats):
    """拍号编排 → 秒制路点（整体平移 offset_beats 拍）。"""
    return [{"t": round((b + offset_beats) * BEAT, 3), "pose": p}
            for b, p in beats]


def build():
    """构建 demo 用 MOTIONS：通し + 5 个单节，全部在节拍网格上。"""
    total_beats = PRELUDE_BEATS + sum(beats[-1][0] for _, _, beats in SECTIONS)
    full = {"description": f"デモ通し ①~⑤（八拍节奏 BPM={BPM:g}，共{total_beats}拍）",
            "waypoints": [{"t": 0.0, "pose": {}}]}
    offset = PRELUDE_BEATS
    for _, _, beats in SECTIONS:
        full["waypoints"] += _beats_to_wps(beats, offset)
        offset += beats[-1][0]   # 各节时长=其最后一个拍号（①为8拍,其余16拍）

    result = {"0_demo_full": full}
    for key, name, beats in SECTIONS:
        result[key] = {"description": f"{name}（BPM={BPM:g}）",
                       "waypoints": [{"t": 0.0, "pose": {}}]
                       + _beats_to_wps(beats, 1)}
    return result


def max_joint_speed(motion, neutral_deg):
    """返回 (最大关节速度deg/s, 发生的关节, 发生时刻)——用于校验不超真机上限。"""
    worst = (0.0, None, 0.0)
    wps = motion["waypoints"]
    tokens = {t for wp in wps for t in wp["pose"]}
    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        for tok in tokens:
            av = a["pose"].get(tok, neutral_deg.get(tok, 0.0))
            bv = b["pose"].get(tok, neutral_deg.get(tok, 0.0))
            speed = abs(bv - av) / dt
            if speed > worst[0]:
                worst = (speed, tok, b["t"])
    return worst


def install():
    """把 demo 动作集就地替换进 motions.MOTIONS（sim_viewer/ai_robot 共享同一 dict）。"""
    demo = build()
    motions.MOTIONS.clear()
    motions.MOTIONS.update(demo)


if __name__ == "__main__":
    install()
    import sim_viewer
    sim_viewer.main()
