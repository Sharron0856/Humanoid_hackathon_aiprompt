# -*- coding: utf-8 -*-
"""视觉互动教练(独立进程):看现场的人 → 判达标 → 写 observations.json。

链路:
  摄像头(默认外接Brio,经 robot_camera.ExternalSource)→ 按节采3个关键帧
  → 第8拍末发起 Qwen-VL 评判(胸前字母认人 A/B/C,标准见 vision_rules)
  → 选出本节最多1条反馈 → 原子写 observations.json
  → demo_voice.py 在该节第9~14拍的空窗读取并播报。

时钟:优先读 demo_voice 写的 vision_clock.json(同一播放时钟,暂停/重播都跟随);
读不到时 --standalone 手动回车对齐 demo 开始,按写死时间轴推算。

用法(.venv-real 环境,和 demo_voice 同时跑):
  .\.venv-real\Scripts\python.exe vision_coach.py            # 外接Brio + 时钟文件
  .\.venv-real\Scripts\python.exe vision_coach.py --cam 0    # 笔记本摄像头
  .\.venv-real\Scripts\python.exe vision_coach.py --standalone --show
挂了也不影响 demo:语音侧读不到新 observations = 静默(没识别到人就不出声)。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

import demo_viewer
import vision_rules as rules

PROJECT_DIR = Path(__file__).resolve().parent
CLOCK_PATH = PROJECT_DIR / "vision_clock.json"
OBS_PATH = PROJECT_DIR / "observations.json"
REPORT_PATH = PROJECT_DIR / "session_report.json"   # 班后汇报(语音侧读)
RECORDS_DIR = PROJECT_DIR / "records"               # 每场留档

# qwen3-vl-plus=商用旗舰(实测640px三帧1.1s);备选 qwen3-vl-235b-a22b-instruct
# /qwen3-vl-flash(0.8s);qwen-vl-max-latest 在国际版 endpoint 无权限。
VLM_MODEL = os.environ.get("VISION_MODEL", "qwen3-vl-plus")
SEND_WIDTH = 640          # 发给 VLM 前缩到这个宽度(实测960px三张要4.7s,640px仅1.0s)
FULL_KEYS = {"0_demo_full", "7_demo_full"}   # 仿真通し / 真机p7通し


def _api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        from llm_motion import API_KEY
        key = API_KEY
    return key


# ---------------- 帧源:后台线程持续取最新帧 ----------------

class FrameGrabber:
    def __init__(self, args):
        self.args = args
        self.frame = None          # 最新 BGR 帧
        self.lock = threading.Lock()
        self.ok = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def latest(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def _run(self):
        if self.args.cam is not None:
            cap = cv2.VideoCapture(self.args.cam)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            print(f"[cam] 本机摄像头 {self.args.cam}")
            while True:
                ret, frame = cap.read()
                if ret:
                    with self.lock:
                        self.frame, self.ok = frame, True
                else:
                    time.sleep(0.2)
        else:
            from robot_camera import ExternalSource
            src = ExternalSource()
            try:
                src.open()
            except Exception as e:
                # 不自动回退本机摄像头:打开用户设备必须由用户显式选择(--cam)
                print(f"[cam] 机器人Brio连不上:{e}")
                print("[cam] 已停止。若要改用笔记本摄像头,请显式加 --cam 0 重新运行。")
                return
            print("[cam] 机器人外接 Brio(TCP)")
            while True:
                jpeg, _err = src.fetch()
                if jpeg is None:
                    time.sleep(0.2)
                    continue
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8),
                                   cv2.IMREAD_COLOR)
                if img is not None:
                    with self.lock:
                        self.frame, self.ok = img, True


# ---------------- 时钟:跟随 demo_voice 的播放时钟 ----------------

class Clock:
    """返回 (section_key, 节内拍号) 或 (None, None)。"""

    def __init__(self, standalone: bool):
        self.standalone = standalone
        self.t0 = None
        self.active = False     # 演出时钟是否在走(用于演出中暂停预览标注)
        # 通し时间轴 [(key, start_s, end_s)],只留 ①~⑤
        self.timeline = [(k, s, e) for k, _n, s, e in
                         demo_viewer.section_timeline() if k in rules.SECTIONS
                         or k == "1_senobi"]

    def start_standalone(self):
        input(">> demo 开始的瞬间按回车对齐时钟 <<")
        self.t0 = time.time()

    def _from_full_t(self, t: float):
        for key, start, end in self.timeline:
            if start <= t < end:
                return key, (t - start) / demo_viewer.BEAT
        return None, None

    def now(self):
        if self.standalone:
            if self.t0 is None:
                self.active = False
                return None, None
            self.active = True
            return self._from_full_t(time.time() - self.t0)
        try:
            raw = json.loads(CLOCK_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            self.active = False
            return None, None
        age = time.time() - raw.get("wall", 0)
        self.active = age <= 2.0
        if age > 2.0:                      # 时钟停更:demo 没在跑
            return None, None
        t = raw["t"] + max(0.0, age)       # 用墙钟外推,误差≤写入间隔
        key = raw["key"]
        if key in FULL_KEYS:
            return self._from_full_t(t)
        if key in rules.SECTIONS:
            return key, t / demo_viewer.BEAT - 1.0   # 单节网格有1拍起手偏移
        return None, None


# ---------------- VLM 评判 ----------------

def _encode(frame) -> str:
    h, w = frame.shape[:2]
    if w > SEND_WIDTH:
        frame = cv2.resize(frame, (SEND_WIDTH, int(h * SEND_WIDTH / w)))
    _ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def _build_prompt(sec: dict) -> str:
    issue_desc = ",".join(sec["issues"])
    letters = "、".join(rules.NAMES)
    return (
        "这是广播体操演示现场按时间顺序采样的几张照片。场上最多有"
        f"{len(rules.NAMES)}位参与者,胸前贴有大字母标识:{letters}。\n"
        f"当前节:{sec['title']}。{sec['standard']}\n"
        "只统计能看到上半身(含胸口)的人;只露出头顶、手脚或被大面积遮挡的"
        "不算,宁缺毋滥,绝不要凑数。只评正在跟着做操的参与者;明显只是"
        "旁观、坐着、路过的人不要输出。\n"
        "任务:对每一位符合条件的参与者:\n"
        f"1. 用胸前字母确定身份;看不清字母时按画面从左到右 = {letters};\n"
        "2. 综合几张照片判断是否达标——取其中动作幅度最大的一张来判断,"
        "单张照片处于动作中途是正常的,不要因此判不达标;\n"
        f"3. 若明确不达标,issue 从这些里选一个:{issue_desc}。"
        "没有把握就算达标(ok=true);\n"
        '4. 再给一个 energy 字段:动作有力、精神饱满 = "high";'
        '达标但明显蔫、幅度偏小 = "low";拿不准填 "high"。\n'
        "只输出JSON数组,不要其他文字,例如:\n"
        '[{"name":"A","ok":true,"energy":"high"},'
        '{"name":"B","ok":false,"issue":"...","energy":"low"}]\n'
        "画面里没有人则输出 []。")


def vlm_judge(section_key: str, frames: list) -> list:
    """返回 [{"name":"A","ok":bool,"issue":str|None}, ...];失败返回 []。"""
    import dashscope
    dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

    sec = rules.SECTIONS[section_key]
    content = [{"image": _encode(f)} for f in frames]
    content.append({"text": _build_prompt(sec)})
    t0 = time.time()
    try:
        rsp = dashscope.MultiModalConversation.call(
            model=VLM_MODEL, api_key=_api_key(),
            messages=[{"role": "user", "content": content}])
    except Exception as e:
        print(f"[vlm] 调用异常:{e}")
        return []
    if rsp.status_code != 200:
        print(f"[vlm] {rsp.code}: {rsp.message}")
        return []
    text = rsp.output.choices[0].message.content
    if isinstance(text, list):             # dashscope 返回 [{'text': ...}]
        text = "".join(p.get("text", "") for p in text)
    text = text.strip().removeprefix("```json").removeprefix("```").strip("`\n ")
    try:
        data = json.loads(text)
    except ValueError:
        print(f"[vlm] JSON解析失败:{text[:120]!r}")
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        name = str(item.get("name", "")).strip().upper()
        if name not in rules.NAMES:
            continue
        issue = item.get("issue")
        if issue not in sec["issues"]:
            issue = None
        out.append({"name": name, "ok": bool(item.get("ok", True)) or not issue,
                    "issue": issue,
                    "energy": str(item.get("energy", "high")).lower()})
    print(f"[vlm] {section_key} {time.time() - t0:.1f}s -> {out}")
    return out


PREVIEW_MODEL = os.environ.get("VISION_PREVIEW_MODEL", "qwen3-vl-flash")
PREVIEW_INTERVAL = 4.0     # 空闲时每隔几秒刷新一次画面标注


def vlm_locate(frame) -> list:
    """定位画面里的人:[{"name":"A|?","box":[x1,y1,x2,y2] 0~1000}];失败 []。"""
    import dashscope
    dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

    letters = "、".join(rules.NAMES)
    prompt = (
        f"找出画面中每一位能看到脸或上半身的人,他们胸前可能贴着大字母标识({letters})。\n"
        '只输出JSON数组,例如 [{"name":"A","box":[120,80,400,900]}]。\n'
        "box 是该人整体的边界框 [x1,y1,x2,y2],坐标归一化到 0~1000。\n"
        ' 看不清字母的人 name 填 "?"。画面里没有人输出 []。')
    try:
        rsp = dashscope.MultiModalConversation.call(
            model=PREVIEW_MODEL, api_key=_api_key(),
            messages=[{"role": "user", "content": [
                {"image": _encode(frame)}, {"text": prompt}]}])
    except Exception:
        return []
    if rsp.status_code != 200:
        return []
    text = rsp.output.choices[0].message.content
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text)
    text = text.strip().removeprefix("```json").removeprefix("```").strip("`\n ")
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        box = item.get("box") or item.get("bbox_2d")
        if not (isinstance(box, list) and len(box) == 4):
            continue
        try:
            box = [max(0, min(1000, int(v))) for v in box]
        except (TypeError, ValueError):
            continue
        name = str(item.get("name", "?")).strip().upper()
        out.append({"name": name if name in rules.NAMES else "?", "box": box})
    return out


# ---------------- 反馈编排:每节最多1条,轮流点名,人人有赞 ----------------

LAST_EVAL = list(rules.SECTIONS)[-1]       # 最后一个评判节(=④)


class Director:
    def __init__(self):
        self.featured: set[str] = set()    # 整场被点过名的人
        self.small: set[str] = set()       # 整场动作偏小的人(不达标或蔫)
        self.history: list = []            # [(节, 评判结果, 播出内容)]

    def decide(self, section_key: str, results: list) -> list:
        for r in results:                  # 汇报统计:不达标或没劲都算"动作偏小"
            if (not r["ok"] and r["issue"]) or r.get("energy") == "low":
                self.small.add(r["name"])
        if not results:
            self.history.append((section_key, [], []))
            if section_key == LAST_EVAL:
                self._write_report([])
            return []                      # 没识别到人 = 不出声
        sec = rules.SECTIONS[section_key]
        detected = {r["name"]: r for r in results}
        star = rules.ROTATION.get(section_key)

        flagged = [r for r in results if not r["ok"] and r["issue"]]
        if flagged:                        # 有明确不达标:纠正优先
            # 还没被点过名的优先(避免同一人连续两节被批评),其次主角
            target = (next((r for r in flagged
                            if r["name"] not in self.featured), None)
                      or next((r for r in flagged if r["name"] == star),
                              flagged[0]))
            obs = [{"name": target["name"],
                    "advice": sec["issues"][target["issue"]]}]
        else:                              # 全达标/拿不准:表扬或打气
            name = star if star in detected and star not in self.featured \
                else None
            if name is None:               # 主角缺席/已点过:挑没点过名的
                fresh = [n for n in sorted(detected)
                         if n not in self.featured]
                name = fresh[0] if fresh else sorted(detected)[0]
            # 达标但不带劲 → 鼓励("もっとテンション上げて");否则表扬
            if detected[name].get("energy") == "low":
                obs = [{"name": name, "advice": rules.ENCOURAGE}]
            else:
                obs = [{"name": name, "advice": random.choice(rules.PRAISES)}]
        self.featured.add(obs[0]["name"])

        # 最后一节收尾:在场但还没被点过名的人补短表扬(上限1句,
        # 防止连播多句压到紧跟的结尾感谢语)
        if section_key == LAST_EVAL:
            for n in rules.NAMES:
                if n not in self.featured and n in detected:
                    obs.append({"name": n,
                                "advice": random.choice(rules.PRAISES)})
                    self.featured.add(n)
                    break
        self.history.append((section_key, results, obs))
        if section_key == LAST_EVAL:
            self._write_report(sorted(detected))
        return obs

    def _write_report(self, participants: list) -> None:
        """⑤评完:存一份本场记录,并给语音侧留班后汇报数据。"""
        record = {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "participants": participants,          # 最后一节仍在场的人
            "small_movement": sorted(self.small),  # 全场动作偏小的人
            "sections": [
                {"section": s, "results": r, "spoken": o}
                for s, r, o in self.history],
        }
        RECORDS_DIR.mkdir(exist_ok=True)
        rec_file = RECORDS_DIR / f"taiso_{time.strftime('%Y%m%d_%H%M%S')}.json"
        rec_file.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
        tmp = REPORT_PATH.with_suffix(".rtmp")
        tmp.write_text(json.dumps({
            "participants": participants,
            "small": sorted(self.small & set(participants)),
            "record_file": rec_file.name,
            "wall": time.time()}, ensure_ascii=False), "utf-8")
        os.replace(tmp, REPORT_PATH)
        print(f"[report] 已存档 {rec_file.name},参加{len(participants)}名,"
              f"动作偏小:{sorted(self.small)}")


def write_obs(section_key: str, seq: int, observations: list) -> None:
    tmp = OBS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "section": section_key, "seq": seq, "wall": time.time(),
        "observations": observations}, ensure_ascii=False), "utf-8")
    os.replace(tmp, OBS_PATH)
    print(f"[obs] {section_key} -> {observations}")


# ---------------- 主循环 ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="视觉互动教练(独立进程)")
    ap.add_argument("--cam", type=int, default=None,
                    help="用本机摄像头编号(默认走机器人外接Brio)")
    ap.add_argument("--standalone", action="store_true",
                    help="不读时钟文件,回车手动对齐 demo 开始")
    ap.add_argument("--show", action="store_true", help="显示调试窗口")
    args = ap.parse_args()

    grabber = FrameGrabber(args)
    grabber.start()
    clock = Clock(args.standalone)
    director = Director()
    if args.standalone:
        clock.start_standalone()

    seq = 0
    cur_section = None
    keyframes: list = []
    kf_idx = 0
    judging = False
    status = "等待时钟…"
    preview = {"people": [], "wall": 0.0, "busy": False, "last_try": 0.0}

    def judge_async(section_key, frames, my_seq):
        nonlocal judging
        results = vlm_judge(section_key, frames)
        if my_seq != seq:      # 已有更新的评判发起:旧结果作废,不覆盖不污染
            print(f"[vlm] {section_key} 结果迟到(seq{my_seq}<{seq}),丢弃")
            return
        write_obs(section_key, my_seq, director.decide(section_key, results))
        judging = False

    def preview_async(frame):
        people = vlm_locate(frame)
        preview.update(people=people, wall=time.time(), busy=False)

    print(f"[run] 模型={VLM_MODEL} 评判节={list(rules.SECTIONS)}")
    while True:
        key, beat = clock.now()
        if key != cur_section:
            cur_section = key
            keyframes, kf_idx = [], 0
        if key in rules.SECTIONS and beat is not None:
            beats = rules.SECTIONS[key]["keyframe_beats"]
            if kf_idx < len(beats) and beat >= beats[kf_idx]:
                frame = grabber.latest()
                if frame is not None:
                    keyframes.append(frame)
                kf_idx += 1
                status = f"{key} 拍{beat:.1f} 采样{len(keyframes)}/{len(beats)}"
                if kf_idx == len(beats) and keyframes:
                    judging = True         # 不做互斥:新评判直接发起,旧的作废
                    seq += 1
                    threading.Thread(
                        target=judge_async, args=(key, list(keyframes), seq),
                        daemon=True).start()
                    status = f"{key} 评判中…"
        elif key is None:
            status = "等待时钟…" if not args.standalone else "空窗"

        if args.show:
            # 空闲时定期刷新"识别到谁"的标注;演出进行中完全暂停(不抢带宽)
            now = time.time()
            if not clock.active and not preview["busy"] and \
                    now - preview["last_try"] > PREVIEW_INTERVAL:
                frame = grabber.latest()
                if frame is not None:
                    preview.update(busy=True, last_try=now)
                    threading.Thread(target=preview_async, args=(frame,),
                                     daemon=True).start()
            img = grabber.latest()
            if img is not None:
                disp = cv2.resize(img, (960, 540))
                if now - preview["wall"] < 4 * PREVIEW_INTERVAL:
                    for p in preview["people"]:
                        x1, y1, x2, y2 = (
                            int(p["box"][0] / 1000 * 960),
                            int(p["box"][1] / 1000 * 540),
                            int(p["box"][2] / 1000 * 960),
                            int(p["box"][3] / 1000 * 540))
                        known = p["name"] in rules.NAMES
                        color = (0, 255, 0) if known else (0, 200, 255)
                        cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(disp, p["name"], (x1 + 4, max(30, y1 + 34)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
                    found = ",".join(p["name"] for p in preview["people"]) or "no one"
                    cv2.putText(disp, f"seen: {found}", (10, 65),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                cv2.putText(disp, status, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("vision_coach (q quit)", disp)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    return 0
        time.sleep(0.03)


if __name__ == "__main__":
    sys.exit(main())
