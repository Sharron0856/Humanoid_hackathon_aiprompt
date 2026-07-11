# G1 AI 真机链路

真机入口是 `ai_robot.py`。它不会让大模型直接写 DDS：大模型只生成路点，
随后依次经过关节白名单、DOF 兼容检查、限位、速度重排、实时状态看门狗、
跟踪误差检查和现场人工确认，最后才交给 `real_robot.py` 发布。

## 当前网络

- G1 PC2：`192.168.123.164`
- 本机 USB 有线口：`192.168.123.222/24`
- Windows/CycloneDDS 建议直接传本机接口 IP，而不是日文网卡显示名。

## 隔离环境

项目已建立 `.venv-real`（Python 3.10）。重建命令：

```powershell
python -m venv .venv-real
.\.venv-real\Scripts\python.exe -m pip install -r requirements-real.txt
```

## 使用顺序

第一步永远是只读状态：

```powershell
.\.venv-real\Scripts\python.exe ai_robot.py `
  --robot-dof 29 --interface 192.168.123.222 --read-only
```

离线检查 AI/预设动作，不连接机器人：

```powershell
.\.venv-real\Scripts\python.exe ai_robot.py --robot-dof 29
```

只有确认实体 DOF、控制模式、防倒保护和急停人员后，才使用：

```powershell
.\.venv-real\Scripts\python.exe ai_robot.py `
  --robot-dof 29 --interface 192.168.123.222 --execute
```

真机模式有两层确认：启动时必须输入 `ENABLE REAL ROBOT`，每条动作还会生成
一次性的 `RUN XXXX` 随机码。默认速度上限为 `10°/s`；未知腰部结构时，
`waist.roll` 和 `waist.pitch` 会被硬拒绝。

首次真机发送只使用交互命令 `probe`。它读取右肘当前角度，只移动 5° 后返回；
该探针通过并确认权重能正常释放之前，不运行 `p1~p5` 或 AI 动作。

## 现场安全条件

- 仅适用于支持 SDK 的 G1 EDU。
- 机器人必须使用防倒架/吊索，运动范围内无人和障碍物。
- 一名操作者全程握住实体急停或遥控器。
- 不允许与其他低层/高层上肢控制器同时发布冲突指令。
- 首次动作应使用单臂、小于 10° 的测试路点；不要直接运行完整广播体操。
