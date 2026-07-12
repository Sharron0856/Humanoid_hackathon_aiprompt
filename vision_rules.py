# -*- coding: utf-8 -*-
"""视觉互动的评判标准与台词表(纯数据,现场改这里即可)。

设计(参考 视觉识别skill.md):
  - 认人:胸前大字母 A/B/C,由视觉大模型直接读;读不到时按画面站位
    (图像左=A 中=B 右=C,现场口头约定别换位)。
  - 评判:机器人播放时钟就是标准答案,VLM 只回答"该节的要点做到没有"。
    只评③④两节。每节在前8拍内采3个关键帧
    (拍号选在编排的动作极值处),第8拍末发起一次 VLM 调用。
  - 台词:固定集合(开场全部 TTS 预取,零等待);VLM 只返回 issue 键,
    绝不让它自由发挥台词。纠正要有把握,表扬可以宽松。
"""

import os

# 参与者字母(按画面从左到右的站位顺序);现场人数变了改环境变量即可,
# 如两人: $env:VISION_NAMES="AB"
NAMES = list(os.environ.get("VISION_NAMES", "ABC"))

# 每节:VLM 评判标准(说给模型听) + 关键帧拍号(动作极值,必须 ≤8,
# 第8拍末要发调用) + issue 键→纠正台词。praise 达标/拿不准时用。
# 只评③④两节(简单直观、误判风险最低):②还在锁定身份,⑤留给收尾气氛。
SECTIONS = {
    "3_udemawashi": {
        "title": "腕を回す運動(抡臂画圈)",
        "keyframe_beats": [2.0, 4.0, 5.5],   # 尽早采完尽早送评,给网络抖动留余量
        "standard": (
            "这一节的标准动作:双臂放松,画尽量大的圆圈,手最高时应过头顶、"
            "最低时扫过身体下方。"
            "评判:圈画得明显偏小、手没有举过头顶 → issue=circle_small。"),
        "issues": {
            "circle_small": "腕をもっと大きく回しましょう",
        },
    },
    "4_mune": {
        "title": "胸を反らす運動(展臂扩胸)",
        "keyframe_beats": [2.0, 4.0, 5.5],   # 第一次反らし顶点在第7拍,5.5已在扬起段
        "standard": (
            "这一节的标准动作:双臂先向两侧水平打开(T字),再向斜上方高举、"
            "同时挺胸后仰。"
            "评判:双臂没有充分展开、抱在身前 → issue=chest_closed;"
            "手臂举得太低、没有过肩 → issue=arms_low。"),
        "issues": {
            "chest_closed": "胸を大きく開きましょう",
            "arms_low": "腕をもっと高く上げましょう",
        },
    },
    "5_yokomage": {
        "title": "体を横に曲げる運動(体侧屈)",
        "keyframe_beats": [3.0, 4.5, 5.5],   # 第一侧弯顶点在4.5~5.5拍
        "standard": (
            "这一节的标准动作:一只手臂举过头顶,上半身向侧面大幅侧弯,"
            "不要向前弯腰。"
            "评判:身体几乎直立、侧弯角度很小 → issue=bend_small;"
            "明显向前弯腰而不是侧弯 → issue=lean_forward。"),
        "issues": {
            "bend_small": "体をしっかり横に曲げましょう",
            "lean_forward": "前かがみにならないようにしましょう",
        },
    },
}

# 表扬台词(达标或拿不准时;说错表扬没事,说错批评很尴尬)
PRAISES = [
    "よく頑張りました!",
    "いい感じですね!その調子!",
    "とても上手です!",
    "元気いっぱいですね!",
]

# 达标但不够带劲(幅度小、蔫)时的鼓励——比纠正温和,比表扬有推动力
ENCOURAGE = "もっとテンション上げていきましょう!"

# ---- 结束后的班后汇报(结尾感谢语之后播;数据来自整场视觉统计) ----
REPORT_END = "体操セッションが終了しました。"
REPORT_CALLOUT = ("さんは今日は少し動きが小さいようでした。"
                  "可能でしたら、あとでお声がけをお願いいたします。")  # 名字接在前面
REPORT_SAVED = "本日の記録は保存しました。"


def report_count_line(n):
    return f"本日は{n}名の方が最後まで参加されました。"

# 每节主角轮换(③A ④B ⑤=None即补漏:优先点还没被点过名的人);
# ⑤收尾时再给未点名者补1句短表扬(上限1句,防止压到紧跟的结尾感谢语)
ROTATION = {"3_udemawashi": "A", "4_mune": "B", "5_yokomage": None}


def all_feedback_texts():
    """所有可能播出的互动台词(用于开场 TTS 预取)。"""
    texts = set()
    for sec in SECTIONS.values():
        for advice in sec["issues"].values():
            for name in NAMES:
                texts.add(f"{name}さん、{advice}")
    for praise in PRAISES:
        for name in NAMES:
            texts.add(f"{name}さん、{praise}")
    for name in NAMES:
        texts.add(f"{name}さん、{ENCOURAGE}")
    texts.add(REPORT_END)
    texts.add(REPORT_SAVED)
    for n in range(1, len(NAMES) + 1):
        texts.add(report_count_line(n))
    for name in NAMES:
        texts.add(f"{name}{REPORT_CALLOUT}")
    return sorted(texts)
