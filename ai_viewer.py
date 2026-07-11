"""
G1 AI 语音指挥官（文字版）：控制台输入自然语言 → 千问生成动作 → 仿真实时播放。

用法：
    1. pip install mujoco openai
    2. 配置 key（见 llm_motion.py 顶部说明）
    3. python ai_viewer.py

控制台指令（在终端里输入，回车发送）：
    任意中文/英文描述   → AI 生成新动作并立即播放，如「挥挥右手」「做个扩胸运动」
    追加修改也可以      → 如「再快一点」「手举得更高」
    p1 ~ p5            → 播放 motions.py 里的预设动作（广播体操）
    r                  → 重播当前动作
    new                → 清空对话上下文（下一条指令从全新动作开始）
    q                  → 退出

查看器窗口按键（先点窗口获得焦点）：空格=暂停/继续，R=重播。

架构说明：LLM 请求在后台线程执行，主线程跑 mujoco 渲染循环不被阻塞；
生成的动作经 llm_motion.validate_motion 做关节白名单+限位裁剪后才会执行。
每个动作开始前会用千问 TTS 播报日语动作名；完整广播体操还会逐节播报。
"""
import math
import queue
import sys
import threading
import time

# Windows 控制台默认编码可能打不出中文/emoji，统一切到 UTF-8（打不出的字符降级替换）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    import mujoco
    import mujoco.viewer
except ImportError:
    raise SystemExit("未安装mujoco，请先运行: pip install mujoco")

import json
import os

from motions import MOTIONS
from sim_viewer import MODEL_PATH, pose_at, resolve_joint, robot_self_contacts
from llm_motion import JOINT_LIMITS, MODEL, MotionAgent, validate_motion

try:
    from tts_qwen import speak as tts_speak
except Exception as e:
    print(f"⚠ 语音模块加载失败，将继续无声播放: {e}")

    def tts_speak(_text):
        pass


AI_TAISO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_taiso.json")

# 动作开始前先留出站姿时间进行日语播报。
ANNOUNCE_LEAD = 2.0

JAPANESE_ANNOUNCEMENTS = {
    "1_nobi_stretch": "伸びの運動を始めます。",
    "2_torso_twist": "体をねじる運動を始めます。",
    "3_updown_thrust": "腕を上下に伸ばす運動を始めます。",
    "4_deep_breath": "深呼吸の運動を始めます。",
    "5_daiichi_full": "ラジオ体操第一を始めます。",
    "ai_taiso_daiichi": "ラジオ体操第一を始めます。",
}

# motions.py 中手工定稿完整序列的各节起始时间。
DAIICHI_SECTION_ANNOUNCEMENTS = [
    (13.0, "一、伸びの運動。"),
    (25.5, "二、腕を振って脚を曲げ伸ばす運動。"),
    (37.5, "三、腕を回す運動。"),
    (49.5, "四、胸を反らす運動。"),
    (62.0, "五、体を横に曲げる運動。"),
    (75.0, "六、体を前後に曲げる運動。"),
    (88.0, "七、体をねじる運動。"),
    (100.0, "八、腕を上下に伸ばす運動。"),
    (110.0, "九、体を斜め下に曲げ、胸を反らす運動。"),
    (123.0, "十、体を回す運動。"),
    (135.5, "十一、両脚で跳ぶ運動。"),
    (147.5, "十二、腕を振って脚を曲げ伸ばす運動。"),
    (159.5, "十三、深呼吸の運動。"),
]


def japanese_announcement(motion):
    """既知预设播报具体日语名；AI 临时生成动作使用日语通用提示。"""
    return JAPANESE_ANNOUNCEMENTS.get(
        motion.get("name"), "新しい動作を始めます。")


def load_presets():
    """motions.py 的预设 + ai_taiso.json（若存在，由 make_taiso.py 生成）。"""
    presets = {k: {"name": k, "description": m["description"],
                   "waypoints": m["waypoints"]} for k, m in MOTIONS.items()}
    if os.path.exists(AI_TAISO_PATH):
        try:
            with open(AI_TAISO_PATH, encoding="utf-8") as f:
                m = validate_motion(json.load(f), max_wps=100000, max_dur=3600)
            presets[m["name"]] = m
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠ ai_taiso.json 加载失败，已跳过: {e}")
    return presets


def build_joint_index(model):
    """解析白名单里的全部关节 + 预设动作用到的关节 → {tokens: qpos地址}。"""
    index = {}
    tokens_needed = set(JOINT_LIMITS)
    for motion in load_presets().values():
        for wp in motion["waypoints"]:
            tokens_needed.update(wp["pose"])
    for tokens in sorted(tokens_needed):
        index[tokens] = model.jnt_qposadr[resolve_joint(model, tokens)]
    return index


def console_worker(agent, motion_q, commands, stop_evt, initial=None):
    """后台线程：读终端输入。LLM 调用在这里做，不卡渲染主循环。

    initial: 启动时自动执行的第一条指令（来自命令行参数），执行完继续读终端。
    """
    presets = load_presets()
    preset_keys = list(presets.keys())
    print(f"\n💬 模型: {MODEL} ｜ 输入动作描述并回车（q退出 / r重播 / new新对话 / p1~p{len(preset_keys)}预设）")
    for i, k in enumerate(preset_keys, 1):
        print(f"   p{i} = {presets[k]['description']}")
    pending = initial
    while not stop_evt.is_set():
        if pending:
            text, pending = pending.strip(), None
            print(f"🤖 > {text}   ←(启动参数自动执行)")
        else:
            try:
                text = input("🤖 > ").strip()
            except (EOFError, KeyboardInterrupt):
                commands.put("quit")
                return
        if not text:
            continue
        low = text.lower()
        if low == "q":
            commands.put("quit")
            return
        if low == "r":
            commands.put("replay")
            continue
        if low == "new":
            agent.reset()
            print("✔ 已清空对话上下文")
            continue
        if low.startswith("p") and low[1:].isdigit():
            i = int(low[1:]) - 1
            if 0 <= i < len(preset_keys):
                motion_q.put(dict(presets[preset_keys[i]]))
            else:
                print(f"没有预设 p{low[1:]}（可用 p1~p{len(preset_keys)}）")
            continue
        # 其余一律当自然语言指令交给千问
        print("… 正在请求千问生成动作 …")
        t0 = time.time()
        try:
            motion = agent.generate(text)
        except Exception as e:
            print(f"✘ 生成失败: {e}")
            continue
        total = motion["waypoints"][-1]["t"]
        print(f"✔ [{motion['name']}] {motion['description']} "
              f"（{len(motion['waypoints'])}个路点，{total:.1f}s，耗时{time.time() - t0:.1f}s）")
        motion_q.put(motion)


def main():
    model = mujoco.MjModel.from_xml_path(MODEL_PATH)
    data = mujoco.MjData(model)
    if model.nkey > 0:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    else:
        mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)

    base_qpos = None
    if model.njnt > 0 and model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE:
        base_qpos = data.qpos[0:7].copy()

    joint_addr = build_joint_index(model)
    neutral_qpos = data.qpos.copy()
    neutral_deg = {tokens: math.degrees(neutral_qpos[adr])
                   for tokens, adr in joint_addr.items()}

    agent = MotionAgent()
    motion_q, commands = queue.Queue(), queue.Queue()
    stop_evt = threading.Event()
    initial = " ".join(sys.argv[1:]).strip() or None   # py ai_viewer.py 做个体操
    threading.Thread(target=console_worker,
                     args=(agent, motion_q, commands, stop_evt, initial),
                     daemon=True).start()

    state = {"motion": None, "sim_t": 0.0, "last": time.time(),
             "paused": False, "done": False, "warned": set(),
             "section_announced": set()}

    def restart_current():
        """回到站姿，先日语播报，ANNOUNCE_LEAD 秒后开始动作。"""
        if state["motion"] is None:
            return
        state["sim_t"] = -ANNOUNCE_LEAD
        state["done"] = False
        state["warned"].clear()
        state["section_announced"].clear()
        tts_speak(japanese_announcement(state["motion"]))

    def key_cb(keycode):
        ch = chr(keycode) if 0 <= keycode < 256 else ""
        if ch == " ":
            state["paused"] = not state["paused"]
            print("⏸ 已暂停（空格继续）" if state["paused"] else "▶ 继续")
        elif ch in ("r", "R"):
            restart_current()
            print("▶ 从头重播")

    print("已加载模型:", MODEL_PATH)
    with mujoco.viewer.launch_passive(model, data, key_callback=key_cb) as viewer:
        while viewer.is_running():
            # 处理控制台命令
            try:
                while True:
                    cmd = commands.get_nowait()
                    if cmd == "quit":
                        return
                    if cmd == "replay":
                        restart_current()
            except queue.Empty:
                pass
            # 新动作到达：立即切换播放
            try:
                state["motion"] = motion_q.get_nowait()
                restart_current()
                print(f"▶ 播放: {state['motion']['description']}")
            except queue.Empty:
                pass

            now = time.time()
            if not state["paused"]:
                state["sim_t"] += now - state["last"]
            state["last"] = now

            data.qpos[:] = neutral_qpos
            motion = state["motion"]
            if motion is not None:
                total = motion["waypoints"][-1]["t"]
                if state["sim_t"] >= 0.0:
                    sim_t = min(state["sim_t"], total - 1e-6)   # 单次播放，停在结束姿态
                    if state["sim_t"] >= total and not state["done"]:
                        state["done"] = True
                        print(f"✔ 播放完毕（{total:.1f}s）。继续输入指令，或 r 重播。")

                    # 完整定稿序列：在每节开始前 ANNOUNCE_LEAD 秒播报日语节名。
                    if motion.get("name") == "5_daiichi_full":
                        for section_i, (start_t, text) in enumerate(
                                DAIICHI_SECTION_ANNOUNCEMENTS):
                            if (sim_t >= max(0.0, start_t - ANNOUNCE_LEAD)
                                    and section_i not in state["section_announced"]):
                                state["section_announced"].add(section_i)
                                tts_speak(text)

                    for tokens, deg in pose_at(motion, sim_t, neutral_deg).items():
                        data.qpos[joint_addr[tokens]] = math.radians(deg)
            if base_qpos is not None:
                data.qpos[0:7] = base_qpos

            mujoco.mj_forward(model, data)

            for pair in robot_self_contacts(model, data):
                if pair not in state["warned"]:
                    state["warned"].add(pair)
                    print(f"⚠ 自碰撞: {pair[0]} <-> {pair[1]} (t≈{state['sim_t']:.1f}s) "
                          f"— 可以直接告诉AI「刚才的动作有自碰撞，调整一下」")

            viewer.sync()
            time.sleep(1 / 60)
    stop_evt.set()


if __name__ == "__main__":
    main()
