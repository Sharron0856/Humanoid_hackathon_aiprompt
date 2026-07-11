"""
ラジオ体操动作路点定义（G1可行的4节精简版）。

角度单位全部是【度】，方便人类阅读和手动调整。
关节用"关键词元组"指定（如 ("left","shoulder","roll")），运行时会自动模糊匹配
模型里的真实关节名——这样不管你下载的G1模型是23自由度还是29自由度版本、
关节命名有细微差异，都能工作；匹配不上会打印清晰的错误和可用关节列表。

★ 调参方法：改这里的角度数字 → 重新跑 sim_viewer.py → 肉眼看效果，循环即可。
   如果某个动作方向反了（比如手臂向后抬而不是向侧抬），把对应角度的正负号翻过来。

★ 明天上真机时：把这里最终版的角度表交给"动作枚举/arm_sdk"环节做映射，
   用 export_waypoints.py 可以一键导出成markdown表格。
"""

# 每个动作 = 一串带时间戳的姿态关键帧，帧与帧之间线性插值。
# pose里没写到的关节自动回到"自然站姿"（G1的keyframe：肩微张11.5°、肘弯73°）。
# 空pose {} 表示整体回自然站姿。t 的单位是秒。
# G1关节限位（本文件用到的）：肩roll 向外最大129°，肩pitch [-177°,153°]，
# 肘 [-60°,120°]，腰yaw ±150°。
# ★ 肘关节约定（实测本模型）：qpos≈85° 手臂才是伸直的；qpos=0 是屈肘约80°。
#   （keyframe自然站姿的肘=73°就是近乎伸直下垂）

# ---- 常用姿态（供⑤连续序列复用；角度=度）----

def _arms(pitch, roll, elbow, extra=None):
    """左右对称的手臂姿态。pitch负=前上举，roll正=左臂向外。"""
    pose = {
        ("left", "shoulder", "pitch"): pitch,
        ("right", "shoulder", "pitch"): pitch,
        ("left", "shoulder", "roll"): roll,
        ("right", "shoulder", "roll"): -roll,
        ("left", "elbow"): elbow,
        ("right", "elbow"): elbow,
    }
    if extra:
        pose.update(extra)
    return pose

_FRONT = _arms(-90, 15, 85)          # 前平举（直臂）
_OVERHEAD = _arms(-165, 25, 85)      # 举过头顶（直臂，已验证无碰撞）
_BACK_SWING = _arms(35, 12, 75)      # 双臂后摆
_SIDE = _arms(-10, 85, 85)           # 侧平举（直臂）
_DOWN = _arms(5, 15, 78)             # 自然下垂（过渡用）
_CHEST_ARCH = _arms(-120, 55, 85, {("waist", "pitch"): -15})  # 张臂扩胸+腰后仰
_TUCK = _arms(-30, 15, 0)            # 屈肘收于胸前（肘qpos 0=实弯约80°）

# ⑤~⑬ 用到的姿态
_SIDE_BEND_A = {("waist", "roll"): 22,   # 向一侧弯腰，对侧手臂举过头
                ("right", "shoulder", "pitch"): -150,
                ("right", "shoulder", "roll"): -25,
                ("right", "elbow"): 85}
_SIDE_BEND_B = {("waist", "roll"): -22,
                ("left", "shoulder", "pitch"): -150,
                ("left", "shoulder", "roll"): 25,
                ("left", "elbow"): 85}
_FWD_BEND = _arms(-60, 15, 85, {("waist", "pitch"): 28})    # 前弯（腰pitch限位30°）
_FWD_HALF = _arms(-40, 15, 80, {("waist", "pitch"): 15})    # 前弯回弹的中间位
_TWIST_A = {("waist", "yaw"): 45,
            ("left", "shoulder", "roll"): 30, ("right", "shoulder", "roll"): -30,
            ("left", "elbow"): 80, ("right", "elbow"): 80}
_TWIST_B = {("waist", "yaw"): -45,
            ("left", "shoulder", "roll"): 30, ("right", "shoulder", "roll"): -30,
            ("left", "elbow"): 80, ("right", "elbow"): 80}
_DIAG_A = _arms(-60, 15, 85, {("waist", "pitch"): 25, ("waist", "yaw"): 25})
_DIAG_B = _arms(-60, 15, 85, {("waist", "pitch"): 25, ("waist", "yaw"): -25})
_CIRC_UP = _arms(-120, 30, 85)                               # 体回し：臂上举随动
_CIRC_F = _arms(-120, 30, 85, {("waist", "pitch"): 22})
_CIRC_L = _arms(-120, 30, 85, {("waist", "roll"): 22})
_CIRC_B = _arms(-120, 30, 85, {("waist", "pitch"): -18})
_CIRC_R = _arms(-120, 30, 85, {("waist", "roll"): -22})
_JACK_UP = _arms(-10, 115, 85)                               # 跳跃的摆臂（侧上举）
_BREATH = {("left", "shoulder", "roll"): 100, ("right", "shoulder", "roll"): -100,
           ("left", "elbow"): 85, ("right", "elbow"): 85}


def _seg(wps, offset):
    """把 [(局部时间, pose), ...] 平移 offset 秒，生成路点列表。"""
    return [{"t": round(t + offset, 2), "pose": p} for t, p in wps]


# ---- ラジオ体操第一 各节（局部时间，秒）----
_SEG_NOBI = [(1.5, _FRONT), (3, _OVERHEAD), (4.5, _OVERHEAD), (6, {}), (6.5, {}),
             (8, _FRONT), (9.5, _OVERHEAD), (11, _OVERHEAD), (12.5, {})]           # ①伸び ×2
_SEG_UDEFURI = [(1.2, _FRONT), (2.4, _BACK_SWING), (4.2, _FRONT), (5.4, _BACK_SWING),
                (7.2, _FRONT), (8.4, _BACK_SWING), (10.2, _FRONT), (11.1, _BACK_SWING),
                (12, _DOWN)]                                                        # ②⑫腕振り ×4
_SEG_MAWASHI = [(0.8, _FRONT), (1.5, _OVERHEAD), (2.2, _SIDE), (3, _DOWN),
                (3.8, _FRONT), (4.5, _OVERHEAD), (5.2, _SIDE), (6, _DOWN),
                (6.8, _FRONT), (7.5, _OVERHEAD), (8.2, _SIDE), (9, _DOWN),
                (9.8, _FRONT), (10.5, _OVERHEAD), (11.2, _SIDE), (12, _DOWN)]       # ③腕回し ×4圈
_SEG_MUNE = [(1.5, _FRONT), (3, _CHEST_ARCH), (4.5, _CHEST_ARCH), (6, {}),
             (7.5, _FRONT), (9, _CHEST_ARCH), (10.5, _CHEST_ARCH), (12, {}),
             (12.5, {})]                                                            # ④胸反らし ×2
_SEG_YOKO = [(1.2, _SIDE_BEND_A), (2.0, _SIDE_BEND_A), (3.25, {}),
             (4.45, _SIDE_BEND_B), (5.25, _SIDE_BEND_B), (6.5, {}),
             (7.7, _SIDE_BEND_A), (8.5, _SIDE_BEND_A), (9.75, {}),
             (10.95, _SIDE_BEND_B), (11.75, _SIDE_BEND_B), (13, {})]                # ⑤横曲げ ×4
_SEG_ZENGO = [(1.0, _FWD_BEND), (1.6, _FWD_HALF), (2.2, _FWD_BEND), (3.2, {}),
              (4.3, _CHEST_ARCH), (5.3, _CHEST_ARCH), (6.5, {}),
              (7.5, _FWD_BEND), (8.1, _FWD_HALF), (8.7, _FWD_BEND), (9.7, {}),
              (10.8, _CHEST_ARCH), (11.8, _CHEST_ARCH), (13, {})]                   # ⑥前後曲げ ×2
_SEG_NEJIRI = [(1.1, _TWIST_A), (1.8, _TWIST_A), (3.0, {}),
               (4.1, _TWIST_B), (4.8, _TWIST_B), (6.0, {}),
               (7.1, _TWIST_A), (7.8, _TWIST_A), (9.0, {}),
               (10.1, _TWIST_B), (10.8, _TWIST_B), (12.0, {})]                      # ⑦ねじり ×4
_SEG_JOUGE = [(0.5, _TUCK), (1.7, _OVERHEAD), (3.0, _TUCK), (4.2, _OVERHEAD),
              (5.5, _TUCK), (6.7, _OVERHEAD), (8.0, _TUCK), (9.0, _OVERHEAD),
              (10, _DOWN)]                                                          # ⑧上下伸ばし ×4
_SEG_NANAME = [(1.2, _DIAG_A), (2.0, _DIAG_A), (3.2, {}),
               (4.2, _CHEST_ARCH), (5.2, _CHEST_ARCH), (6.5, {}),
               (7.7, _DIAG_B), (8.5, _DIAG_B), (9.7, {}),
               (10.7, _CHEST_ARCH), (11.7, _CHEST_ARCH), (13, {})]                  # ⑨斜め下+胸反らし
_SEG_MAWASU = [(0.8, _CIRC_UP), (1.8, _CIRC_F), (2.8, _CIRC_L), (3.8, _CIRC_B),
               (4.8, _CIRC_R), (5.8, _CIRC_F), (6.8, _CIRC_R), (7.8, _CIRC_B),
               (8.8, _CIRC_L), (9.8, _CIRC_F), (10.8, _CIRC_UP), (12.5, {})]        # ⑩体回し 左右各1圈
_SEG_JUMP = [(0.7, _JACK_UP), (1.4, _DOWN), (2.1, _JACK_UP), (2.8, _DOWN),
             (3.5, _JACK_UP), (4.2, _DOWN), (4.9, _JACK_UP), (5.6, _DOWN),
             (6.3, _JACK_UP), (7.0, _DOWN), (7.7, _JACK_UP), (8.4, _DOWN),
             (9.1, _JACK_UP), (9.8, _DOWN), (10.5, _JACK_UP), (11.2, _DOWN),
             (12, {})]                                                              # ⑪跳跃摆臂 ×8
_SEG_BREATH = [(3, _BREATH), (4, _BREATH), (6.5, {}), (9.5, _BREATH), (10.5, _BREATH),
               (13, {})]                                                            # ⑬深呼吸 ×2

MOTIONS = {

    # ① 伸びの運動：双臂经身体前方缓慢上举过头，保持，再缓慢放下
    # 举过头用肩pitch（负=向前上举，限位-177°），纯肩roll最多只能举到侧上方129°。
    # 顶点姿态 pitch=-165/roll=25/肘=85 已扫描确认无自碰撞（roll<25会撞躯干）。
    "1_nobi_stretch": {
        "description": "伸びの運動（双臂前举过头伸展）",
        "waypoints": [
            {"t": 0.0, "pose": {}},
            {"t": 2.0, "pose": {   # 前平举
                ("left", "shoulder", "pitch"): -90,
                ("right", "shoulder", "pitch"): -90,
                ("left", "shoulder", "roll"): 15,
                ("right", "shoulder", "roll"): -15,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 3.5, "pose": {   # 举过头顶，手臂伸直
                ("left", "shoulder", "pitch"): -165,
                ("right", "shoulder", "pitch"): -165,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 5.0, "pose": {   # 顶点保持
                ("left", "shoulder", "pitch"): -165,
                ("right", "shoulder", "pitch"): -165,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 7.0, "pose": {}},   # 缓慢放回自然站姿
        ],
    },

    # ② 体をねじる運動：腰部左右旋转，手臂微张随动
    "2_torso_twist": {
        "description": "体をねじる運動（腰部左右转体）",
        "waypoints": [
            {"t": 0.0, "pose": {
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
            }},
            {"t": 1.5, "pose": {
                ("waist", "yaw"): 35,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
            }},
            {"t": 3.0, "pose": {
                ("waist", "yaw"): -35,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
            }},
            {"t": 4.5, "pose": {
                ("waist", "yaw"): 35,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
            }},
            {"t": 6.0, "pose": {
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
            }},
        ],
    },

    # ③ 腕を上下に伸ばす運動：屈肘收于胸前 → 向正上方冲举 → 收回，重复
    "3_updown_thrust": {
        "description": "腕を上下に伸ばす運動（手臂上下伸举）",
        "waypoints": [
            {"t": 0.0, "pose": {   # 屈肘收于胸前
                ("left", "shoulder", "pitch"): -30,
                ("right", "shoulder", "pitch"): -30,
                ("left", "shoulder", "roll"): 15,
                ("right", "shoulder", "roll"): -15,
                ("left", "elbow"): 0,
                ("right", "elbow"): 0,
            }},
            {"t": 1.2, "pose": {   # 冲举到最高（同动作①顶点，已验证无碰撞）
                ("left", "shoulder", "pitch"): -165,
                ("right", "shoulder", "pitch"): -165,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 2.4, "pose": {   # 收回胸前
                ("left", "shoulder", "pitch"): -30,
                ("right", "shoulder", "pitch"): -30,
                ("left", "shoulder", "roll"): 15,
                ("right", "shoulder", "roll"): -15,
                ("left", "elbow"): 0,
                ("right", "elbow"): 0,
            }},
            {"t": 3.6, "pose": {
                ("left", "shoulder", "pitch"): -165,
                ("right", "shoulder", "pitch"): -165,
                ("left", "shoulder", "roll"): 25,
                ("right", "shoulder", "roll"): -25,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 4.8, "pose": {
                ("left", "shoulder", "pitch"): -30,
                ("right", "shoulder", "pitch"): -30,
                ("left", "shoulder", "roll"): 15,
                ("right", "shoulder", "roll"): -15,
                ("left", "elbow"): 0,
                ("right", "elbow"): 0,
            }},
        ],
    },

    # ④ 深呼吸：非常缓慢的举臂-放下，作为结尾
    "4_deep_breath": {
        "description": "深呼吸（缓慢举臂放下收尾）",
        "waypoints": [
            {"t": 0.0, "pose": {}},
            {"t": 3.0, "pose": {
                ("left", "shoulder", "roll"): 100,
                ("right", "shoulder", "roll"): -100,
                ("left", "elbow"): 85,
                ("right", "elbow"): 85,
            }},
            {"t": 6.0, "pose": {}},
        ],
    },
    # ⑤ ラジオ体操第一（通し）完整序列，按NHK版节奏近似编排，总长约2分53秒。
    #    时间线（秒）：0前奏 | 13①伸び | 25.5②腕振り | 37.5③腕回し | 49.5④胸反らし
    #    | 62⑤横曲げ | 75⑥前後曲げ | 88⑦ねじり | 100⑧上下伸ばし | 110⑨斜め下+胸反らし
    #    | 123⑩体回し | 135.5⑪跳跃(只做摆臂) | 147.5⑫腕振り | 159.5⑬深呼吸 | 172.5终
    #    如实说明：②⑫的屈膝和⑪的跳跃需要动基座，运动学回放做不了，只做上肢部分；
    #    ③⑩的"画圈"是路点近似。各节时长以NHK音源为参照，误差±1s量级。
    "5_daiichi_full": {
        "description": "ラジオ体操第一（通し）完整序列 ①~⑬（约2分53秒）",
        "waypoints": (
            [{"t": 0.0, "pose": {}}, {"t": 13.0, "pose": {}}]   # 前奏
            + _seg(_SEG_NOBI, 13)        # ① 13-25.5
            + _seg(_SEG_UDEFURI, 25.5)   # ② 25.5-37.5
            + _seg(_SEG_MAWASHI, 37.5)   # ③ 37.5-49.5
            + _seg(_SEG_MUNE, 49.5)      # ④ 49.5-62
            + _seg(_SEG_YOKO, 62)        # ⑤ 62-75
            + _seg(_SEG_ZENGO, 75)       # ⑥ 75-88
            + _seg(_SEG_NEJIRI, 88)      # ⑦ 88-100
            + _seg(_SEG_JOUGE, 100)      # ⑧ 100-110
            + _seg(_SEG_NANAME, 110)     # ⑨ 110-123
            + _seg(_SEG_MAWASU, 123)     # ⑩ 123-135.5
            + _seg(_SEG_JUMP, 135.5)     # ⑪ 135.5-147.5
            + _seg(_SEG_UDEFURI, 147.5)  # ⑫ 147.5-159.5
            + _seg(_SEG_BREATH, 159.5)   # ⑬ 159.5-172.5
        ),
    },

}
