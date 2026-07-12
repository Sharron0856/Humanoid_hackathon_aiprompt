# -*- coding: utf-8 -*-
"""在 G1 PC2 上运行:读外接 Brio 300(/dev/video6),TCP 推 JPEG 流。

由 robot_camera.py --external 自动 sftp 上传并拉起,一般不需要手动运行。
协议:每帧 = 4字节大端长度 + JPEG 字节。单客户端,断开后等下一个。
"""
import glob
import socket
import struct
import sys
import time

import cv2

CAM_NAME = "brio"   # 按设备名找外接相机(USB重新枚举后设备号会变,不能写死)
PORT = 5601
W, H, FPS = 1280, 720, 30
JPEG_Q = 80


def find_devices():
    """返回名字含 CAM_NAME 的 /dev/videoN 编号列表(按号递增)。"""
    hits = []
    for p in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        try:
            name = open(p).read().strip().lower()
        except OSError:
            continue
        if CAM_NAME in name:
            hits.append(int(p.split("video")[-1].split("/")[0]))
    return hits


def open_camera():
    devs = find_devices()
    if not devs:
        print(f"no v4l2 device named *{CAM_NAME}*", flush=True)
        sys.exit(1)
    for dev in devs:                     # 同名节点里只有采集节点能出帧
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FPS, FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 总是取最新帧,降低延迟
        if cap.isOpened() and cap.read()[0]:
            print(f"camera /dev/video{dev} opened", flush=True)
            return cap
        cap.release()
    print(f"devices {devs} all failed to capture", flush=True)
    sys.exit(1)


def serve():
    cap = open_camera()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    print("listening on %d" % PORT, flush=True)
    while True:
        conn, addr = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("client %s" % (addr,), flush=True)
        fails = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails > 30:
                        print("camera dead, reopening", flush=True)
                        cap.release()
                        time.sleep(1)
                        cap = open_camera()
                        fails = 0
                    continue
                fails = 0
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
                if not ok:
                    continue
                data = buf.tobytes()
                conn.sendall(struct.pack(">I", len(data)) + data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            print("client gone", flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    serve()
