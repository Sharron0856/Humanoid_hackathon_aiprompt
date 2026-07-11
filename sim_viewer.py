"""
G1 ラジオ体操动作仿真查看器（纯运动学回放，不跑物理）。

作用：在没有真机的情况下，可视化验证 motions.py 里定义的动作路点——
"看起来像不像广播体操、左右是否对称、有没有自碰撞、节奏合不合适"。
角度不满意就改 motions.py 里的数字，重新跑本脚本，肉眼迭代。

用法：
    1. pip install mujoco
    2. 下载G1模型（见README，来自mujoco_menagerie官方模型库）
    3. 修改下面的 MODEL_PATH 指向 scene.xml
    4. python sim_viewer.py

查看器内按键（先用鼠标点一下窗口获得焦点）：
    数字键 1-4 : 切换动作并从头播放
    R          : 从头播放当前动作
    L          : 切换 单次播放/循环播放（默认单次：播完停在结束姿态）
    空格       : 暂停/继续
    ESC        : 退出

注意：这是运动学回放（直接设置关节角度），机器人不会因为动作失衡而摔倒，
真机的平衡表现需要明天用真机验证——但动作外观/对称性/自碰撞在这里就能定稿。
"""
import time
import math

try:
    import mujoco
    import mujoco.viewer
except ImportError:
    raise SystemExit(
        "未安装mujoco，请先运行: pip install mujoco\n"
        "（完全离线可用，装完不需要联网）"
    )

from motions import MOTIONS

# ★ 改成你解压后的模型路径（用正斜杠或双反斜杠都可以）
MODEL_PATH = "mujoco_menagerie/unitree_g1/scene.xml"


# ---------------- 关节名模糊匹配 ----------------

def resolve_joint(model, tokens):
    """
    用关键词元组（如 ("left","shoulder","roll")）在模型里找到唯一匹配的关节。
    找不到或找到多个都会给出清晰报错+可用关节列表，方便排查模型版本差异。
    """
    tokens = [t.lower() for t in tokens]
    matches = []
    for j in range(model.njnt):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j) or ""
        low = name.lower()
        if all(t in low for t in tokens):
            matches.append((j, name))
    if len(matches) == 1:
        return matches[0][0]
    all_names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j)
                 for j in range(model.njnt)]
    if not matches:
        raise SystemExit(
            f"[错误] 找不到匹配 {tokens} 的关节。\n"
            f"模型里的全部关节：\n  " + "\n  ".join(filter(None, all_names)) +
            "\n请对照上表修改 motions.py 里的关键词元组。"
        )
    raise SystemExit(
        f"[错误] 关键词 {tokens} 匹配到多个关节：{[m[1] for m in matches]}\n"
        f"请在 motions.py 里加更具体的关键词区分。"
    )


def build_joint_index(model):
    """预解析 MOTIONS 中出现的全部关节，返回 {tokens: qpos地址}。"""
    index = {}
    for motion in MOTIONS.values():
        for wp in motion["waypoints"]:
            for tokens in wp["pose"]:
                if tokens not in index:
                    jid = resolve_joint(model, tokens)
                    index[tokens] = model.jnt_qposadr[jid]
    return index


# ---------------- 路点插值 ----------------

def pose_at(motion, t, neutral=None):
    """给定时间t，返回线性插值后的 {tokens: 角度(度)}。超出末尾则循环。

    neutral: {tokens: 自然站姿角度(度)}。路点里没写到的关节向自然站姿插值
    （而不是向0插值）——G1的自然站姿本身肘部是弯的，强行归0反而会让
    手腕撞到髋部。
    """
    neutral = neutral or {}
    wps = motion["waypoints"]
    total = wps[-1]["t"]
    t = t % total if total > 0 else 0.0

    def val(wp, kk):
        return wp["pose"].get(kk, neutral.get(kk, 0.0))

    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"]
            k = (t - a["t"]) / span if span > 0 else 1.0
            keys = set(a["pose"]) | set(b["pose"])
            return {kk: val(a, kk) * (1 - k) + val(b, kk) * k for kk in keys}
    return dict(wps[-1]["pose"])


# ---------------- 自碰撞检查 ----------------

def robot_self_contacts(model, data):
    """返回机器人自身部件之间的接触对（排除与地面的接触）。"""
    pairs = []
    for i in range(data.ncon):
        c = data.contact[i]
        g1name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, c.geom1) or f"geom{c.geom1}"
        g2name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, c.geom2) or f"geom{c.geom2}"
        if "floor" in g1name.lower() or "floor" in g2name.lower():
            continue
        if "ground" in g1name.lower() or "ground" in g2name.lower():
            continue
        pairs.append((g1name, g2name))
    return pairs


# ---------------- 主程序 ----------------

def main():
    model = mujoco.MjModel.from_xml_path(MODEL_PATH)
    data = mujoco.MjData(model)

    # 有keyframe就用keyframe（通常是标准站姿），否则用默认姿态
    if model.nkey > 0:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    else:
        mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)

    # 冻结浮动基座：把根部自由关节的位姿固定在初始站姿，机器人不会倒
    base_qpos = None
    if model.njnt > 0 and model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE:
        base_qpos = data.qpos[0:7].copy()

    joint_addr = build_joint_index(model)
    neutral_qpos = data.qpos.copy()
    # 各动作关节在自然站姿（keyframe）下的角度，作为路点未覆盖时的插值目标
    neutral_deg = {tokens: math.degrees(neutral_qpos[adr])
                   for tokens, adr in joint_addr.items()}

    motion_keys = list(MOTIONS.keys())
    SPEEDS = [0.25, 0.5, 1.0, 2.0]
    state = {"idx": 0, "paused": False, "sim_t": 0.0, "last": time.time(),
             "warned": set(), "loop": False, "done": False, "speed": 1.0}

    def key_cb(keycode):
        ch = chr(keycode) if 0 <= keycode < 256 else ""
        if ch == " ":
            state["paused"] = not state["paused"]
            print("▶ 已暂停" if state["paused"] else "▶ 继续")
        elif ch in ("r", "R"):
            state["sim_t"] = 0.0
            state["done"] = False
        elif ch in ("l", "L"):
            state["loop"] = not state["loop"]
            state["done"] = False
            state["sim_t"] = 0.0
            print("▶ 播放模式:", "循环" if state["loop"] else "单次（播完按R重播）")
        elif ch in ("-", "="):   # - 减速 / = 加速（0.25/0.5/1/2倍）
            i = SPEEDS.index(state["speed"])
            i = max(0, i - 1) if ch == "-" else min(len(SPEEDS) - 1, i + 1)
            state["speed"] = SPEEDS[i]
            print(f"▶ 播放速度: {state['speed']}x")
        elif keycode == 263:   # ← 后退5秒
            state["sim_t"] = max(0.0, state["sim_t"] - 5.0)
            state["done"] = False
            print(f"▶ 后退5秒 → {state['sim_t']:.1f}s")
        elif keycode == 262:   # → 前进5秒
            state["sim_t"] += 5.0
            state["done"] = False
            print(f"▶ 前进5秒 → {state['sim_t']:.1f}s")
        elif ch.isdigit():
            i = int(ch) - 1
            if 0 <= i < len(motion_keys):
                state["idx"] = i
                state["sim_t"] = 0.0
                state["warned"].clear()
                state["done"] = False
                print(f"\n▶ 切换到动作 {motion_keys[i]}: "
                      f"{MOTIONS[motion_keys[i]]['description']}")

    print("已加载模型:", MODEL_PATH)
    print("按数字键1-{}切换动作 / R重播 / L切换单次·循环 / -减速 =加速 / ←后退5秒 →前进5秒 / 空格暂停 / ESC退出".format(len(motion_keys)))
    print("当前为单次播放模式：每节播完一遍自动停在结束姿态")
    print(f"\n▶ 当前动作 {motion_keys[0]}: {MOTIONS[motion_keys[0]]['description']}")

    with mujoco.viewer.launch_passive(model, data, key_callback=key_cb) as viewer:
        while viewer.is_running():
            now = time.time()
            if not state["paused"]:
                state["sim_t"] += (now - state["last"]) * state["speed"]
            state["last"] = now
            sim_t = state["sim_t"]

            motion = MOTIONS[motion_keys[state["idx"]]]
            total = motion["waypoints"][-1]["t"]
            if not state["loop"] and sim_t >= total:
                sim_t = total - 1e-6   # 单次模式：停在结束姿态，不循环
                if not state["done"]:
                    state["done"] = True
                    print(f"✔ 本节播放完毕（{total:.1f}s）。按 R 重播，按 1-{len(motion_keys)} 换节，按 L 切循环。")
            target = pose_at(motion, sim_t, neutral_deg)

            # 回到自然站姿再叠加当前动作角度（没写到的关节保持自然站姿）
            data.qpos[:] = neutral_qpos
            for tokens, deg in target.items():
                data.qpos[joint_addr[tokens]] = math.radians(deg)
            if base_qpos is not None:
                data.qpos[0:7] = base_qpos

            mujoco.mj_forward(model, data)

            # 自碰撞提示（同一对只提示一次，避免刷屏）
            for pair in robot_self_contacts(model, data):
                if pair not in state["warned"]:
                    state["warned"].add(pair)
                    print(f"⚠ 自碰撞: {pair[0]} <-> {pair[1]} "
                          f"(动作 {motion_keys[state['idx']]}，t≈{sim_t:.1f}s) — 请调小相关角度")

            viewer.sync()
            time.sleep(1 / 60)


if __name__ == "__main__":
    main()
