"""一次性执行经现场确认的 G1 预设动作；默认仍不允许腰 roll/pitch。"""

import argparse
import math

from motions import MOTIONS
from real_robot import G1ArmSdkExecutor, SafetyConfig, prepare_motion, validate_for_robot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=[f"p{i}" for i in range(1, len(MOTIONS) + 1)], required=True)
    ap.add_argument("--max-speed-deg", type=float, default=5.0)
    ap.add_argument("--confirmed", required=True)
    args = ap.parse_args()
    if args.confirmed != "SAFETY-CONFIRMED":
        raise SystemExit("缺少现场安全确认")

    key = list(MOTIONS)[int(args.preset[1:]) - 1]
    source = MOTIONS[key]
    motion = {"name": key, "description": source["description"],
              "waypoints": source["waypoints"]}
    safety = SafetyConfig(max_speed_rad_s=math.radians(args.max_speed_deg))
    executor = G1ArmSdkExecutor("192.168.123.222", 29, safety)

    print("[1/5] 只读连接 rt/lowstate …", flush=True)
    executor.connect_read_only()
    controlled = validate_for_robot(motion, 29)
    neutral = executor.neutral_degrees(controlled)
    planned = prepare_motion(motion, 29, neutral, safety)
    print(f"[2/5] {key}: {motion['description']}", flush=True)
    print(f"[3/5] 限速 {args.max_speed_deg:.1f}°/s，预计动作 {planned.duration:.1f}s", flush=True)

    try:
        from tts_qwen import _play_blocking, synth
        _play_blocking(synth("伸びの運動を始めます。"))
    except Exception as e:
        print(f"语音播报跳过: {e}", flush=True)

    print("[4/5] 真机执行中；Ctrl+C 可停止并释放权重", flush=True)
    completed = executor.execute(motion)
    print(f"[5/5] 完成；arm_sdk 权重已释放，实际规划时长 {completed.duration:.1f}s", flush=True)


if __name__ == "__main__":
    main()
