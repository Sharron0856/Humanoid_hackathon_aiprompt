# -*- coding: utf-8 -*-
"""临时校验脚本：demo 动作集的限位/自碰撞/峰值速度一键检查。"""
import demo_viewer, motions

demo_viewer.install()
import validate  # noqa: E402  导入即运行限位+自碰撞扫描

print()
print("=== 峰值关节速度检查（真机上限45°/s）===")
for k, m in motions.MOTIONS.items():
    s, tok, t = demo_viewer.max_joint_speed(m, validate.neutral_deg)
    flag = "OK " if s <= 45 else "OVER"
    print(f"  [{flag}] {k}: {s:.1f} deg/s @ {'.'.join(tok)} t={t:.2f}s")
