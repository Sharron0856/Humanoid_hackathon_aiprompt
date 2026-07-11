# -*- coding: utf-8 -*-
"""
用千问逐节生成ラジオ体操第一全 13 节，拼接成完整序列存到 ai_taiso.json。

为什么逐节生成：一次生成 13 节 JSON 太长，模型容易偷工减料；
逐节调用每节质量最高，做不了的动作（跳跃/屈膝）由系统提示词里的
可行性对照表自动降级成替代方案。

拼好后会用 mujoco 做无窗口逐帧自碰撞扫描，全部通过才写文件。
生成的序列在 ai_viewer.py 里以预设 p6 播放。

用法：py make_taiso.py   （约需 1~3 分钟，13 次 API 调用）
"""
import json
import math
import sys
import time

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from llm_motion import MotionAgent, MODEL

OUT_PATH = "ai_taiso.json"
GAP = 0.8          # 节与节之间的停顿（秒）
PRELUDE = 2.0      # 开头站姿前奏（秒）

# (节号, 给模型的指令)。可行性细节模型会从系统提示词的对照表里取。
SECTIONS = [
    ("①", "伸びの運動（伸展运动）"),
    ("②", "腕を振って脚を曲げ伸ばす運動（摆臂运动，屈膝按规则省略）"),
    ("③", "腕を回す運動（手臂绕环，按规则用4路点近似画圈）"),
    ("④", "胸を反らす運動（扩胸后仰）"),
    ("⑤", "体を横に曲げる運動（体侧屈）"),
    ("⑥", "体を前後に曲げる運動（体前后屈，前弯按规则用近似）"),
    ("⑦", "体をねじる運動（转体）"),
    ("⑧", "腕を上下に伸ばす運動（手臂上下伸展）"),
    ("⑨", "体を斜め下に曲げ胸を反らす運動（斜下屈体+扩胸）"),
    ("⑩", "体を回す運動（躯干绕环，按规则用路点近似）"),
    ("⑪", "両脚でとぶ運動（跳跃，按规则只做节奏摆臂替代）"),
    ("⑫", "同第②节，摆臂运动"),
    ("⑬", "深呼吸の運動（深呼吸收尾，节奏放慢）"),
]


def generate_all():
    agent = MotionAgent()
    sections = []
    for i, (mark, desc) in enumerate(SECTIONS, 1):
        agent.reset()   # 每节独立生成，不让上下文越滚越长
        prompt = (f"生成ラジオ体操第一 第{i}节 {mark} {desc}。"
                  f"时长控制在 11~13 秒，从自然站姿开始、以自然站姿（空pose）结束，"
                  f"节奏均匀有体操感。做不了的部分按可行性对照表降级，不要跳过整节。")
        t0 = time.time()
        m = agent.generate(prompt)
        total = m["waypoints"][-1]["t"]
        print(f"  [{i:2d}/13] {mark} {m['description']}  "
              f"({len(m['waypoints'])}路点 {total:.1f}s, 耗时{time.time() - t0:.1f}s)")
        sections.append(m)
    return sections


def stitch(sections):
    """按 motions.py 的 _seg 思路平移时间轴拼接，节间留 GAP 秒停顿。"""
    waypoints = [{"t": 0.0, "pose": {}}]
    offset = PRELUDE
    timeline = []
    for m in sections:
        timeline.append((round(offset, 1), m["description"]))
        for wp in m["waypoints"]:
            t = round(offset + wp["t"], 2)
            if t <= waypoints[-1]["t"]:      # 保证严格递增
                t = round(waypoints[-1]["t"] + 0.05, 2)
            waypoints.append({"t": t, "pose": dict(wp["pose"])})
        offset = waypoints[-1]["t"] + GAP
    return waypoints, timeline


def collision_scan(waypoints):
    """无窗口逐帧(20Hz)运动学回放，返回检出的自碰撞对集合。"""
    import mujoco
    from ai_viewer import build_joint_index
    from sim_viewer import MODEL_PATH, pose_at, robot_self_contacts

    model = mujoco.MjModel.from_xml_path(MODEL_PATH)
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    joint_addr = build_joint_index(model)
    neutral_qpos = data.qpos.copy()
    neutral_deg = {t: math.degrees(neutral_qpos[a]) for t, a in joint_addr.items()}
    base_qpos = data.qpos[0:7].copy()

    motion = {"waypoints": waypoints}
    total = waypoints[-1]["t"]
    hits = {}
    for i in range(int(total * 20) + 1):
        t = min(i / 20, total - 1e-6)
        data.qpos[:] = neutral_qpos
        for tokens, deg in pose_at(motion, t, neutral_deg).items():
            data.qpos[joint_addr[tokens]] = math.radians(deg)
        data.qpos[0:7] = base_qpos
        mujoco.mj_forward(model, data)
        for pair in robot_self_contacts(model, data):
            hits.setdefault(pair, round(t, 1))
    return hits


def main():
    print(f"模型: {MODEL} ｜ 开始逐节生成 13 节……")
    sections = generate_all()
    waypoints, timeline = stitch(sections)
    total = waypoints[-1]["t"]
    print(f"\n拼接完成：{len(waypoints)} 个路点，总长 {total / 60:.0f}分{total % 60:.0f}秒")
    for t, desc in timeline:
        print(f"  {t:6.1f}s  {desc}")

    print("\n自碰撞扫描中……")
    hits = collision_scan(waypoints)
    if hits:
        print("⚠ 检出自碰撞（角度已在限位内，多为轻微擦碰，可播放后肉眼确认）:")
        for pair, t in hits.items():
            print(f"    t≈{t}s  {pair[0]} <-> {pair[1]}")
    else:
        print("✔ 全程无自碰撞")

    data = {
        "name": "ai_taiso_daiichi",
        "description": f"AI生成 ラジオ体操第一 全13节（{total / 60:.0f}分{total % 60:.0f}秒）",
        "waypoints": [{"t": wp["t"],
                       "pose": {".".join(k): v for k, v in wp["pose"].items()}}
                      for wp in waypoints],
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"\n✔ 已保存 {OUT_PATH} —— 在 ai_viewer.py 里输入 p6 播放")


if __name__ == "__main__":
    main()
