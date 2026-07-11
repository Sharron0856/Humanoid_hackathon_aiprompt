"""
把 motions.py 里定稿的动作路点导出成markdown表格（motions_export.md）。

用途：明天上真机时，把这份表交给"arm_sdk真机映射"环节——每个动作在哪个时间点、
哪个关节、什么角度，一目了然，不用现场翻代码。

用法：python export_waypoints.py
（不需要mujoco，纯文本处理，任何电脑都能跑）
"""
from motions import MOTIONS

OUT = "motions_export.md"

lines = ["# G1 ラジオ体操动作路点表（仿真定稿版）\n",
         "> 由 export_waypoints.py 自动生成。角度单位：度。未列出的关节 = 0（自然下垂）。\n"]

for key, motion in MOTIONS.items():
    lines.append(f"\n## {key} — {motion['description']}\n")
    lines.append("| 时间(s) | 关节 | 角度(°) |")
    lines.append("|---|---|---|")
    for wp in motion["waypoints"]:
        if not wp["pose"]:
            lines.append(f"| {wp['t']:.1f} | （全部回中立位） | 0 |")
            continue
        for tokens, deg in wp["pose"].items():
            joint = "_".join(tokens)
            lines.append(f"| {wp['t']:.1f} | {joint} | {deg:.0f} |")
    total = motion["waypoints"][-1]["t"]
    lines.append(f"\n单次时长：{total:.1f}s\n")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"已导出到 {OUT}")
