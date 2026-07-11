"""无头验证：关节匹配 / 限位 / 自碰撞扫描（不开窗口）。"""
import math
import mujoco
from motions import MOTIONS
from sim_viewer import resolve_joint, pose_at, robot_self_contacts, MODEL_PATH
model = mujoco.MjModel.from_xml_path(MODEL_PATH)
data = mujoco.MjData(model)

# 1) 关节匹配 + 限位检查
print("=== 关节匹配与限位检查 ===")
joint_addr, joint_range = {}, {}
ok = True
for mkey, motion in MOTIONS.items():
    for wp in motion["waypoints"]:
        for tokens, deg in wp["pose"].items():
            if tokens not in joint_addr:
                jid = resolve_joint(model, tokens)
                name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid)
                joint_addr[tokens] = model.jnt_qposadr[jid]
                lo, hi = model.jnt_range[jid]
                joint_range[tokens] = (math.degrees(lo), math.degrees(hi), name)
            lo, hi, name = joint_range[tokens]
            if not (lo - 0.5 <= deg <= hi + 0.5):
                ok = False
                print(f"  ✗ 超限位: {mkey} t={wp['t']}s {name} 目标{deg}° 限位[{lo:.0f}°, {hi:.0f}°]")
for tokens, (lo, hi, name) in joint_range.items():
    print(f"  {tokens} -> {name}  限位[{lo:.0f}°, {hi:.0f}°]")
if ok:
    print("  ✓ 所有目标角度都在关节限位内")

# 2) 自碰撞扫描（每个动作按0.05s步进整段扫）
print("\n=== 自碰撞扫描 ===")
if model.nkey > 0:
    mujoco.mj_resetDataKeyframe(model, data, 0)
else:
    mujoco.mj_resetData(model, data)
mujoco.mj_forward(model, data)
neutral = data.qpos.copy()
neutral_deg = {tokens: math.degrees(neutral[adr]) for tokens, adr in joint_addr.items()}

any_col = False
for mkey, motion in MOTIONS.items():
    total = motion["waypoints"][-1]["t"]
    seen = set()
    t = 0.0
    while t <= total + 1e-9:
        target = pose_at(motion, min(t, total - 1e-6) if total else 0, neutral_deg)
        data.qpos[:] = neutral
        for tokens, deg in target.items():
            data.qpos[joint_addr[tokens]] = math.radians(deg)
        mujoco.mj_forward(model, data)
        for pair in robot_self_contacts(model, data):
            if pair not in seen:
                seen.add(pair)
                any_col = True
                print(f"  ✗ {mkey} t={t:.2f}s 自碰撞: {pair[0]} <-> {pair[1]}")
        t += 0.05
    if not seen:
        print(f"  ✓ {mkey}（{total:.1f}s）无自碰撞")
print("\n验证完成:", "存在问题，见上方 ✗" if (any_col or not ok) else "全部通过 ✓")
