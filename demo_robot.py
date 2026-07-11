# -*- coding: utf-8 -*-
"""真机 demo 入口：把预设替换为 ①~⑤ 核心环节，其余流程与 ai_robot.py 完全一致。

预设映射：
    p1 = ①背伸びの運動        p2 = ②腕の運動        p3 = ③腕を回す運動
    p4 = ④胸をそらす運動      p5 = ⑤体を横曲げする運動
    p6 = 零号挨拶（挥手+胸前礼）
    p7 = デモ通し 挨拶+①~⑤ 连续（约87秒，限速后会更长）

用法（参数与 ai_robot.py 相同，但本入口默认 --max-speed-deg 45=仿真原节奏；
首次链路联调请显式传 --max-speed-deg 10）：
    .\.venv-real\Scripts\python.exe demo_robot.py --robot-dof 29 --interface 192.168.123.222 --execute

注意：④用到腰 pitch、⑤用到腰 roll，默认会被硬拒绝——只有确认腰部硬件支持
（29DOF 且现场负责人同意）才加 --allow-waist-roll-pitch 放行这两节。
不加该参数时 p1~p3 可正常执行，p4/p5/p6 会被拒绝，这是预期行为。
"""
import demo_viewer
import motions

_demo = demo_viewer.build()
_full = _demo.pop("0_demo_full")
motions.MOTIONS.clear()
motions.MOTIONS.update(_demo)            # p1~p5 = ①~⑤ 单节，p6 = 挨拶
motions.MOTIONS["7_demo_full"] = _full   # p7 = 通し

import ai_robot

# 演出入口默认原速 45°/s：编排峰值 43.6°/s，45 下 prepare_motion 不拉伸任何
# 一段，真机时间轴=仿真时间轴（10°/s 是逐段拉伸，快段拖慢4倍、慢段不变，
# 节拍会全乱）。首次链路联调仍应显式传 --max-speed-deg 10 走 probe 流程。
ai_robot.DEFAULT_MAX_SPEED_DEG = 45.0

# 语音教练：真机执行时按动作进度报幕（挨拶/各节コツ/结尾）。
# 配 --robot-speaker 从 G1 扬声器播出；TTS 不可用时自动降级为控制台字幕。
try:
    from demo_voice import TTS_READY, VoiceCoach
    _coach = VoiceCoach()
    _coach.add_alias("0_demo_full", "7_demo_full")   # p7 与通し共用提示表
    if TTS_READY:
        _coach.prefetch_all()
    ai_robot.VOICE_COACH = _coach
except Exception as _e:
    print(f"⚠ 语音教练未启用: {_e}")

if __name__ == "__main__":
    ai_robot.main()
