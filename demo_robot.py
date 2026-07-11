# -*- coding: utf-8 -*-
"""真机 demo 入口：把预设替换为 ①~⑤ 核心环节，其余流程与 ai_robot.py 完全一致。

预设映射：
    p1 = ①背伸びの運動        p2 = ②腕の運動        p3 = ③腕を回す運動
    p4 = ④胸をそらす運動      p5 = ⑤体を横曲げする運動
    p6 = デモ通し ①~⑤ 连续（约68秒，限速后会更长）

用法（参数与 ai_robot.py 相同）：
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
motions.MOTIONS.update(_demo)            # p1~p5 = ①~⑤ 单节
motions.MOTIONS["6_demo_full"] = _full   # p6 = 通し

import ai_robot

if __name__ == "__main__":
    ai_robot.main()
