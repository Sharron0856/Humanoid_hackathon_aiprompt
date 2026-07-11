# -*- coding: utf-8 -*-
"""デモ用查看器：ラジオ体操第一 ①~⑤ 核心环节，节拍对齐版。

通し = 零号挨拶14s（挥手→手放胸前礼→回站姿）+ ①8拍 + ②~⑤各16拍，
节与节之间另有静止缓冲（SECTION_GAP_S，语音报幕窗）。动作部分全在八拍节拍
网格上，节奏由 BPM 控制，默认 77（接近NHK原版），可用环境变量 DEMO_BPM 覆盖。

真机速度约束（arm_sdk 硬上限 45°/s）决定了编排取舍：
  - ②腕の運動：前-70°/后+20°，3拍1振り完整2来回（真人版4来回且摆幅更大；
    摆幅和次数二选一，按"动作×2"的原版结构取次数）
  - ③腕を回す：一个乐句画1圈、共2圈（真人版4圈做不到），第1圈外→内、
    第2圈反対回し，胸前/头顶交叉姿态与原版对齐
  - ④胸をそらす：横振り→低位交差→胸反らし，共2回（真人版4回），
    T/Y姿态预载肩yaw±30°给低位交叉腾速度余量；反らし臂高-130°有伸展感，
    需3拍扬起（2拍会超速），节奏为 2-2-3-3-3-3
  - ⑤横曲げ：举臂走pitch前举到头顶-140°（侧举roll限位129°够不到头顶），
    举臂与侧弯折叠成一条弧线（三段式装不下16拍），每侧顶点"弯-松-弯"2弹；
    回位用显式站姿 _STAND5（{}回keyframe站姿会超速45.9°/s）
  当前编排在 BPM=77 时最大关节速度 43.6°/s；BPM 再快就会突破 45°/s 上限，
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
SECTION_BEATS = 16         # 每节 2×8拍
GREETING_S = 14.0          # 零号挨拶动作总长（秒），结束后①准时起手
# 通し中节与节之间的静止缓冲：给语音报幕（节名/コツ）留时间窗，可用
# DEMO_GAP 覆盖（语音功能联调时按实际TTS时长微调，建议0.5~1.5s）。
SECTION_GAP_S = float(os.environ.get("DEMO_GAP", "1.0"))

# ---- 姿态（在 motions.py 已验证姿态基础上按速度上限微调）----
_F = _arms(-90, 15, 85)                    # 前平举
_OH = _arms(-165, 25, 85)                  # 举过头顶（备用）
# ① 只动肩pitch：roll/肘在上下两姿态完全一致，纯直臂上举↔放下
_UP_HEAD = _arms(-150, 20, 85)             # 举起：直臂高举过头
_DOWN_V = _arms(-15, 20, 85)               # 放下：直臂近垂直下垂
# ② 完整2来回的速度账：前后摆幅90°、每摆3拍（38.5°/s），4摆+4拍收尾=16拍。
#    摆幅再大（如前-90/后+35=125°）就只装得下1.5来回。
_F2 = _arms(-70, 15, 85)                   # ② 前摆（直臂，略低于水平）
_B2 = _arms(20, 12, 75)                    # ② 后摆
_DN3 = _arms(-25, 15, 85)                  # ②③共用的下垂位（微前倾，给圈顶留限速余量）
# ③ 双手交叉：肩roll内收会压到头/躯干，所以交叉靠「肘弯40°+肩yaw内旋50°」
#    实现（模型网格搜索得到的无碰撞姿态：左手过中线7cm、右手9cm）。
#    _TOP_X=头顶上方交叉；_X_CHEST=同一交叉构型降到胸前（原版的起点/终点姿态）。
_TOP_X = {("left", "shoulder", "pitch"): -120, ("left", "shoulder", "roll"): 8,
          ("left", "shoulder", "yaw"): -50, ("left", "elbow"): 40,
          ("right", "shoulder", "pitch"): -138, ("right", "shoulder", "roll"): -8,
          ("right", "shoulder", "yaw"): 50, ("right", "elbow"): 40}
#    _X_CHEST 网格搜索结论：胸口高度两腕更容易互撞，需 yaw 62°+肘42°+pitch错开14°
#    （左手过中线1.6cm、右手3.8cm，段速峰值40.4°/s，与TOP_X/DN3的插值路径无碰撞）。
_X_CHEST = {("left", "shoulder", "pitch"): -74, ("left", "shoulder", "roll"): 8,
            ("left", "shoulder", "yaw"): -62, ("left", "elbow"): 42,
            ("right", "shoulder", "pitch"): -88, ("right", "shoulder", "roll"): -8,
            ("right", "shoulder", "yaw"): 62, ("right", "elbow"): 42}
_SIDE3 = _arms(-75, 55, 85)                # ③ 画圈的侧扫位（roll 55°，圈要抡大）
# ④ 三站位：横振りT字 → 低位交差 → 斜め上Y字+胸反らし。
#    低位交叉的运动学死角：手臂下垂时肩yaw轴近垂直，yaw≥90°前臂才横扫过中线，
#    但从yaw=0两拍转90°超速。解法：T/Y直臂时yaw是纯轴向自旋（手不动、看不见），
#    预载±30°，低位只需再转60°。_LOW_X4 参数为网格搜索结果
#    （左手过中线2.7cm、右手1.7cm、腹前0.83m高，段速峰值43.6°/s，路径无碰撞）。
_YAW_PRE = {("left", "shoulder", "yaw"): -30, ("right", "shoulder", "yaw"): 30}
_T4 = _arms(-10, 70, 85, _YAW_PRE)                        # ④ 横振り（T字）
# ④ 胸反らし要有伸展感：臂举到-130°（明显过肩、如参考照片的Y字）+腰后仰18°。
#    从低位交叉到-130°差98°，2拍内62.9°/s超速 → 给3拍（41.9°/s），
#    节奏变为 2-2-3-3-3-3：横振り/交差利落，两次反らし各3拍悠长扬起。
_Y4 = _arms(-130, 38, 85, {**_YAW_PRE, ("waist", "pitch"): -18})  # ④ 斜め上+胸反らし
_LOW_X4 = {("left", "shoulder", "pitch"): -32, ("left", "shoulder", "roll"): 2,
           ("left", "shoulder", "yaw"): -90, ("left", "elbow"): 28,
           ("right", "shoulder", "pitch"): -46, ("right", "shoulder", "roll"): -2,
           ("right", "shoulder", "yaw"): 90, ("right", "elbow"): 40}
_END4 = _arms(-32, 15, 85, _YAW_PRE)                      # ④ 收尾下垂（保留yaw预载）
# ⑤ 举臂高度的物理现实：肩roll限位129°，侧举最多只到水平上方39°；要"举到头顶"
#    必须走pitch前举（限位-177°）。前举行程约150°需4.5拍，三段式（举→弯→停）
#    装不下 → 把举臂和侧弯折叠成一条弧线同时完成：
#      扬起入弯4.5拍 → 顶点定格1拍 → 换边横摆4.5拍 → 定格1拍 → 收臂5拍 = 16拍。
#    举臂pitch -140°（手在头顶上方），roll外张20°防倒向对侧时蹭头。
#    下垂臂显式pitch +5（用neutral的+11.5会让换边段顶到43.2°/s）、外张30°防撞髋。
_BEND_A = {("right", "shoulder", "pitch"): -140, ("right", "shoulder", "roll"): -20,
           ("right", "elbow"): 85,
           ("left", "shoulder", "pitch"): 5, ("left", "shoulder", "roll"): 30,
           ("left", "elbow"): 80,
           ("waist", "roll"): -22}
_BEND_B = {("left", "shoulder", "pitch"): -140, ("left", "shoulder", "roll"): 20,
           ("left", "elbow"): 85,
           ("right", "shoulder", "pitch"): 5, ("right", "shoulder", "roll"): -30,
           ("right", "elbow"): 80,
           ("waist", "roll"): 22}
# ⑤ 顶点"弯-松-弯"双脉冲（原版每侧弯2次）：腰22°↔12°各半拍，25.7°/s，
#   手臂不动、零额外拍数。
_BEND_A2 = {**_BEND_A, ("waist", "roll"): -12}
_BEND_B2 = {**_BEND_B, ("waist", "roll"): 12}
# ⑤ 回位用显式站姿：keyframe站姿肩pitch=+11.5°，从-60°用{}回位是45.9°/s超速；
#   显式给+5°则为41.7°/s，视觉上与自然站姿几乎无差别。
_STAND5 = _arms(5, 15, 78)

# ---- 零号挨拶动作（通し开头14s；只用肩+肘，23DOF真机兼容）----
# 挥手：右臂抬到脸侧（肘弯30°前臂朝上），肩yaw±15°左右摆=招手；
# 绅士礼：手收到胸口（yaw内旋40°+肘深弯18°），只动手臂不弯腰。
_WAVE_L = {("right", "shoulder", "pitch"): -85, ("right", "shoulder", "roll"): -20,
           ("right", "shoulder", "yaw"): -15, ("right", "elbow"): 30}
_WAVE_R = {**_WAVE_L, ("right", "shoulder", "yaw"): 15}
_CHEST_HAND = {("right", "shoulder", "pitch"): -55, ("right", "shoulder", "roll"): -8,
               ("right", "shoulder", "yaw"): 40, ("right", "elbow"): 18}
# (秒, 姿态)。挥手0~4.8 → 手放胸前定格4.8~9.0 → 恢复站立9.0~13.0，
# 13.0~14.0的最后1秒滑入①的起手位（20°/s，远低于限速）。
_GREETING_WPS = [(0.0, {}), (2.4, _WAVE_L), (3.2, _WAVE_R), (4.0, _WAVE_L),
                 (4.8, _WAVE_R), (6.4, _CHEST_HAND), (9.0, _CHEST_HAND),
                 (11.5, _STAND5), (13.0, _STAND5)]

# ---- 各节编排：[(节内拍号, 姿态), ...]，拍号必须递增且 ≤ SECTION_BEATS ----
_SEC_BEATS = {
    "1_senobi": ("①背伸びの運動（8拍1回：4拍举至-150°,4拍放下）",
                 [(0, _DOWN_V), (4, _UP_HEAD), (8, _DOWN_V)]),
    "2_ude": ("②腕の運動（3拍1振り・完整2来回，末4拍收回下垂）",
              [(3, _F2), (6, _B2), (9, _F2), (12, _B2), (16, _DN3)]),
    "3_udemawashi": ("③腕を回す運動（外→内1圈+反対回し1圈，胸前・頭上交差）",
                     [(2, _SIDE3), (4, _TOP_X), (6, _X_CHEST), (8, _DN3),
                      (10, _X_CHEST), (12, _TOP_X), (14, _SIDE3), (16, _DN3)]),
    "4_mune": ("④胸をそらす運動（横振り→低位交差→3拍かけて大きく胸反らし×2）",
               [(2, _T4), (4, _LOW_X4), (7, _Y4), (10, _LOW_X4),
                (13, _Y4), (16, _END4)]),
    "5_yokomage": ("⑤体を横曲げする運動（前から頭上へ振り上げ→側屈2弹，左右各1轮）",
                   [(4.5, _BEND_A), (5, _BEND_A2), (5.5, _BEND_A),
                    (10, _BEND_B), (10.5, _BEND_B2), (11, _BEND_B),
                    (16, _STAND5)]),
}

SECTIONS = [(key, name, beats) for key, (name, beats) in _SEC_BEATS.items()]


def _beats_to_wps(beats, offset_beats):
    """拍号编排 → 秒制路点（整体平移 offset_beats 拍）。"""
    return [{"t": round((b + offset_beats) * BEAT, 3), "pose": p}
            for b, p in beats]


def build():
    """构建 demo 用 MOTIONS：通し + 5 个单节 + 挨拶，动作部分全在节拍网格上。

    通し = 挨拶14s（挥手→手放胸前礼→回站姿）→ ①~⑤，每节结束后按
    SECTION_GAP_S 保持结束姿态静止（节间缓冲=语音报幕窗），下一节的
    拍号网格从缓冲结束处重新起算。
    """
    gap_beats = SECTION_GAP_S / BEAT
    full = {"description": f"デモ通し 挨拶+①~⑤（BPM={BPM:g}，"
                           f"节间缓冲{SECTION_GAP_S:g}s×{len(SECTIONS) - 1}）",
            "waypoints": [{"t": round(s, 3), "pose": p} for s, p in _GREETING_WPS]}
    offset = GREETING_S / BEAT
    for i, (_, _, beats) in enumerate(SECTIONS):
        full["waypoints"] += _beats_to_wps(beats, offset)
        offset += beats[-1][0]   # 各节时长=其最后一个拍号（①~⑤均为16拍）
        if i < len(SECTIONS) - 1:   # 节间缓冲：结束姿态原地保持
            offset += gap_beats
            full["waypoints"].append(
                {"t": round(offset * BEAT, 3), "pose": beats[-1][1]})

    result = {"0_demo_full": full}
    for key, name, beats in SECTIONS:
        result[key] = {"description": f"{name}（BPM={BPM:g}）",
                       "waypoints": [{"t": 0.0, "pose": {}}]
                       + _beats_to_wps(beats, 1)}
    result["6_aisatsu"] = {
        "description": "零号挨拶（挥手5s→手放胸前礼4s→回站姿）",
        "waypoints": [{"t": round(s, 3), "pose": p} for s, p in _GREETING_WPS]}
    return result


def section_timeline():
    """通し中各段的时间表 [(key, 名称, 开始秒, 结束秒), ...]（含节间缓冲）。

    语音功能的调度依据：
      - 挨拶语音：0 ~ 14s（与挥手/鞠躬动作同步）
      - 各节报幕/コツ：在上一节结束（=缓冲开始）时触发，缓冲+节首拍的时间够读节名；
        コツ长句会自然压在动作上（原版广播体操旁白也是边做边说）。
    """
    timeline = [("6_aisatsu", "零号挨拶（挥手→胸前礼→站姿）", 0.0, GREETING_S)]
    t = GREETING_S
    for i, (key, name, beats) in enumerate(SECTIONS):
        start = t
        t += beats[-1][0] * BEAT
        timeline.append((key, name, round(start, 2), round(t, 2)))
        if i < len(SECTIONS) - 1:
            t += SECTION_GAP_S
    return timeline


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
