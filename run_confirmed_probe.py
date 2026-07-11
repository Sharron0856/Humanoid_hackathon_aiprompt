"""一次性 G1 arm_sdk 联调探针；只运行右肘 5° 往返后退出。"""

import argparse
import math

from ai_robot import probe_motion
from real_robot import G1ArmSdkExecutor, SafetyConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirmed", required=True)
    args = ap.parse_args()
    if args.confirmed != "SAFETY-CONFIRMED":
        raise SystemExit("缺少现场安全确认")

    executor = G1ArmSdkExecutor(
        interface="192.168.123.222",
        robot_dof=29,
        safety=SafetyConfig(max_speed_rad_s=math.radians(5.0)),
    )
    print("[1/4] 只读连接 rt/lowstate …", flush=True)
    executor.connect_read_only()
    motion = probe_motion(executor)
    target = motion["waypoints"][1]["pose"][("right", "elbow")]
    print(f"[2/4] 当前状态有效；右肘目标 {target:.2f}°", flush=True)
    print("[3/4] 执行 5° 往返；Ctrl+C 可停止并释放权重", flush=True)
    prepared = executor.execute(motion)
    print(f"[4/4] 完成；arm_sdk 权重已释放，时长 {prepared.duration:.1f}s", flush=True)


if __name__ == "__main__":
    main()
