"""AI 生成动作并经人工确认后交给 Unitree G1 真机执行。

默认是离线 dry-run。只有同时提供 ``--execute``、成功收到 ``rt/lowstate``，
并在每个动作前输入当次随机确认码，程序才会创建 ``rt/arm_sdk`` publisher。

示例：
    # 只验证 AI 动作，不连接机器人
    python ai_robot.py --robot-dof 29

    # 只读 DDS 状态检查（绝不发布控制消息）
    python ai_robot.py --robot-dof 29 --interface イーサネット --read-only

    # 真机人工确认模式
    python ai_robot.py --robot-dof 29 --interface イーサネット --execute
"""

from __future__ import annotations

import argparse
import math
import secrets
import sys
import time

from llm_motion import MODEL, MotionAgent
from motions import MOTIONS
from real_robot import (
    G1ArmSdkExecutor,
    RealRobotError,
    SafetyConfig,
    available_tokens,
    prepare_motion,
    validate_for_robot,
)

try:
    from tts_qwen import speak as tts_speak
except Exception:
    def tts_speak(_text):
        pass


# 单关节速度上限默认值。demo_robot 等演出入口可在 main() 前改成 45（编排
# 峰值 43.6°/s，45 下时间轴零拉伸=仿真原节奏）；首次联调入口保持保守的 10。
DEFAULT_MAX_SPEED_DEG = 10.0


def parse_args():
    ap = argparse.ArgumentParser(description="G1 AI 真机上肢控制（默认 dry-run）")
    ap.add_argument("--robot-dof", type=int, choices=(23, 29), required=True,
                    help="必须按实体机器人明确指定 23 或 29")
    ap.add_argument("--interface", help="连接 G1 的 DDS 网卡名，如 イーサネット/enp2s0")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--read-only", action="store_true",
                      help="只订阅 rt/lowstate，绝不创建 publisher")
    mode.add_argument("--execute", action="store_true",
                      help="允许在逐动作随机码确认后发布 rt/arm_sdk")
    ap.add_argument("--max-speed-deg", type=float, default=DEFAULT_MAX_SPEED_DEG,
                    help=f"单关节速度上限，默认 {DEFAULT_MAX_SPEED_DEG:g}°/s，"
                         "最大允许45°/s")
    ap.add_argument("--allow-waist-roll-pitch", action="store_true",
                    help="仅硬件明确支持时启用腰 roll/pitch；默认拒绝")
    ap.add_argument("--robot-speaker", action="store_true",
                    help="语音从 G1 内置扬声器播出（Qwen TTS→PCM→PlayStream；"
                         "失败自动回退本机扬声器）")
    return ap.parse_args()


# 语音教练挂载点：demo_robot 等入口可注入 demo_voice.VoiceCoach 实例，
# 真机执行时按限速拉伸比例换算回原时间轴触发报幕。
VOICE_COACH = None


def _enable_robot_speaker():
    """DDS 初始化完成后调用：把 tts_qwen 的播放出口切到 G1 扬声器。"""
    import tts_qwen
    from robot_speaker import RobotSpeaker
    tts_qwen.set_robot_sink(RobotSpeaker())
    print("✓ 语音将从 G1 扬声器播出（单条失败自动回退本机）")


def preset_motion(text: str):
    if not (text.startswith("p") and text[1:].isdigit()):
        return None
    keys = list(MOTIONS)
    idx = int(text[1:]) - 1
    if not (0 <= idx < len(keys)):
        raise RealRobotError(f"预设范围为 p1~p{len(keys)}")
    key = keys[idx]
    return {"name": key, "description": MOTIONS[key]["description"],
            "waypoints": MOTIONS[key]["waypoints"]}


def probe_motion(executor):
    """基于实时右肘角生成 ±5° 的最小真机往返探针。"""
    token = ("right", "elbow")
    current = executor.neutral_degrees((token,))[token]
    # 远离关节硬限位的一侧移动，保留至少 5° 余量。
    target = current + 5.0 if current <= 110.0 else current - 5.0
    return {
        "name": "right_elbow_probe_5deg",
        "description": "真机链路探针：右肘相对当前姿态移动5度后返回",
        "waypoints": [
            {"t": 0.0, "pose": {}},
            {"t": 2.0, "pose": {token: target}},
            {"t": 4.0, "pose": {}},
        ],
    }


def print_plan(motion, prepared, original_duration):
    joints = sorted({".".join(t) for wp in motion["waypoints"] for t in wp["pose"]})
    print("\n--- 待执行动作 ---")
    print("名称:", motion.get("name", "motion"))
    print("说明:", motion.get("description", ""))
    print(f"路点: {len(motion['waypoints'])}")
    print(f"原时长: {original_duration:.1f}s")
    print(f"限速后时长: {prepared.duration:.1f}s")
    print("显式动作关节:", ", ".join(joints) if joints else "自然站姿")
    print("arm_sdk保持关节:", ", ".join(".".join(t) for t in prepared.controlled_tokens))


def require_startup_confirmation():
    print("\n真机发布模式即将启用。确认以下条件：")
    print("  1. G1 为 EDU 版本且 DOF 参数正确")
    print("  2. 机器人使用防倒保护，周围无人和障碍物")
    print("  3. 一名操作者全程握住实体急停/遥控器")
    print("  4. 当前控制模式与 arm_sdk 匹配，不存在冲突控制器")
    phrase = input('全部满足才输入 "ENABLE REAL ROBOT": ').strip()
    if phrase != "ENABLE REAL ROBOT":
        raise RealRobotError("未完成真机启动确认")


def main():
    args = parse_args()
    safety = SafetyConfig(max_speed_rad_s=math.radians(args.max_speed_deg))
    executor = None

    if args.read_only or args.execute:
        if not args.interface:
            raise SystemExit("--read-only/--execute 必须提供 --interface")
        executor = G1ArmSdkExecutor(
            args.interface, args.robot_dof, safety,
            allow_waist_roll_pitch=args.allow_waist_roll_pitch,
        )
        if args.execute:
            require_startup_confirmation()
        print(f"正在通过 {args.interface} 等待 rt/lowstate …")
        executor.connect_read_only()
        print("✓ 已收到新鲜的 G1 lowstate")
        if args.robot_speaker:
            try:
                _enable_robot_speaker()
                if args.read_only:   # 只读模式顺便做扬声器链路测试（不涉及运动）
                    tts_speak("スピーカーテスト。こんにちは！")
                    print("已发送扬声器测试语音，等待播放……")
                    time.sleep(8)
            except Exception as e:
                print(f"⚠ G1扬声器启用失败（{e}），语音回退本机播放")
        if args.read_only:
            diag = executor.status_summary()
            print(f"mode_pr={diag['mode_pr']}  mode_machine={diag['mode_machine']}")
            tokens = tuple(sorted(
                available_tokens(args.robot_dof, args.allow_waist_roll_pitch),
                key=lambda t: str(t),
            ))
            state = executor.neutral_degrees(tokens)
            for token, deg in state.items():
                motor = diag["motors"][token]
                print(f"  {'.'.join(token):28s} {deg:8.2f}°  "
                      f"mode={motor['mode']} temp={motor['temperature']}°C")
            print("只读检查完成；没有创建 rt/arm_sdk publisher。")
            return

    agent = MotionAgent()
    print(f"\n模型: {MODEL} | G1 {args.robot_dof}DOF | "
          f"模式: {'真机逐次确认' if args.execute else 'DRY-RUN'}")
    print(f"速度硬上限: {args.max_speed_deg:.1f}°/s")
    print(f"输入自然语言动作或 p1~p{len(MOTIONS)}；"
          "probe=右肘5°链路测试；new清上下文；q退出。")

    while True:
        try:
            text = input("AI-G1 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return
        if not text:
            continue
        if text.lower() == "q":
            return
        if text.lower() == "new":
            agent.reset()
            print("✓ AI 上下文已清空")
            continue

        try:
            if text.lower() == "probe":
                if executor is None:
                    raise RealRobotError("probe 需要 --execute 真机状态，dry-run 不猜当前角度")
                motion = probe_motion(executor)
            else:
                motion = preset_motion(text.lower())
            if motion is None:
                print("… AI 正在生成并校验动作 …")
                motion = agent.generate(text)

            controlled = validate_for_robot(
                motion, args.robot_dof, args.allow_waist_roll_pitch)
            if executor:
                neutral = executor.neutral_degrees(controlled)
            else:
                # dry-run 只用于结构/限速检查；真实执行一定改用 lowstate 实测值。
                neutral = {t: 0.0 for t in controlled}
            prepared = prepare_motion(
                motion, args.robot_dof, neutral, safety,
                args.allow_waist_roll_pitch,
            )
            print_plan(motion, prepared, motion["waypoints"][-1]["t"])

            if not args.execute:
                print("DRY-RUN通过：未连接控制 publisher，机器人不会动作。")
                continue

            code = secrets.token_hex(2).upper()
            answer = input(f"现场确认安全后输入 RUN {code}，其他输入取消: ").strip()
            if answer != f"RUN {code}":
                print("已取消；没有发送动作。")
                continue

            tts_speak("動作を開始します。")
            print("真机执行中；按 Ctrl+C 立即进入 arm_sdk 权重释放。")
            on_tick = None
            if VOICE_COACH is not None:
                # 限速拉伸是全局近似均匀的：按时长比例把真机时间轴映射回
                # 编排时间轴，报幕点就落在对应的动作相位上。
                orig = motion["waypoints"][-1]["t"]
                scale = orig / prepared.duration if prepared.duration > 0 else 1.0
                key = motion.get("name", "")
                on_tick = (lambda t, _k=key, _s=scale:
                           VOICE_COACH.on_tick(_k, t * _s))
            completed = executor.execute(motion, on_tick=on_tick)
            print(f"✓ 动作完成并释放 arm_sdk 权重（{completed.duration:.1f}s）")
        except KeyboardInterrupt:
            if executor:
                executor.stop()
            print("\n操作者中断；正在/已经释放 arm_sdk。")
        except (RealRobotError, ValueError) as e:
            print(f"✘ 已拒绝: {e}")
        except Exception as e:
            # 未知异常不自动重试真机动作。
            if executor:
                executor.stop()
            print(f"✘ 异常，未重试: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
