"""Unitree G1 真机上肢执行器。

这个模块刻意把“动作规划”和“DDS 真机发送”分开：

* 默认只做 dry-run，不导入 SDK，也不会发送任何指令。
* 真机模式只使用官方 ``rt/lowstate`` + ``rt/arm_sdk`` 链路。
* 首次状态、消息看门狗、速度限制、跟踪误差和 arm_sdk 权重释放均为硬门槛。
* 23DOF/29DOF 必须由操作者明确指定；有歧义的腰 roll/pitch 默认禁用。

动作格式与 motions.py / llm_motion.py 相同：角度单位为度，路点间线性插值。
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from html import escape
from ipaddress import ip_address

from llm_motion import JOINT_LIMITS, validate_motion


Token = tuple[str, ...]


# 官方 unitree_sdk2_python G1JointIndex。23DOF 仍保留同一消息索引，
# 但腕 pitch/yaw 不存在；腰 roll/pitch 还取决于具体腰部硬件配置。
G1_JOINT_INDEX: dict[Token, int] = {
    ("waist", "yaw"): 12,
    ("waist", "roll"): 13,
    ("waist", "pitch"): 14,
    ("left", "shoulder", "pitch"): 15,
    ("left", "shoulder", "roll"): 16,
    ("left", "shoulder", "yaw"): 17,
    ("left", "elbow"): 18,
    ("left", "wrist", "roll"): 19,
    ("left", "wrist", "pitch"): 20,
    ("left", "wrist", "yaw"): 21,
    ("right", "shoulder", "pitch"): 22,
    ("right", "shoulder", "roll"): 23,
    ("right", "shoulder", "yaw"): 24,
    ("right", "elbow"): 25,
    ("right", "wrist", "roll"): 26,
    ("right", "wrist", "pitch"): 27,
    ("right", "wrist", "yaw"): 28,
}

ARM_SDK_WEIGHT_INDEX = 29
_WRIST_7DOF_ONLY = {
    ("left", "wrist", "pitch"), ("left", "wrist", "yaw"),
    ("right", "wrist", "pitch"), ("right", "wrist", "yaw"),
}
_WAIST_ROLL_PITCH = {("waist", "roll"), ("waist", "pitch")}


class RealRobotError(RuntimeError):
    """真机规划、连接或安全检查失败。"""


@dataclass(frozen=True)
class SafetyConfig:
    control_hz: float = 50.0
    max_speed_rad_s: float = math.radians(20.0)
    min_approach_s: float = 3.0
    state_timeout_s: float = 0.30
    first_state_timeout_s: float = 5.0
    max_tracking_error_rad: float = math.radians(35.0)
    tracking_grace_s: float = 2.0
    weight_ramp_s: float = 2.0
    release_s: float = 1.5
    kp: float = 40.0
    kd: float = 1.0
    wrist_kp: float = 20.0
    wrist_kd: float = 1.0

    def __post_init__(self):
        if not (10.0 <= self.control_hz <= 100.0):
            raise ValueError("control_hz 必须在 10~100Hz")
        if not (0 < self.max_speed_rad_s <= math.radians(45.0)):
            raise ValueError("首次真机速度上限必须在 (0,45] deg/s")
        if not (0 < self.kp <= 60.0 and 0 < self.kd <= 2.0
                and 0 < self.wrist_kp <= 40.0 and 0 < self.wrist_kd <= 2.0):
            raise ValueError("kp/kd 超出本项目允许的保守范围")


@dataclass
class PreparedMotion:
    name: str
    description: str
    waypoints: list[dict]
    controlled_tokens: tuple[Token, ...]

    @property
    def duration(self) -> float:
        return float(self.waypoints[-1]["t"])


def available_tokens(robot_dof: int, allow_waist_roll_pitch: bool = False) -> set[Token]:
    if robot_dof not in (23, 29):
        raise RealRobotError("robot_dof 必须明确指定为 23 或 29")
    result = set(G1_JOINT_INDEX)
    if robot_dof == 23:
        result -= _WRIST_7DOF_ONLY
    if not allow_waist_roll_pitch:
        result -= _WAIST_ROLL_PITCH
    return result


def _all_motion_tokens(motion: dict) -> set[Token]:
    return {tokens for wp in motion["waypoints"] for tokens in wp["pose"]}


def validate_for_robot(
    motion: dict,
    robot_dof: int,
    allow_waist_roll_pitch: bool = False,
) -> tuple[Token, ...]:
    """拒绝真机不存在或未显式启用的关节。"""
    allowed = available_tokens(robot_dof, allow_waist_roll_pitch)
    used = _all_motion_tokens(motion)
    unsupported = sorted(used - allowed)
    if unsupported:
        names = ", ".join(".".join(x) for x in unsupported)
        raise RealRobotError(
            f"动作包含当前 G1 配置未启用的关节: {names}。"
            "不会静默省略，以免动作语义改变或破坏平衡。"
        )
    # arm_sdk 每周期保持所有已允许上肢关节在首次读到的姿态；这样未写入的
    # 关节不会意外回到 0。腰部仅在动作明确使用时才接管。
    arm_tokens = {t for t in allowed if t[0] in ("left", "right")}
    controlled = arm_tokens | {t for t in used if t[0] == "waist"}
    return tuple(sorted(controlled, key=lambda t: G1_JOINT_INDEX[t]))


def pose_at(motion: dict, t: float, neutral_deg: dict[Token, float]) -> dict[Token, float]:
    """纯 Python 路点插值；不依赖 MuJoCo，可在机器人计算单元运行。"""
    wps = motion["waypoints"]
    if t <= wps[0]["t"]:
        a = b = wps[0]
        ratio = 0.0
    elif t >= wps[-1]["t"]:
        a = b = wps[-1]
        ratio = 0.0
    else:
        for i in range(len(wps) - 1):
            a, b = wps[i], wps[i + 1]
            if a["t"] <= t <= b["t"]:
                span = b["t"] - a["t"]
                ratio = (t - a["t"]) / span if span > 0 else 1.0
                break

    result = {}
    for tokens in neutral_deg:
        av = a["pose"].get(tokens, neutral_deg[tokens])
        bv = b["pose"].get(tokens, neutral_deg[tokens])
        result[tokens] = av * (1.0 - ratio) + bv * ratio
    return result


def prepare_motion(
    motion: dict,
    robot_dof: int,
    neutral_deg: dict[Token, float],
    safety: SafetyConfig,
    allow_waist_roll_pitch: bool = False,
) -> PreparedMotion:
    """按最大关节速度拉长时间轴，不改变路点形状。"""
    controlled = validate_for_robot(motion, robot_dof, allow_waist_roll_pitch)
    missing = [t for t in controlled if t not in neutral_deg]
    if missing:
        raise RealRobotError(f"首次状态缺少关节: {missing}")

    source = motion["waypoints"]
    stretched = [{"t": 0.0, "pose": dict(source[0]["pose"])}]
    elapsed = 0.0
    max_speed_deg_s = math.degrees(safety.max_speed_rad_s)
    for i in range(len(source) - 1):
        a, b = source[i], source[i + 1]
        nominal_dt = float(b["t"] - a["t"])
        if nominal_dt <= 0:
            raise RealRobotError("动作时间轴不是严格递增")
        pa = {t: a["pose"].get(t, neutral_deg[t]) for t in controlled}
        pb = {t: b["pose"].get(t, neutral_deg[t]) for t in controlled}
        required_dt = max((abs(pb[t] - pa[t]) / max_speed_deg_s for t in controlled),
                          default=0.0)
        elapsed += max(nominal_dt, required_dt)
        stretched.append({"t": round(elapsed, 4), "pose": dict(b["pose"])})

    return PreparedMotion(
        name=str(motion.get("name", "motion")),
        description=str(motion.get("description", "")),
        waypoints=stretched,
        controlled_tokens=controlled,
    )


class G1ArmSdkExecutor:
    """官方 DDS arm_sdk 的保守执行封装。实例化不会发送指令。"""

    def __init__(
        self,
        interface: str,
        robot_dof: int,
        safety: SafetyConfig | None = None,
        allow_waist_roll_pitch: bool = False,
    ):
        self.interface = interface
        self.robot_dof = robot_dof
        self.safety = safety or SafetyConfig()
        self.allow_waist_roll_pitch = allow_waist_roll_pitch
        available_tokens(robot_dof, allow_waist_roll_pitch)

        self._state = None
        self._state_at = 0.0
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = False
        self._sdk = None
        self._publisher = None
        self._subscriber = None
        self._low_cmd = None
        self._crc = None

    @staticmethod
    def sdk_available() -> bool:
        try:
            import unitree_sdk2py  # noqa: F401
            return True
        except ImportError:
            return False

    def connect_read_only(self):
        """初始化 DDS 并等待 lowstate；不创建/写入任何电机命令。"""
        try:
            import unitree_sdk2py.core.channel as sdk_channel
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
        except ImportError as e:
            raise RealRobotError(
                "未安装 unitree_sdk2py。请在受支持的 Python/Linux 环境安装官方 "
                "unitree_sdk2_python 后先运行只读状态检查。"
            ) from e

        if os.name == "nt":
            # 官方 1.0.1 wheel 的配置把日志写死为 /tmp/cdds.LOG，Windows 会在
            # Domain 初始化前失败。这里不改 site-packages，仅替换本进程中由
            # ChannelFactory 使用的 XML；IP 参数使用 address 选择器，避免日文
            # FriendlyName 在 CycloneDDS 中解析不一致。
            try:
                ip_address(self.interface)
                selector = f'address="{escape(self.interface)}"'
            except ValueError:
                selector = f'name="{escape(self.interface)}"'
            sdk_channel.ChannelConfigHasInterface = f'''<?xml version="1.0"?>
<CycloneDDS><Domain Id="any"><General><Interfaces>
<NetworkInterface {selector} priority="default" multicast="default"/>
</Interfaces></General></Domain></CycloneDDS>'''

        sdk_channel.ChannelFactoryInitialize(0, self.interface)
        subscriber = sdk_channel.ChannelSubscriber("rt/lowstate", LowState_)
        subscriber.Init(self._on_state, 10)
        self._subscriber = subscriber  # 保持 DataReader 生命周期覆盖整个执行阶段
        deadline = time.monotonic() + self.safety.first_state_timeout_s
        while time.monotonic() < deadline:
            if self._fresh_state() is not None:
                self._connected = True
                return
            time.sleep(0.05)
        raise RealRobotError(
            f"{self.safety.first_state_timeout_s:.1f}s 内未收到 rt/lowstate；"
            "检查网卡、DDS、防火墙和机器人控制模式。"
        )

    def _on_state(self, msg):
        with self._state_lock:
            self._state = msg
            self._state_at = time.monotonic()

    def _fresh_state(self):
        with self._state_lock:
            if self._state is None:
                return None
            if time.monotonic() - self._state_at > self.safety.state_timeout_s:
                return None
            return self._state

    def _ensure_writer(self):
        if not self._connected:
            raise RealRobotError("必须先通过 connect_read_only() 收到新鲜状态")
        if self._publisher is not None:
            return
        from unitree_sdk2py.core.channel import ChannelPublisher
        from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_
        from unitree_sdk2py.utils.crc import CRC

        self._low_cmd = unitree_hg_msg_dds__LowCmd_()
        self._crc = CRC()
        self._publisher = ChannelPublisher("rt/arm_sdk", LowCmd_)
        self._publisher.Init()

    def neutral_degrees(self, tokens: tuple[Token, ...]) -> dict[Token, float]:
        state = self._fresh_state()
        if state is None:
            raise RealRobotError("lowstate 已超时")
        return {
            t: math.degrees(float(state.motor_state[G1_JOINT_INDEX[t]].q))
            for t in tokens
        }

    def status_summary(self) -> dict:
        """返回只读硬件诊断字段，不暴露或修改 DDS 对象。"""
        state = self._fresh_state()
        if state is None:
            raise RealRobotError("lowstate 已超时")
        motors = {}
        for tokens, index in G1_JOINT_INDEX.items():
            motor = state.motor_state[index]
            temperature = motor.temperature
            if isinstance(temperature, (list, tuple)):
                temperature = tuple(int(v) for v in temperature)
            else:
                temperature = int(temperature)
            motors[tokens] = {
                "index": index,
                "mode": int(motor.mode),
                "q_deg": math.degrees(float(motor.q)),
                "temperature": temperature,
            }
        return {
            "mode_pr": int(state.mode_pr),
            "mode_machine": int(state.mode_machine),
            "motors": motors,
        }

    def stop(self):
        self._stop.set()

    def _write(self, target_rad: dict[Token, float], weight: float):
        state = self._fresh_state()
        if state is None:
            raise RealRobotError("rt/lowstate 看门狗超时，立即停止发送")
        cmd = self._low_cmd
        # 继承实体当前机构/机器模式；arm_sdk 只接管列出的上肢关节。
        cmd.mode_pr = state.mode_pr
        cmd.mode_machine = state.mode_machine
        cmd.motor_cmd[ARM_SDK_WEIGHT_INDEX].q = max(0.0, min(1.0, weight))
        for tokens, q in target_rad.items():
            motor = cmd.motor_cmd[G1_JOINT_INDEX[tokens]]
            motor.mode = 1
            motor.tau = 0.0
            motor.q = float(q)
            motor.dq = 0.0
            if "wrist" in tokens:
                motor.kp = self.safety.wrist_kp
                motor.kd = self.safety.wrist_kd
            else:
                motor.kp = self.safety.kp
                motor.kd = self.safety.kd
        cmd.crc = self._crc.Crc(cmd)
        self._publisher.Write(cmd)
        return state

    def _release(self, tokens: tuple[Token, ...]):
        """保持当前实测姿态并渐降 arm_sdk 权重。"""
        if self._publisher is None:
            return
        steps = max(1, int(self.safety.release_s * self.safety.control_hz))
        for i in range(steps):
            state = self._fresh_state()
            if state is None:
                break
            hold = {t: float(state.motor_state[G1_JOINT_INDEX[t]].q) for t in tokens}
            try:
                self._write(hold, 1.0 - (i + 1) / steps)
            except RealRobotError:
                break
            time.sleep(1.0 / self.safety.control_hz)

    def execute(self, motion: dict):
        """执行一条已校验动作；调用方必须完成现场人工确认。"""
        if not self._connected:
            raise RealRobotError("尚未完成只读连接")
        controlled = validate_for_robot(
            motion, self.robot_dof, self.allow_waist_roll_pitch)
        neutral = self.neutral_degrees(controlled)
        prepared = prepare_motion(
            motion, self.robot_dof, neutral, self.safety,
            self.allow_waist_roll_pitch,
        )
        self._ensure_writer()
        self._stop.clear()

        first_deg = pose_at(
            {"waypoints": prepared.waypoints}, 0.0, neutral)
        start_rad = {t: math.radians(neutral[t]) for t in controlled}
        first_rad = {t: math.radians(first_deg[t]) for t in controlled}
        max_delta = max((abs(first_rad[t] - start_rad[t]) for t in controlled), default=0.0)
        approach_s = max(
            self.safety.min_approach_s,
            max_delta / self.safety.max_speed_rad_s,
        )
        period = 1.0 / self.safety.control_hz

        try:
            # 阶段1：从实时姿态平滑进入动作首帧，同时渐增 arm_sdk 权重。
            stage_start = time.monotonic()
            while True:
                elapsed = time.monotonic() - stage_start
                ratio = min(1.0, elapsed / approach_s)
                target = {t: start_rad[t] * (1.0 - ratio) + first_rad[t] * ratio
                          for t in controlled}
                weight = min(1.0, elapsed / self.safety.weight_ramp_s)
                self._write(target, weight)
                if self._stop.is_set():
                    raise RealRobotError("操作者停止")
                if ratio >= 1.0:
                    break
                time.sleep(period)

            # 阶段2：执行经速度约束拉伸后的路点。
            run_start = time.monotonic()
            while True:
                elapsed = time.monotonic() - run_start
                t = min(elapsed, prepared.duration)
                deg = pose_at({"waypoints": prepared.waypoints}, t, neutral)
                target = {tok: math.radians(deg[tok]) for tok in controlled}
                state = self._write(target, 1.0)

                if elapsed > self.safety.tracking_grace_s:
                    worst = max(
                        abs(float(state.motor_state[G1_JOINT_INDEX[tok]].q) - q)
                        for tok, q in target.items()
                    )
                    if worst > self.safety.max_tracking_error_rad:
                        raise RealRobotError(
                            f"关节跟踪误差 {math.degrees(worst):.1f}° 超过上限，已停止"
                        )
                if self._stop.is_set():
                    raise RealRobotError("操作者停止")
                if elapsed >= prepared.duration:
                    break
                time.sleep(period)
        finally:
            self._release(controlled)

        return prepared


def normalize_motion(raw: dict) -> dict:
    """让文件/LLM JSON 统一经过同一套白名单和数值校验。"""
    return validate_motion(raw, max_wps=100000, max_dur=3600.0)
