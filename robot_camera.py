# -*- coding: utf-8 -*-
"""把 G1 上的摄像头画面拉到本机(只读,不发任何控制指令)。

两路画面源:
  external  外接罗技 Brio 300(插在 PC2 USB 上,/dev/video6)——默认
            首次运行会自动 ssh 到 PC2 部署并拉起 brio_sender.py,再 TCP 收流
  head      头部内置 RealSense 彩色流(机器人 videohub 服务,DDS RPC)

用法(.venv-real 环境):
  探针:抓一帧存盘退出,验证链路
    .\.venv-real\Scripts\python.exe robot_camera.py --probe
  实时预览窗口(q/ESC 退出,s 抓拍到 preview\)
    .\.venv-real\Scripts\python.exe robot_camera.py
  看头部内置相机:
    .\.venv-real\Scripts\python.exe robot_camera.py --source head
"""
from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time
from ipaddress import ip_address
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_DIR = Path(__file__).resolve().parent
SNAP_DIR = PROJECT_DIR / "preview"

PC2_HOST = "192.168.123.164"
PC2_USER = "unitree"
PC2_PASS = "123"
BRIO_PORT = 5601
SENDER_LOCAL = PROJECT_DIR / "brio_sender.py"
SENDER_REMOTE = f"/home/{PC2_USER}/brio_sender.py"


# ---------------- 外接 Brio(TCP 收流) ----------------

class ExternalSource:
    """ssh 拉起 PC2 上的 brio_sender.py,然后 TCP 收 长度前缀+JPEG 帧。"""

    def __init__(self, host: str = PC2_HOST, port: int = BRIO_PORT):
        self.host, self.port = host, port
        self.sock: socket.socket | None = None
        self.ssh = None  # 保持存活:会话断开时 PC2 侧发送端一并退出

    def start_sender(self) -> None:
        import paramiko

        print(f"[ssh] 登录 {PC2_USER}@{self.host},部署并拉起 brio_sender …")
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(self.host, username=PC2_USER, password=PC2_PASS,
                    timeout=8, look_for_keys=False, allow_agent=False)
        sftp = cli.open_sftp()
        sftp.put(str(SENDER_LOCAL), SENDER_REMOTE)
        sftp.close()
        # p[y] 写法防止 pkill 匹配到承载它自己的 bash -c 命令行
        cli.exec_command("pkill -f 'brio_sender.p[y]'")[1].read()
        time.sleep(0.5)
        # 不用 nohup 后台:让命令挂在本会话上,viewer 退出→ssh 断→发送端回收
        cli.exec_command(
            f"exec python3 -u {SENDER_REMOTE} > /tmp/brio_sender.log 2>&1")
        self.ssh = cli

    def connect(self, retries: int = 10) -> None:
        last = None
        for _ in range(retries):
            try:
                s = socket.create_connection((self.host, self.port), timeout=3)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(5.0)
                self.sock = s
                print(f"[tcp] 已连接 {self.host}:{self.port}")
                return
            except OSError as e:
                last = e
                time.sleep(1)
        raise RuntimeError(
            f"连不上 {self.host}:{self.port}({last})。"
            f"看 PC2 侧日志:ssh {PC2_USER}@{self.host} cat /tmp/brio_sender.log")

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("sender 断开")
            buf += chunk
        return buf

    def fetch(self):
        try:
            (length,) = struct.unpack(">I", self._recv_exact(4))
            if length > 20_000_000:
                raise ConnectionError("协议错位")
            return self._recv_exact(length), 0
        except (OSError, ConnectionError) as e:
            # 断线重连一次;还不行就交给上层的失败计数
            try:
                if self.sock:
                    self.sock.close()
                self.connect(retries=2)
            except Exception:
                pass
            return None, str(e)

    def open(self) -> None:
        self.start_sender()
        self.connect()


# ---------------- 头部内置相机(DDS videohub) ----------------

class HeadSource:
    def __init__(self, interface: str, timeout_s: float):
        self.interface, self.timeout_s = interface, timeout_s
        self.client = None

    def open(self) -> None:
        import unitree_sdk2py.core.channel as sdk_channel
        from unitree_sdk2py.go2.video.video_client import VideoClient

        if os.name == "nt":
            # 与 real_robot.py 相同的补丁:官方 wheel 的 XML 把日志写死在 /tmp,
            # Windows 上 Domain 初始化会失败;IP 用 address 选择器避开日文网卡名。
            try:
                ip_address(self.interface)
                selector = f'address="{escape(self.interface)}"'
            except ValueError:
                selector = f'name="{escape(self.interface)}"'
            sdk_channel.ChannelConfigHasInterface = f'''<?xml version="1.0"?>
<CycloneDDS><Domain Id="any"><General><Interfaces>
<NetworkInterface {selector} priority="default" multicast="default"/>
</Interfaces></General></Domain></CycloneDDS>'''

        print(f"[dds] 初始化,接口 {self.interface} …")
        sdk_channel.ChannelFactoryInitialize(0, self.interface)
        client = VideoClient()
        client.SetTimeout(self.timeout_s)
        client.Init()
        self.client = client

    def fetch(self):
        code, data = self.client.GetImageSample()
        if code != 0:
            return None, code
        return bytes(data), 0


# ---------------- 共用:解码 / 探针 / 预览 ----------------

def decode(jpeg: bytes):
    import cv2
    import numpy as np

    return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)


def run_probe(source, tag: str) -> int:
    jpeg, err = source.fetch()
    if jpeg is None:
        print(f"[FAIL] 取帧失败:{err}")
        return 1
    img = decode(jpeg)
    if img is None:
        print(f"[FAIL] 收到 {len(jpeg)} 字节但 JPEG 解码失败。")
        return 1
    SNAP_DIR.mkdir(exist_ok=True)
    out = SNAP_DIR / f"robot_cam_probe_{tag}.jpg"
    # 不用 cv2.imwrite:Windows 上遇到中文路径会静默失败
    out.write_bytes(jpeg)
    h, w = img.shape[:2]
    print(f"[OK] 抓到一帧 {w}x{h},{len(jpeg)} 字节 -> {out}")
    return 0


def run_viewer(source, tag: str) -> int:
    import cv2

    win = f"G1 {tag} camera  (q/ESC quit, s snapshot)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    n, t0, fps = 0, time.monotonic(), 0.0
    fail_streak = 0
    while True:
        jpeg, err = source.fetch()
        if jpeg is None:
            fail_streak += 1
            if fail_streak >= 30:
                print(f"[FAIL] 连续取帧失败({err}),退出。")
                return 1
            time.sleep(0.1)
            continue
        fail_streak = 0
        img = decode(jpeg)
        if img is None:
            continue
        n += 1
        if n % 10 == 0:
            now = time.monotonic()
            fps = 10.0 / max(now - t0, 1e-6)
            t0 = now
        cv2.putText(img, f"{fps:.1f} fps", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow(win, img)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("s"):
            SNAP_DIR.mkdir(exist_ok=True)
            out = SNAP_DIR / f"robot_cam_{tag}_{time.strftime('%H%M%S')}.jpg"
            ok, buf = cv2.imencode(".jpg", img)
            if ok:
                out.write_bytes(buf.tobytes())  # 绕开 imwrite 的中文路径问题
                print(f"[snap] {out}")
    cv2.destroyAllWindows()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="G1 摄像头取流到本机(只读)")
    ap.add_argument("--source", choices=("external", "head"), default="external",
                    help="external=外接Brio300(默认) head=头部内置RealSense")
    ap.add_argument("--interface", default="192.168.123.222",
                    help="head 模式:本机与机器人同网段的网口 IP(同 real_robot.py)")
    ap.add_argument("--probe", action="store_true", help="只抓一帧存盘验证链路")
    ap.add_argument("--timeout", type=float, default=3.0, help="head 模式 RPC 超时秒")
    args = ap.parse_args()

    if args.source == "external":
        source = ExternalSource()
    else:
        source = HeadSource(args.interface, args.timeout)
    source.open()
    return run_probe(source, args.source) if args.probe \
        else run_viewer(source, args.source)


if __name__ == "__main__":
    sys.exit(main())
