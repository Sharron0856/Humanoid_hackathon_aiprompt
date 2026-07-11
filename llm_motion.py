"""
用通义千问（阿里云国际版 Model Studio）把自然语言指令翻译成 G1 动作路点。

工作方式：
    用户说「挥挥右手」→ 千问按系统提示词里的关节约定生成 JSON 路点
    → 本模块做安全校验（关节白名单 + 限位裁剪 + 时间轴检查）
    → 转成 motions.py 同款格式（tuple关节键 + 度数），交给查看器播放。

配置（三选一，优先级从高到低）：
    1. 环境变量 DASHSCOPE_API_KEY
    2. 直接改下面的 API_KEY 常量
    模型默认 qwen3-max（国际版最新旗舰），可用环境变量 QWEN_MODEL 覆盖。

依赖：pip install openai   （走 DashScope 的 OpenAI 兼容接口，无需 dashscope SDK）
"""
import json
import os
import re

# ★ 通过环境变量 DASHSCOPE_API_KEY 提供 key；不要把密钥硬编码进源码。
# 本地想图省事也可临时填在这里，但切勿提交到 git（会公开泄露）。
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"   # 国际版端点
MODEL = os.environ.get("QWEN_MODEL", "qwen3-max")

# ---------------- 关节白名单与限位（度）----------------
# 与 motions.py 的经验值一致：LLM 给出的角度超限会被裁剪并打印警告，
# 从源头保证生成的动作不会顶着关节硬限位跑。
JOINT_LIMITS = {
    ("left", "shoulder", "pitch"): (-177, 153),
    ("right", "shoulder", "pitch"): (-177, 153),
    ("left", "shoulder", "roll"): (-15, 129),    # 左臂 roll 正=向外张开
    ("right", "shoulder", "roll"): (-129, 15),   # 右臂与左臂镜像
    ("left", "shoulder", "yaw"): (-90, 90),
    ("right", "shoulder", "yaw"): (-90, 90),
    ("left", "elbow"): (-60, 120),
    ("right", "elbow"): (-60, 120),
    ("left", "wrist", "roll"): (-90, 90),
    ("right", "wrist", "roll"): (-90, 90),
    ("left", "wrist", "pitch"): (-60, 60),
    ("right", "wrist", "pitch"): (-60, 60),
    ("left", "wrist", "yaw"): (-60, 60),
    ("right", "wrist", "yaw"): (-60, 60),
    ("waist", "yaw"): (-150, 150),
    ("waist", "pitch"): (-30, 30),               # 正=前弯，负=后仰
    ("waist", "roll"): (-30, 30),                # 正=向左侧弯
}

MAX_DURATION = 60.0    # 单个生成动作最长时长（秒），防止 LLM 生成超长序列
MAX_WAYPOINTS = 80

_JOINT_KEYS_DOC = "\n".join(
    f'  - "{".".join(k)}"  范围 [{lo}, {hi}]' for k, (lo, hi) in JOINT_LIMITS.items()
)

SYSTEM_PROMPT = f"""你是宇树 G1 人形机器人的动作编排师。用户用自然语言描述动作，你输出关节路点 JSON，机器人会线性插值逐帧执行。

## 输出格式（只输出 JSON，不要任何其他文字）
{{
  "name": "英文短名，如 wave_right_hand",
  "description": "动作的中文一句话描述",
  "waypoints": [
    {{"t": 0.0, "pose": {{}}}},
    {{"t": 1.5, "pose": {{"right.shoulder.pitch": -120, "right.elbow": 60}}}},
    {{"t": 3.0, "pose": {{}}}}
  ]
}}
规则：t 单位秒、必须从 0 开始严格递增；pose 是"关节名: 角度(度)"；空 pose {{}} 表示整体回到自然站姿；pose 里没写的关节自动回自然站姿。

## 可用关节（只能用这些名字）
{_JOINT_KEYS_DOC}

## G1 关节方向约定（务必遵守，方向错了动作就反了）
- shoulder.pitch：负=手臂向前上方抬起（-90 前平举，-165 举过头顶），正=向后摆。
- shoulder.roll：左臂正=向外侧张开（85 侧平举，129 极限），右臂取相反符号（镜像）。
- elbow：★特殊约定★ 85 才是手臂伸直，0 是屈肘约80度，120 是过伸。想要直臂就写 85。
- waist.yaw：正=向左转体；waist.pitch：正=前弯腰，负=后仰；waist.roll：正=向左侧弯腰。
- wrist.roll/pitch/yaw：手腕小幅动作（如挥手时摆腕），建议 ±45 以内。

## 已验证无自碰撞的姿态（尽量在这些基础上改，别凭空发明极限姿态）
- 前平举: {{"left.shoulder.pitch": -90, "right.shoulder.pitch": -90, "left.shoulder.roll": 15, "right.shoulder.roll": -15, "left.elbow": 85, "right.elbow": 85}}
- 举过头顶: pitch=-165, roll=±25, elbow=85（roll 绝对值小于 25 会撞到躯干！）
- 侧平举: pitch=-10, roll=±85, elbow=85
- 屈肘收胸前: pitch=-30, roll=±15, elbow=0
- 扩胸后仰: pitch=-120, roll=±55, elbow=85, waist.pitch=-15
- 叉腰(近似): pitch=10, roll=±35, elbow=0

## 编排要领
- 单侧动作只写单侧关节（如挥右手就别动左臂）。
- 相邻路点间隔一般 0.5~2 秒：太快显得抽搐，太慢显得迟钝。挥手等往复动作重复 2~4 次。
- 动作结尾建议回到自然站姿（空 pose），除非用户要求保持姿势。
- 用户如果在追加修改上一个动作（如"再举高一点""慢一点"），在上一个 JSON 基础上改，保持整体结构。

## 两类做不到的事（务必区分，遇到时用替代方案，并在 description 里注明"近似/省略了什么"）

### A. 仿真物理边界（运动学回放决定，任何角度都绕不过，不要尝试）
- 机器人基座被固定在原地：不能跳跃、不能行走、不能踏步、不能移动重心。
- 腿部关节不开放：不能屈膝、下蹲、踢腿、抬腿。
- 头/颈不可动，手指不可动。
- 遇到这类要求：省略腿部/移动部分，只编排上肢+腰的对应节奏动作，别输出不存在的关节名。

### B. G1 关节边界（限位决定，用下面的验证过的近似姿态，别硬顶限位）
- 深前弯（指尖碰地）做不到：腰 pitch 上限 30 → 用 waist.pitch=28 加双臂前下伸（shoulder.pitch=-60）近似。
- 大幅后仰做不到：waist.pitch 取 -15 左右就是安全的"胸反らし"，不要低于 -25。
- 侧弯幅度有限：waist.roll 取 ±22 左右，必须配合【对侧】手臂举过头，同侧举会撞头：
  向左弯 waist.roll=+22 → 举右臂（right.shoulder.pitch=-150, right.shoulder.roll=-25, right.elbow=85）；
  向右弯 waist.roll=-22 → 举左臂（left.shoulder.pitch=-150, left.shoulder.roll=+25, left.elbow=85）。
- 手臂连续画圈没有圆轨迹指令 → 每圈用 4 个路点近似：前平举 → 举过头顶 → 侧平举 → 自然下垂。
- 举过头顶时 shoulder.roll 绝对值必须 ≥25（否则撞躯干）；shoulder.pitch 别低于 -165。

## ラジオ体操第一 可行性对照表（用户要做广播体操时按此编排，每节约 12~13 秒，节奏均匀）
① 伸びの運動：✔可做。前平举→举过头顶→保持→放下，×2遍。
② 腕を振って脚を曲げ伸ばす：△屈膝做不了 → 只做双臂"前平举↔后摆(pitch=35, roll=±12, elbow=75)"往复×4。
③ 腕を回す：△画圈用4路点近似（见上），×4圈。
④ 胸を反らす：✔可做。前平举→扩胸后仰（pitch=-120, roll=±55, elbow=85, waist.pitch=-15）→回正，×2。
⑤ 体を横に曲げる：✔可做。waist.roll=±22 + 对侧手臂过头，左右交替×4。
⑥ 体を前後に曲げる：△前弯用近似（waist.pitch=28）；弯2次回弹的节奏可用 28→15→28 表现；后仰同④，×2。
⑦ 体をねじる：✔可做。waist.yaw=±45，手臂微张（roll=±30, elbow=80）随动，左右交替×4。
⑧ 腕を上下に伸ばす：✔可做。屈肘收胸前（elbow=0）↔向上冲举（pitch=-165, roll=±25, elbow=85），×4。
⑨ 体を斜め下に曲げ胸を反らす：△斜前弯 = waist.pitch=25 + waist.yaw=±25 + 双臂斜下伸，与胸反らし交替。
⑩ 体を回す：△躯干画圈用路点近似：waist 依次 pitch=22 → roll=22 → pitch=-18 → roll=-22，双臂上举（pitch=-120, roll=±30）随动，左右各1圈。
⑪ 両脚でとぶ：✘跳不了 → 只做节奏摆臂：侧上举（roll=±115, elbow=85）↔放下，×8，每次约0.7秒。
⑫ 同②。
⑬ 深呼吸：✔可做。双臂经侧方缓慢举起（roll=±100, elbow=85）再缓慢放下，节奏放到3秒一拍，×2。
"""


def _get_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("未安装 openai SDK，请先运行: pip install openai")
    key = os.environ.get("DASHSCOPE_API_KEY", "") or API_KEY
    if not key:
        raise SystemExit(
            "未配置 API key。请设置环境变量 DASHSCOPE_API_KEY，\n"
            "或直接填写 llm_motion.py 顶部的 API_KEY 常量。\n"
            "（阿里云国际版控制台 Model Studio → API-KEY 处获取）"
        )
    return OpenAI(api_key=key, base_url=BASE_URL)


def _extract_json(text):
    """LLM 偶尔会包 ```json 围栏或夹带说明文字，这里稳健地抠出 JSON 对象。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text


def validate_motion(raw, max_wps=MAX_WAYPOINTS, max_dur=MAX_DURATION):
    """
    校验并转换 LLM 输出：
      - 关节名必须在白名单里（"a.b.c" 字符串 → tuple 键）
      - 角度裁剪到限位，越限打印警告
      - 时间轴必须从 0 开始严格递增，总长/路点数封顶
    返回 motions.py 同款的 motion dict；不合法直接抛 ValueError。
    max_wps/max_dur 可放宽，用于加载拼接好的长序列文件（如 ai_taiso.json）。
    """
    if not isinstance(raw, dict) or "waypoints" not in raw:
        raise ValueError("缺少 waypoints 字段")
    wps_in = raw["waypoints"]
    if not isinstance(wps_in, list) or not wps_in:
        raise ValueError("waypoints 必须是非空列表")
    if len(wps_in) > max_wps:
        raise ValueError(f"路点过多（{len(wps_in)} > {max_wps}）")

    waypoints, prev_t = [], -1.0
    for i, wp in enumerate(wps_in):
        t = float(wp.get("t", -1))
        if i == 0 and t != 0.0:
            # 首帧不从0开始就补一帧自然站姿，而不是直接拒绝
            waypoints.append({"t": 0.0, "pose": {}})
            prev_t = 0.0
        if t <= prev_t:
            raise ValueError(f"第{i}个路点时间 t={t} 未递增")
        if t > max_dur:
            raise ValueError(f"动作超长（t={t}s > {max_dur}s 上限）")
        pose_in = wp.get("pose", {})
        if not isinstance(pose_in, dict):
            raise ValueError(f"第{i}个路点的 pose 不是对象")
        pose = {}
        for key, deg in pose_in.items():
            tokens = tuple(key.strip().lower().split("."))
            if tokens not in JOINT_LIMITS:
                raise ValueError(
                    f"未知关节 \"{key}\"。可用关节：\n{_JOINT_KEYS_DOC}")
            lo, hi = JOINT_LIMITS[tokens]
            deg = float(deg)
            if not (lo <= deg <= hi):
                clamped = max(lo, min(hi, deg))
                print(f"  ⚠ {key}={deg}° 超出限位[{lo},{hi}]，已裁剪为 {clamped}°")
                deg = clamped
            pose[tokens] = deg
        waypoints.append({"t": round(t, 3), "pose": pose})
        prev_t = t

    return {
        "name": str(raw.get("name", "llm_motion")),
        "description": str(raw.get("description", "AI生成动作")),
        "waypoints": waypoints,
    }


class MotionAgent:
    """带多轮上下文的动作生成器：支持「再快一点」这类追加修改。"""

    def __init__(self, max_history=8):
        self.client = _get_client()
        self.history = []          # [{"role": "user"/"assistant", "content": str}, ...]
        self.max_history = max_history

    def generate(self, instruction):
        """自然语言指令 → 校验后的 motion dict。校验失败会带错误反馈自动重试一次。"""
        messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                    + self.history
                    + [{"role": "user", "content": instruction}])
        last_err = None
        for attempt in range(2):
            resp = self.client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0.3)
            text = resp.choices[0].message.content
            try:
                motion = validate_motion(json.loads(_extract_json(text)))
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
                # 把错误喂回去让模型自己修一次
                messages += [{"role": "assistant", "content": text},
                             {"role": "user",
                              "content": f"你的输出有问题：{e}。请修正后重新只输出 JSON。"}]
                continue
            # 成功：记入对话历史（存修正后的规范 JSON，便于后续追加修改）
            canonical = json.dumps(
                {"name": motion["name"], "description": motion["description"],
                 "waypoints": [{"t": w["t"],
                                "pose": {".".join(k): v for k, v in w["pose"].items()}}
                               for w in motion["waypoints"]]},
                ensure_ascii=False)
            self.history += [{"role": "user", "content": instruction},
                             {"role": "assistant", "content": canonical}]
            self.history = self.history[-self.max_history:]
            return motion
        raise ValueError(f"模型两次输出均不合法，最后错误：{last_err}")

    def reset(self):
        self.history.clear()
