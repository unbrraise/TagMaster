# -*- coding: utf-8 -*-
r"""TagMaster · 打标高手

选一个文件夹 → 逐段抽帧 → VLM 判 8 个字段 → DinoV2 判景别
（可选）复制改名 + 生成筛选预览页

依赖同目录的 python\ 与 models\，不安装任何额外包。
"""
import os
import sys
import csv
import json
import re
import time
import queue
import shutil
import glob
import threading
import subprocess
import traceback
import collections
import webbrowser
import http.server

# 程序根目录 = 本文件所在目录，复制到任何位置都能用，不依赖固定盘符
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.chdir(BASE)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

FF = os.path.join(BASE, "models", "ffmpeg", "bin", "ffmpeg.exe")
FFP = os.path.join(os.path.dirname(FF), "ffprobe.exe")
# 上次用过的路径与勾选项：开始任务/关窗时保存，下次启动自动恢复
SETTINGS = os.path.join(BASE, "设置.json")

# ffmpeg / ffprobe 是控制台程序，Windows 下每次调用默认会新建一个 cmd 窗口。
# 打标几百段会闪几百次黑框，所以统一走这个静默入口。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0


def run_quiet(cmd, **kw):
    """静默执行外部命令：不新建控制台窗口，断开 stdin 防止 ffmpeg 等待按键。"""
    kw.setdefault("stdin", subprocess.DEVNULL)
    if os.name == "nt":
        kw.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(cmd, **kw)

# ---------- 配色 ----------
BG = "#0f1216"
PANEL = "#171c24"
PANEL2 = "#1d232c"
LINE = "#2a323d"
TXT = "#e6ebf2"
DIM = "#8b95a7"
ACCENT = "#f5c451"
OK = "#63c98a"
ERR = "#e8735e"

FONT = "Microsoft YaHei UI"
KEYS = ["景别", "人数", "朝向", "主体", "动作", "场景", "光线", "情绪"]
FIELD_ORDER = ["景别", "人数", "朝向", "主体", "动作", "场景", "光线", "情绪"]
# 扩展字段：前三个让模型判（单帧可靠），运镜只从文件名导入时填，模型不参与
EXTRA_KEYS = ["风格", "色调", "字幕", "运镜"]
TAG_KEYS = KEYS + EXTRA_KEYS
STYLE_SET = {"赛璐璐", "厚涂", "水彩", "3D", "实拍", "线稿"}
TONE_SET = {"冷", "暖", "中性", "黑白", "高饱和", "低饱和"}
SUB_SET = {"有", "无"}
CAM_SET = {"固定", "推", "拉", "摇", "移", "跟", "慢镜头", "第一人称", "手持", "变速"}
STYLE_ALIAS = {"赛璐璐风格": "赛璐璐", "水彩画": "水彩", "写实": "实拍", "真人": "实拍",
               "3d": "3D", "3D渲染": "3D", "线稿风": "线稿"}
TONE_ALIAS = {"冷色调": "冷", "暖色调": "暖", "黑白画面": "黑白", "鲜艳": "高饱和",
              "灰暗": "低饱和", "灰色": "低饱和"}
SUB_ALIAS = {"有字幕": "有", "无字幕": "无", "没有": "无", "是": "有", "否": "无"}
# 手写文件名里的对应词
NAME_STYLE = {"水彩": "水彩", "厚涂": "厚涂", "赛璐璐": "赛璐璐", "实拍": "实拍",
              "线稿": "线稿", "3d": "3D", "3D": "3D"}
NAME_TONE = {"黑白": "黑白", "高饱和": "高饱和", "低饱和": "低饱和"}
NAME_CAM = {"慢镜头": "慢镜头", "慢镜": "慢镜头", "第一人称": "第一人称", "固定": "固定",
            "推镜": "推", "拉镜": "拉", "摇镜": "摇", "移镜": "移", "跟拍": "跟",
            "手持": "手持", "变速": "变速"}
SHOT_SET = {"大特写", "特写", "近景", "中景", "全景", "远景", "空镜"}
SHOT_MAP = {"小特写": "特写", "极大特写": "大特写", "中近景": "近景", "中全景": "全景",
            "大远景": "远景", "近特写": "特写", "脸部特写": "特写", "面部特写": "特写",
            "半身": "中景", "全身": "全景"}
NOISE = {"背景模糊", "模糊", "画面", "可见", "部分", "局部", "其他", "若干", "一些",
         "整体", "似乎", "可能", "看起来", "人物们", "东西", "物体", "人物", "人们"}
LIGHT_SET = {"白天", "夜晚", "黄昏", "阳光", "逆光", "昏暗"}
PEOPLE_SET = {"无人", "单人", "双人", "多人"}
FACE_SET = {"正面", "侧脸", "背影"}
MOOD_SET = {"平静", "温柔", "孤独", "悲伤", "紧张", "温暖",
            "空灵", "压抑", "愤怒", "惊讶", "恐惧", "欢快"}

PROMPT = """这是一段动画短片的 {n} 张连续画面。逐项如实标注，不确定就填"无"，不要编造。所有字段必须用中文。只输出一行 JSON，不要解释、不要重复。

"人数"：数画面里有几个人，填 无人/单人/双人/多人
"景别"：人数是"无人"时填"空镜"；有人时按人的大小判断——只有脸部局部填"大特写"，头肩填"特写"，上半身填"近景"，腰以上填"中景"，全身填"全景"，人在画面里很小填"远景"
"朝向"：主要人物的朝向，填 正面/侧脸/背影；人数是"无人"时填"无"
"主体"：主要人物或物体的特征，1-3个中文词空格分隔
"动作"：正在做的动作或姿态，1-2个中文词，没有动作填"无"
"场景"：环境地点，1-2个中文词（教室/走廊/街道/房间/天台/天空/水面/森林/废墟）
"光线"：填 白天/夜晚/黄昏/阳光/逆光/昏暗
"情绪"：从这里选一个 平静/温柔/孤独/悲伤/紧张/温暖/空灵/压抑/愤怒/惊讶/恐惧/欢快
"风格"：画风，填 赛璐璐/厚涂/水彩/3D/实拍/线稿
"色调"：整体色调，填 冷/暖/中性/黑白/高饱和/低饱和
"字幕"：画面里有没有字幕、台词或界面文字，填 有/无

JSON 示例：
{{"人数":"单人","景别":"近景","朝向":"侧脸","主体":"少女 金发","动作":"回头","场景":"街道","光线":"夜晚","情绪":"孤独","风格":"赛璐璐","色调":"冷","字幕":"无"}}"""


# ---------------- 工具函数 ----------------
# 模型引用放在模块级，任务结束 / 取消 / 报错时统一释放，避免反复打标累积显存。
_GPU = {"an": None, "clf": None, "torch": None}


def release_gpu():
    """释放模型引用并归还 torch 显存缓存；GUI 在任务终态、启动前、退出时调用。"""
    import gc
    _GPU["an"] = None
    _GPU["clf"] = None
    gc.collect()
    t = _GPU.get("torch")
    try:
        if t is not None and t.cuda.is_available():
            t.cuda.empty_cache()
            t.cuda.ipc_collect()
    except Exception:
        pass


def ffmpeg_version():
    """取一行 ffmpeg 版本写进日志，便于排查路径 / 编码兼容问题。"""
    try:
        r = run_quiet([FF, "-version"], capture_output=True, text=True,
                      encoding="utf-8", errors="ignore")
        return ((r.stdout or "").splitlines() or ["unknown"])[0].strip()[:120]
    except Exception as e:
        return "读取失败：%s" % e


def src_stat(path):
    """源视频的 (大小, 修改时间整数秒)：用来判断标签和缓存帧是否已过期。"""
    try:
        st = os.stat(path)
        return int(st.st_size), int(st.st_mtime)
    except OSError:
        return 0, 0


def frame_sig(path):
    """缓存签名 = 源文件大小 + 修改时间；源视频一换，旧帧自然不会被命中。"""
    sz, mt = src_stat(path)
    return "%d_%d" % (sz, mt)


def frame_path(frame_dir, fn, n, k, sig=""):
    """缓存帧路径：保留完整原文件名（含扩展名）+ 源签名 + 抽帧数，避免串片。"""
    return os.path.join(frame_dir, "%s__%s__n%d__%d.jpg" % (
        os.path.basename(fn), sig, n, k))


def frame_valid(p, src_path, slack=1.0):
    """缓存帧是否仍能代表当前源视频（存在、非空、且不早于源文件）。"""
    if not (os.path.exists(p) and os.path.getsize(p) > 0):
        return False
    _, src_mt = src_stat(src_path)
    try:
        return os.path.getmtime(p) + slack >= src_mt
    except OSError:
        return False


def ff_err(r, limit=240):
    """把 ffmpeg 的 stderr 压成一行短文本，方便写进日志。"""
    try:
        s = r.stderr or b""
        if isinstance(s, bytes):
            s = s.decode("utf-8", "ignore")
    except Exception:
        s = str(r.stderr or "")
    lines = [x.strip() for x in str(s).replace("\r", "\n").splitlines() if x.strip()]
    return " ".join(lines[-3:])[:limit]


def frame_offsets(d, n, k):
    """第 k 帧的目标时刻及附近备选时刻（秒）。

    抽帧失败时只在目标附近挪动，不悄悄退回片头——否则模型看到的会是开头画面。
    """
    d = max(0.1, float(d or 0) or 2.0)
    last = max(0.0, d - 0.05)
    t = min(last, d * (k + 1) / (n + 1))
    step = min(0.5, max(0.08, d / (n + 1) * 0.3))
    cand = [t, t - step, t + step, t - 2 * step, t + 2 * step,
            d * 0.5, d * 0.15, d * 0.85]
    out = []
    for x in cand:
        x = round(min(last, max(0.0, x)), 3)
        if x not in out:
            out.append(x)
    return out


def dur_of(path):
    if not os.path.isfile(FFP):
        raise RuntimeError("找不到 ffprobe.exe：%s（缺了不能用 2 秒兜底冒充时长）" % FFP)
    try:
        r = run_quiet([FFP, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        return float((r.stdout or "0").strip() or 0) or 2.0
    except Exception:
        return 2.0


def grab_frames(path, frame_dir, n=2, log=None):
    """按时长均分抽帧，结果长期缓存；命中缓存或续跑时完全不碰 ffmpeg。"""
    out = []
    d = None
    sig = frame_sig(path)
    for i in range(n):
        p = frame_path(frame_dir, path, n, i, sig)
        # 源视频没被换掉就复用旧帧，续跑时省掉整轮抽帧
        if frame_valid(p, path):
            out.append(p)
            continue
        if d is None:
            d = dur_of(path)
        r = None
        for off in frame_offsets(d, n, i):
            r = run_quiet([FF, "-y", "-v", "error", "-ss", "%.3f" % off, "-i", path,
                                "-vf", "scale=512:-1", "-vframes", "1", "-q:v", "3", p],
                               check=False, capture_output=True)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                break
        if os.path.exists(p) and os.path.getsize(p) > 0:
            out.append(p)
        elif log and r is not None:
            log("抽帧失败 %s：%s" % (
                os.path.basename(path), ff_err(r) or "目标时刻附近均无有效画面"), ERR)
    return out


def clean_field(s):
    s = str(s or "").strip().strip('"')
    # 模型偶尔会把值包在括号里，如 "情绪":("平静")" —— 括号不是标签内容
    s = re.sub(r"[()（）\[\]【】{}]", "", s)
    s = re.sub(r"[A-Za-z]+", "", s)
    s = re.sub(r"[/、,，;；]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" _-")
    return s


def enum_field(v, allowed, alias=None):
    """扩展字段归一：先精确、再别名、最后按包含关系认；认不出就留空，不写脏值。"""
    s = str(v or "").strip().strip('"').replace(" ", "")
    if not s:
        return ""
    if s in allowed:
        return s
    if alias and s in alias:
        return alias[s]
    low = s.lower()
    for a in allowed:
        if a and (a in s or a.lower() in low):
            return a
    return ""


def extract_json(text):
    """用 raw_decode 取完整 JSON 对象：支持嵌套，也能跳过后面附带的解释文字。"""
    s = (text or "").strip()
    dec = json.JSONDecoder()
    merged, i = {}, 0
    while True:
        a = s.find("{", i)
        if a == -1:
            break
        try:
            obj, end = dec.raw_decode(s[a:])
        except ValueError:
            i = a + 1
            continue
        if isinstance(obj, dict):
            merged.update(obj)
        i = a + max(1, end)
    return merged or None


# 兜底解析用的引号 / 值字符类：半角、全角引号都认
_JSON_Q = '["\u201c\u201d]?'
_JSON_V = '([^"\u201c\u201d,}，\n]{1,40})'


def repair_json(text):
    """修补模型最常见的 JSON 手滑：值被括号包住写成 ("平静") 这种。

    模型会写成 ("平静")"，字符序列是 ( " 平静 " ) "：
    先去掉括号，再把值后面多出来的连续引号并掉（空值 "" 不受影响）。
    """
    t = re.sub(r'[（(]\s*(["“])', r"\1", text or "")
    t = re.sub(r'(["”])\s*[）)]', r"\1", t)
    t = re.sub(r'(?<=[^\s:,{\[])"{2,}', '"', t)
    return t


def salvage_fields(text):
    """JSON 整体解析失败时的兜底：按「键:值」把能认的字段逐个捡回来。

    模型偶尔写出一处语法错，标准解析就整条失败，但另外十个字段其实都是好的
    —— 一个括号不该把整条结果丢掉。捡回来的值照样要过 parse_tags 的枚举校验。
    """
    s = text or ""
    out = {}
    for k in TAG_KEYS:
        m = re.search(_JSON_Q + re.escape(k) + _JSON_Q + r"\s*[:：]\s*" + _JSON_Q + _JSON_V, s)
        if m:
            v = m.group(1).strip()
            if v:
                out[k] = v
    return out or None


def parse_tags(text):
    d = extract_json(text)
    if not d:
        d = extract_json(repair_json(text))
    if not d:
        d = salvage_fields(text)
    if not d:
        d = salvage_fields(repair_json(text))
    if not d:
        return None
    def _flat(v):
        # 模型偶尔把某个字段写成嵌套对象，摊平后再清洗，避免脏标签混进文件名
        if isinstance(v, dict):
            return " ".join(str(x) for x in v.values())
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return v

    out = {k: clean_field(_flat(d.get(k, ""))) for k in KEYS}
    out["风格"] = enum_field(_flat(d.get("风格")), STYLE_SET, STYLE_ALIAS)
    out["色调"] = enum_field(_flat(d.get("色调")), TONE_SET, TONE_ALIAS)
    out["字幕"] = enum_field(_flat(d.get("字幕")), SUB_SET, SUB_ALIAS)
    out["运镜"] = ""  # 运镜只从文件名导入时填，模型只看静帧，不让它猜

    shot = SHOT_MAP.get(out["景别"], out["景别"])
    if shot not in SHOT_SET:
        shot = next((k for k in SHOT_SET if k and k in shot), "")
    out["景别"] = shot
    if out["人数"] not in PEOPLE_SET:
        out["人数"] = ""
    if out["朝向"] not in FACE_SET and out["朝向"] != "无":
        out["朝向"] = ""
    if out["光线"] not in LIGHT_SET:
        out["光线"] = ""
    if out["情绪"] not in MOOD_SET:
        out["情绪"] = ""
    if out["人数"] == "无人":
        # 无人一律空镜：模型偶尔把场景名（如「水面」）填进景别，这里直接纠正
        out["朝向"] = "无"
        out["景别"] = "空镜"
    if not out["景别"]:
        # 景别认不出：有人物时兜底中景（DinoV2 之后会复算覆盖），连人物都没有才算失败
        if out["人数"] in PEOPLE_SET:
            out["景别"] = "中景"
        else:
            return None
    return out


# 续跑时判定「这条旧结果真的能用」：只写了个景别的不算完成。
# 主体 / 动作 / 场景允许为空（模型确实可能给「无」），朝向允许「无」。
CACHE_KEYS = ("景别", "人数", "朝向")


def row_complete(r, src=None):
    """续跑判定：字段完整 + 源视频未被替换（大小 / 修改时间一致）。

    光线、情绪经校验后可能合法为空，不列为必需，否则每次续跑都会重跑这些片段。
    """
    fn = str(r.get("原文件") or "").strip()
    if not fn:
        return False
    if not all(str(r.get(k) or "").strip() for k in CACHE_KEYS):
        return False
    if str(r.get("景别") or "").strip() not in SHOT_SET:
        return False
    if str(r.get("人数") or "").strip() not in PEOPLE_SET:
        return False
    if src is not None:
        sz, mt = src_stat(os.path.join(src, fn))
        try:
            ok_sz = int(r.get("源大小") or 0)
            ok_mt = int(r.get("源修改时间") or 0)
        except (TypeError, ValueError):
            return False
        if not sz or ok_sz != sz or ok_mt != mt:
            return False
    return True


def parse_fail_reason(text):
    """把「解析失败」翻译成能看懂的原因（判定口径与 parse_tags 保持一致）。"""
    s = (text or "").strip()
    if not s:
        return "模型没有返回内容"
    if s.startswith("EXC:"):
        return "推理出错（%s）" % s[4:].strip().splitlines()[0][:80]
    if "{" not in s:
        return "输出里没有 JSON（模型在用文字描述）"
    d = extract_json(s)
    if not d:
        return "JSON 解析失败（输出被截断或夹杂了解释，常见于无人物的空镜长输出）"
    shot = str(d.get("景别") or "").strip()
    if not shot:
        return "输出里没有有效的「景别」字段"
    m = SHOT_MAP.get(shot, shot)
    if m not in SHOT_SET and not next((k for k in SHOT_SET if k and k in m), ""):
        return "景别无法识别（%s）" % shot[:16]
    return "字段缺失或不合规"


def split_tokens(v):
    v = (v or "").strip()
    if not v or v == "无":
        return []
    return [t for t in re.split(r"[\s_]+", v) if t and t != "无"]


def segment(token, vocab):
    if len(token) < 3:
        return [token]
    best = None
    for i in range(2, len(token) - 1):
        a, b = token[:i], token[i:]
        if a in vocab and b in vocab:
            score = min(len(a), len(b))
            if best is None or score > best[0]:
                best = (score, [a, b])
    return best[1] if best else [token]


def build_name(row, num, max_stem=110):
    """按标签拼文件名：丢掉空值/「无」、压掉连续下划线、留足 Windows 路径余量。

    Windows 单文件名上限 255，完整路径还受 MAX_PATH 260 限制，所以主体截到 110 字符。
    编号始终放在最后一段——预览页靠 split('_').pop() 显示它。
    """
    parts = []
    for k in FIELD_ORDER:
        v = re.sub(r"[\s_]+", " ", str(row.get(k, "") or "").strip()).strip()
        if v and v != "无":
            parts.append(v)
    num = re.sub(r'[\\/:*?"<>|]', "", str(num)).strip() or "0000"
    label = "_".join(parts)
    room = max_stem - len(num) - 1
    if room <= 0:
        label = ""
    elif len(label) > room:
        label = label[:room].rstrip(" _-")
    stem = (label + "_" + num) if label else num
    stem = re.sub(r'[\\/:*?"<>|]', "", stem).rstrip(" .") or "clip"
    return stem + ".mp4"


def build_name_lib(row, max_stem=110):
    """并入素材库时的文件名：沿用库里手写风格——空格分隔、不要编号。

    形如「中景 夜晚 金发少女 撑伞 水面.mp4」；人数/朝向/情绪留在标签表里，不进名字。
    撞名时由复制环节加 -2、-3，绝不覆盖库里已有文件。
    """
    parts = []
    for k in ("景别", "光线", "主体", "动作", "场景"):
        v = re.sub(r"[\s_]+", " ", str(row.get(k, "") or "").strip()).strip()
        if v and v != "无":
            parts.append(v)
    stem = " ".join(parts).strip()
    stem = re.sub(r'[\\/:*?"<>|]', "", stem)[:max_stem].strip(" .-")
    if not stem:
        stem = str(row.get("编号") or "").strip() or "clip"
    return stem + ".mp4"


GALLERY_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>素材库 · 标签浏览</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0e1014;--panel:#161a21;--line:#242a34;--txt:#e8ecf2;--dim:#8b95a7;--accent:#f5c451;--accent2:#5ec8f0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 "Microsoft YaHei",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:20;background:rgba(14,16,20,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:18px 28px 14px}
h1{font-size:17px;font-weight:600;letter-spacing:.4px;display:flex;align-items:baseline;gap:12px}
h1 span{font-size:12px;color:var(--dim);font-weight:400}
.toolbar{display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap}
input[type=search]{background:var(--panel);border:1px solid var(--line);color:var(--txt);padding:8px 12px;border-radius:8px;width:280px;outline:none;font-size:13px}
input[type=search]:focus{border-color:#3a4350}
.count{color:var(--dim);font-size:12px}.count b{color:var(--accent);font-weight:600}
.send{margin-left:auto;background:none;border:1px solid var(--accent);color:var(--accent);padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px;font-weight:600}
.send:hover{background:var(--accent);color:#1a1408}
.send:disabled{opacity:.4;cursor:not-allowed}
.pickbtn{background:none;border:1px solid var(--line);color:var(--dim);padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px}
.pickbtn.on{background:var(--accent2);border-color:var(--accent2);color:#0c1218;font-weight:600}
.pickbtn.on:hover{color:#0c1218}
.reset{background:none;border:1px solid var(--line);color:var(--dim);padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px}
.reset:hover{color:var(--txt);border-color:#3a4350}
.facets{display:flex;flex-direction:column;gap:7px;margin-top:12px}
.frow{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.flabel{width:42px;flex:none;font-size:11px;color:var(--dim);letter-spacing:1px}
.chip{background:var(--panel);border:1px solid var(--line);color:var(--dim);padding:3px 10px;border-radius:20px;cursor:pointer;font-size:12px;user-select:none;transition:.12s;white-space:nowrap}
.chip:hover{color:var(--txt);border-color:#3a4350}
.chip.on{background:var(--accent);border-color:var(--accent);color:#1a1408;font-weight:600}
.chip .n{opacity:.55;margin-left:4px;font-size:11px}
main{padding:22px 28px 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:16px}
.card{display:block;color:inherit;text-decoration:none;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;transition:.15s}
.card:hover{border-color:#3d4756;transform:translateY(-2px)}
.thumb{position:relative;aspect-ratio:16/9;background:#000}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.badge{position:absolute;top:7px;left:7px;display:flex;gap:4px}
.badge i{font-style:normal;font-size:10.5px;padding:2px 6px;border-radius:4px;background:rgba(0,0,0,.66);color:#fff}
.badge i.s{color:#1a1408;background:rgba(245,196,81,.92);font-weight:700}
.badge i.m{color:#0c1218;background:rgba(94,200,240,.9);font-weight:600}
.meta{padding:9px 10px 11px}
.m1{font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m2{font-size:11.5px;color:var(--dim);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pick{position:absolute;right:8px;bottom:8px;width:22px;height:22px;border-radius:50%;border:2px solid rgba(255,255,255,.65);background:rgba(0,0,0,.35);cursor:pointer;transition:.12s}
.pick:hover{border-color:var(--accent)}
.pick.on{background:var(--accent);border-color:var(--accent)}
.pick.on::after{content:'';position:absolute;left:7px;top:4px;width:5px;height:9px;border:solid #1a1408;border-width:0 2px 2px 0;transform:rotate(45deg)}
.card.picked{border-color:var(--accent)}
.empty{color:var(--dim);padding:60px 0;text-align:center;font-size:13px}
</style></head><body>
<header>
  <h1>素材库 · 标签浏览 <span>点卡片直接播放原片</span></h1>
  <div class="toolbar">
    <input type="search" id="q" placeholder="搜索关键词：少女 / 森林 / 背影 …">
    <div class="count">命中 <b id="cnt">0</b> / <span id="tot">0</span></div>
    <button class="send" id="send" disabled>发送到 Premiere（已选 0）</button>
    <button class="pickbtn" id="onlypicked">只看已选</button>
    <button class="reset" id="clearsel">取消全选</button>
    <button class="reset" id="reset">清空筛选</button>
  </div>
  <div class="facets" id="facets"></div>
</header>
<main><div class="grid" id="grid"></div></main>
<script>
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
const DATA = __DATA__;
const KEYMAP = {景别:'s', 人数:'n', 朝向:'d', 光线:'l', 情绪:'m', 场景:'p', 风格:'g', 色调:'h', 字幕:'u', 运镜:'v', 作品:'w'};
const state = {}; Object.keys(KEYMAP).forEach(k => state[k] = new Set());
const grid = document.getElementById('grid'), cnt = document.getElementById('cnt');
document.getElementById('tot').textContent = DATA.items.length;
const facetsEl = document.getElementById('facets');
for (const [label, vals] of Object.entries(DATA.facets)) {
  const row = document.createElement('div'); row.className = 'frow';
  const lb = document.createElement('div'); lb.className = 'flabel'; lb.textContent = label;
  row.appendChild(lb);
  vals.forEach(({v, c}) => {
    const el = document.createElement('div');
    el.className = 'chip'; el.dataset.group = label; el.dataset.v = v;
    el.innerHTML = esc(v) + '<span class="n">' + c + '</span>';
    el.onclick = () => { const s = state[label];
      if (s.has(v)) { s.delete(v); el.classList.remove('on'); }
      else { s.add(v); el.classList.add('on'); } render(); };
    row.appendChild(el);
  });
  facetsEl.appendChild(row);
}
let q = '';
document.getElementById('q').oninput = e => { q = e.target.value.trim().toLowerCase(); render(); };
document.getElementById('reset').onclick = () => {
  Object.values(state).forEach(s => s.clear());
  document.querySelectorAll('.chip.on').forEach(e => e.classList.remove('on'));
  q = ''; document.getElementById('q').value = ''; render();
};
// ---- 勾选 → 发送到 Premiere ----
const picked = new Map();   // path -> item；重渲染后按 path 恢复选中态
const sendBtn = document.getElementById('send');
const PR_PORT = __PRPORT__;
function updateSel() {
  sendBtn.textContent = '发送到 Premiere（已选 ' + picked.size + '）' + (tagOnline ? ' · 在线' : '');
  sendBtn.disabled = picked.size === 0;
}
let tagOnline = false;
let onlyPicked = false;   // 只看已选：换筛选/换页也不丢勾选的卡片
function updateSel() {
  sendBtn.textContent = '发送到 Premiere（已选 ' + picked.size + '）' + (tagOnline ? ' · 在线' : '');
  sendBtn.disabled = picked.size === 0;
}
document.getElementById('clearsel').onclick = () => {
  picked = {};
  render(); updateSel();
};
document.getElementById('onlypicked').onclick = function () {
  onlyPicked = !onlyPicked;
  this.classList.toggle('on', onlyPicked);
  render();
};
async function doSend() {
  if (!picked.size) return;
  const items = [...picked.values()].map(it => ({path: it.path, bin: it.bin || ''}));
  const payload = JSON.stringify({tm_cmd: {action: 'importMany', items, createdAt: Date.now()}});
  // 优先直发：TagMaster 在线时浏览器直接写入 PR 命令队列，不用下载文件
  try {
    const r = await fetch('http://127.0.0.1:' + PR_PORT + '/send', {
      method: 'POST', body: payload,
      headers: {'Content-Type': 'text/plain;charset=UTF-8'}  // 简单请求，不触发预检
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.msg || '发送被拒');
    sendBtn.textContent = '已发送 ' + j.count + ' 个到 Premiere';
    setTimeout(updateSel, 6000);
    return;
  } catch (e) { /* TagMaster 未开：回退为下载文件 */ }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([payload], {type: 'application/json'}));
  a.download = 'tm_cmd.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 8000);
  sendBtn.textContent = 'TagMaster 未开 —— 已下载 tm_cmd.json，拖进 TagMaster';
  setTimeout(updateSel, 8000);
}
sendBtn.onclick = doSend;
fetch('http://127.0.0.1:' + PR_PORT + '/ping')
  .then(r => r.json()).then(j => { if (j.ok) { tagOnline = true; updateSel(); } })
  .catch(() => {});
function match(it) {
  if (onlyPicked && !picked.has(it.path)) return false;
  for (const [label, key] of Object.entries(KEYMAP)) {
    const s = state[label];
    if (s.size) { const toks = String(it[key] || '').split(' ');
      if (!toks.some(t => s.has(t))) return false; }
  }
  if (q) { const hay = [it.f, it.o, it.a, it.p, it.l, it.m, it.d, it.s, it.n, it.w].join(' ').toLowerCase();
    if (!hay.includes(q)) return false; }
  return true;
}
function render() {
  const list = DATA.items.filter(match); cnt.textContent = list.length;
  if (!list.length) { grid.innerHTML = '<div class="empty">' + (onlyPicked && picked.size ? '已选的片段被当前筛选挡住了 —— 点「只看已选」关掉，或「取消全选」' : '没有符合条件的片段') + '</div>'; return; }
  const frag = document.createDocumentFragment();
  list.forEach(it => {
    // 真实链接 + URL 归一化：特殊字符文件名也能正确指向本地文件，且不受弹窗拦截
    const card = document.createElement('a'); card.className = 'card'; card.title = it.f;
    if (picked.has(it.path)) card.classList.add('picked');
    // 路径相对当前预览页（单部页是 ../名字.mp4，全库页是 作品/名字.mp4），逐段编码
    card.href = new URL(it.f.split('/').map(encodeURIComponent).join('/'), location.href).href;
    card.target = '_blank'; card.rel = 'noopener';
    const badge = [it.s ? '<i class="s">' + esc(it.s) + '</i>' : '',
                   it.n ? '<i>' + esc(it.n) + '</i>' : '',
                   it.d && it.d !== '无' ? '<i>' + esc(it.d) + '</i>' : ''].join('');
    const mood = it.m ? '<i class="m">' + esc(it.m) + '</i>' : '';
    card.innerHTML = '<div class="thumb"><img loading="lazy" src="' + esc(it.t) + '" alt="">' +
      '<div class="badge">' + badge + '</div>' +
      (mood ? '<div class="badge" style="left:auto;right:7px">' + mood + '</div>' : '') + '</div>' +
      '<div class="meta"><div class="m1">' + esc(it.o || '——') + (it.a ? ' · ' + esc(it.a) : '') + '</div>' +
      '<div class="m2">' + [it.p, it.l, it.g, it.h].filter(Boolean).map(esc).join(' / ') +
      ' <span style="opacity:.45">· ' + esc(it.id || it.f.replace('.mp4', '').split('_').pop()) + '</span></div></div>';
    // 勾选圆点：点它只切换选中，不触发打开视频
    const pick = document.createElement('span');
    pick.className = 'pick' + (picked.has(it.path) ? ' on' : '');
    pick.title = '勾选后可发送到 Premiere';
    pick.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      if (picked.has(it.path)) picked.delete(it.path); else picked.set(it.path, it);
      pick.classList.toggle('on');
      card.classList.toggle('picked', picked.has(it.path));
      updateSel();
    };
    card.appendChild(pick);
    frag.appendChild(card);
  });
  grid.innerHTML = ''; grid.appendChild(frag);
}
render();
</script></body></html>"""


class Cancelled(Exception):
    pass


# ---------------- 流水线 ----------------
def plan_dirs(out, lib_mode):
    """按模式规划目录：普通模式片段进 clips/；库模式片段直接进输出目录，管理文件收 _标签数据。"""
    if lib_mode:
        data = os.path.join(out, "_标签数据")
        return {"clips": out, "data": data,
                "thumbs": os.path.join(data, "thumbs"),
                "frames": os.path.join(data, "cache"),
                "csv": os.path.join(data, "tags.csv")}
    return {"clips": os.path.join(out, "clips"), "data": out,
            "thumbs": os.path.join(out, "thumbs"),
            "frames": os.path.join(out, "cache"),
            "csv": os.path.join(out, "tags.csv")}


def write_gallery(rows, data_dir, clips_dir, thumbs_dir, lib_mode, log, prog, stage, detail,
                  stop_event, p0=97, pspan=2.5):
    """生成缩略图 + 预览页（打标和「导入已有片段」共用）。返回 preview.html 路径。"""
    stage("生成预览页")
    detail("抽缩略图并生成筛选页")
    prog(p0)
    items = []
    facets_src = {k: collections.Counter() for k in ("景别", "人数", "朝向", "光线", "情绪", "场景", "风格", "色调", "字幕", "运镜")}
    keymap = {"景别": "s", "人数": "n", "朝向": "d", "光线": "l", "情绪": "m", "场景": "p",
              "风格": "g", "色调": "h", "字幕": "u", "运镜": "v"}
    for i, item in enumerate(rows):
        if stop_event.is_set():
            raise Cancelled()
        row = item["row"]
        name = row.get("新文件名")
        if not name:
            continue
        # 缩略图用唯一的新文件名，避免编号重复串片；源片更新后重新生成
        th = "%s.jpg" % os.path.splitext(name)[0]
        tp = os.path.join(thumbs_dir, th)
        clip_path = os.path.join(clips_dir, name)
        if not os.path.isfile(clip_path):
            continue  # 文件被改名或删除了，跳过这条，避免预览里出现坏卡片
        if not frame_valid(tp, clip_path):
            vd = dur_of(clip_path)
            r = None
            for off in frame_offsets(vd, 2, 0):
                r = run_quiet([FF, "-y", "-v", "error", "-ss", "%.3f" % off,
                                    "-i", clip_path,
                                    "-vf", "scale=320:-1", "-vframes", "1", "-q:v", "5", tp],
                                   check=False, capture_output=True)
                if os.path.exists(tp) and os.path.getsize(tp) > 0:
                    break
            if i < 3 and not os.path.exists(tp) and r is not None:
                log("缩略图失败 %s：%s" % (name, ff_err(r) or "无有效画面"), ERR)
        if not os.path.exists(tp):
            continue
        # 视频路径相对当前预览页：单部作品在 _标签数据 里用 ../，普通模式在根目录用 clips/
        items.append({"f": ("../" if lib_mode else "clips/") + name,
                      "t": "thumbs/" + th, "id": str(row.get("编号", "")),
                      "path": clip_path,
                      "bin": os.path.basename(os.path.normpath(clips_dir)) if lib_mode else "本批片段",
                      "s": row.get("景别", ""), "n": row.get("人数", ""), "d": row.get("朝向", ""),
                      "o": row.get("主体", ""), "a": row.get("动作", ""),
                      "p": row.get("场景", ""), "l": row.get("光线", ""), "m": row.get("情绪", ""),
                      "g": row.get("风格", ""), "h": row.get("色调", ""),
                      "u": row.get("字幕", ""), "v": row.get("运镜", "")})
        for label, key in keymap.items():
            for tok in str(row.get(label, "")).split():
                if tok and tok != "无":
                    facets_src[label][tok] += 1
        if rows:
            prog(int(p0 + pspan * (i + 1) / len(rows)))

    facets = {label: [{"v": v, "c": n} for v, n in c.most_common(24)] for label, c in facets_src.items()}
    data = json.dumps({"items": items, "facets": facets}, ensure_ascii=False)
    # 防止标签里出现 </script> 时提前闭合脚本块
    data = (data.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))
    # 库模式下预览页在 _标签数据 里，片段在上一级；缩略图仍在同级的 thumbs/
    html = GALLERY_HTML.replace("__DATA__", data).replace("__PRPORT__", str(PR_HTTP_PORT))
    html_path = os.path.join(data_dir, "preview.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log("预览页：%s" % html_path)
    return html_path


NAME_LIGHT = {"夜色": "夜晚", "落日": "黄昏", "夕阳": "黄昏", "晴天": "白天", "晨光": "白天"}
# 库里手写命名的自定义类别：不是标准景别，但用户就是这么分类的，原词保留
NAME_SHOT = {"混合": "混合", "混剪": "混合"}
# 场景词：导入时填进「场景」，让筛选多一档（原词保留）
NAME_SCENE = {"室内": "室内", "室外": "室外", "天空": "天空", "森林": "森林",
              "海边": "海边", "教室": "教室", "走廊": "走廊", "街道": "街道",
              "房间": "房间", "水面": "水面", "天台": "天台", "车内": "车内",
              "废墟": "废墟", "校园": "校园", "卧室": "卧室"}
# 文件名匹配用「别名 + 词表」一起做包含匹配（如「冷色调」「水彩风格」）
NAME_STYLE_ALL = dict(STYLE_ALIAS, **NAME_STYLE)
NAME_TONE_ALL = dict(TONE_ALIAS, **NAME_TONE)


def name_hit(t, exact, mapping=None):
    """文件名 token 归一：先精确匹配，再按「包含」匹配（如「水彩风格」→水彩、「冷色调」→冷）。"""
    if t in exact:
        return t
    for k, v in (mapping or {}).items():
        if k and k in t:
            return v
    return ""


def parse_name_tags(stem):
    """从手写文件名反推标签：能认的认，其余整段当主体描述。

    「中景 夜晚 女主咬勋章」→ 景别=中景 光线=夜晚 主体=女主咬勋章
    """
    s = re.sub(r"[-_]+$", "", str(stem or "").strip())
    toks = [t for t in re.split(r"[\s_]+", s) if t]
    row = {k: "" for k in TAG_KEYS}
    rest = []
    for t in toks:
        if not row["景别"] and (t in SHOT_SET or t in SHOT_MAP or t in NAME_SHOT):
            row["景别"] = (t if (t in SHOT_SET or t in NAME_SHOT)
                           else SHOT_MAP[t])
        elif not row["光线"] and name_hit(t, LIGHT_SET, NAME_LIGHT):
            row["光线"] = name_hit(t, LIGHT_SET, NAME_LIGHT)
        elif not row["人数"] and t in PEOPLE_SET:
            row["人数"] = t
        elif not row["朝向"] and (t in FACE_SET or t == "侧面"):
            row["朝向"] = "侧脸" if t == "侧面" else t
        elif not row["情绪"] and t in MOOD_SET:
            row["情绪"] = t
        elif not row["风格"] and name_hit(t, STYLE_SET, NAME_STYLE_ALL):
            row["风格"] = name_hit(t, STYLE_SET, NAME_STYLE_ALL)
        elif not row["色调"] and name_hit(t, TONE_SET, NAME_TONE_ALL):
            row["色调"] = name_hit(t, TONE_SET, NAME_TONE_ALL)
        elif not row["运镜"] and name_hit(t, CAM_SET, NAME_CAM):
            row["运镜"] = name_hit(t, CAM_SET, NAME_CAM)
        elif not row["场景"] and t in NAME_SCENE:
            row["场景"] = NAME_SCENE[t]
        else:
            rest.append(t)
    if row["人数"] == "无人" or row["景别"] == "空镜":
        row["人数"] = "无人"
        row["景别"] = "空镜"
        row["朝向"] = "无"
    row["主体"] = " ".join(rest)
    return row


def import_existing(out, log, prog, stage, detail, stop_event):
    """把素材库文件夹里已有的视频按文件名导入标签表：不改名、不复制、不动原文件。"""
    if os.path.basename(out.rstrip("\\/")) == "_标签数据":
        out = os.path.dirname(out.rstrip("\\/"))
    p = plan_dirs(out, True)
    data_dir, clips_dir, thumbs_dir = p["data"], p["clips"], p["thumbs"]
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(thumbs_dir, exist_ok=True)
    csv_path = p["csv"]

    stage("扫描已有片段")
    detail("读取文件名并解析标签")
    prog(2)
    names = sorted(f for f in os.listdir(out)
                   if f.lower().endswith((".mp4", ".mov", ".mkv", ".avi")))
    if not names:
        raise RuntimeError("这个文件夹里没有视频文件")

    cols = ["原文件", "编号"] + TAG_KEYS + ["新文件名", "模型原文", "耗时秒", "源大小", "源修改时间"]
    prev_rows = []
    if os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8-sig") as f:
                prev_rows = [r for r in csv.DictReader(f) if r.get("新文件名")]
        except Exception:
            prev_rows = []
    # ---- 改名处理 ----
    # 模型打标的行：文件不在但文件夹里有「唯一同尺寸」的新文件 → 认定为改名，自动重连，标签不丢
    # 文件名导入的行：直接丢弃，稍后按新文件名重新解析
    exist = set(names)
    free = [f for f in names]
    by_size = {}
    for f in names:
        try:
            by_size.setdefault(os.path.getsize(os.path.join(out, f)), []).append(f)
        except OSError:
            pass
    kept_rows, relinked, stale = [], 0, 0
    for r in prev_rows:
        nm = r.get("新文件名")
        if nm in exist:
            kept_rows.append(r)
            if nm in free:
                free.remove(nm)
            continue
        if (r.get("模型原文") or "") == "(文件名导入)":
            continue  # 交给下面按新名字重新导入
        try:
            sz = int(r.get("源大小") or 0)
        except Exception:
            sz = 0
        cand = [f for f in by_size.get(sz, []) if f in free]
        if len(cand) == 1:
            newn = cand[0]
            free.remove(newn)
            rr = dict(r)
            rr["新文件名"] = newn
            kept_rows.append(rr)
            relinked += 1
            log("改名重连：%s → %s（按大小匹配，标签保留）" % (nm, newn))
        else:
            stale += 1
    if relinked or stale:
        log("改名处理：重连 %d 个，清理失效记录 %d 条（文件已不在）" % (relinked, stale), "dim")
    prev_rows = kept_rows

    # 从文件名导入的行每次都重新解析（词表或文件名更新后重跑即可生效）；
    # 模型打标的行保持不动，不会因为重跑导入被覆盖。
    done = {r.get("新文件名") for r in prev_rows
            if (r.get("模型原文") or "") != "(文件名导入)"}
    todo = [f for f in names if f not in done]
    mx = 0
    for r in prev_rows:
        try:
            mx = max(mx, int(str(r.get("编号") or 0)))
        except Exception:
            pass

    log("素材库：%s" % out)
    log("文件夹里 %d 个视频，已在标签表 %d 个，本次导入 %d 个"
        % (len(names), len(names) - len(todo), len(todo)))
    if not todo:
        prog(100)
        stage("完成")
        detail("全部已导入，无需重复")
        return {"imported": 0, "csv": csv_path, "html": ""}

    new_rows = []
    for i, fn in enumerate(todo):
        if stop_event.is_set():
            raise Cancelled()
        r = parse_name_tags(os.path.splitext(fn)[0])
        mx += 1
        r["编号"] = "%04d" % mx
        r["原文件"] = ""
        r["新文件名"] = fn
        try:
            st = os.stat(os.path.join(out, fn))
            sz, mt = int(st.st_size), int(st.st_mtime)
        except OSError:
            sz, mt = 0, 0
        new_rows.append({"row": r, "raw": "(文件名导入)", "sec": 0.0, "sz": sz, "mt": mt})
        prog(int(3 + 20 * (i + 1) / len(todo)))

    by_name = {x["row"]["新文件名"] for x in new_rows}
    all_rows = [{"row": r, "raw": r.get("模型原文", ""), "sec": r.get("耗时秒", 0),
                 "sz": r.get("源大小", 0), "mt": r.get("源修改时间", 0)}
                for r in prev_rows if r.get("新文件名") not in by_name]
    all_rows += new_rows

    stage("写入标签表")
    detail("合并已有记录")
    prog(25)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for item in all_rows:
            d = dict(item["row"])
            d["模型原文"] = item["raw"]
            d["耗时秒"] = item["sec"]
            d["源大小"] = item.get("sz", 0)
            d["源修改时间"] = item.get("mt", 0)
            w.writerow({k: d.get(k, "") for k in cols})
    log("标签表：%s（累计 %d 条）" % (csv_path, len(all_rows)))

    # 缩略图 + 预览页（缩略图缺失的会在这里补，已存在的跳过）
    html_path = write_gallery(all_rows, data_dir, clips_dir, thumbs_dir, True,
                              log, prog, stage, detail, stop_event, p0=30, pspan=66)
    known = sum(1 for x in new_rows if x["row"]["景别"])
    log("导入完成：本次处理 %d 个（其中 %d 个认出景别/类别，其余只保留描述文字）"
        % (len(new_rows), known))
    prog(100)
    stage("完成")
    detail("全库可搜索")
    return {"imported": len(new_rows), "csv": csv_path, "html": html_path}


def import_tree(out, log, prog, stage, detail, stop_event):
    """选中的是总库目录时，自动对每个作品子文件夹各导一次（各有自己的 _标签数据）。"""
    def has_video(p):
        try:
            return any(f.lower().endswith((".mp4", ".mov", ".mkv", ".avi"))
                       for f in os.listdir(p))
        except OSError:
            return False

    if has_video(out):
        return import_existing(out, log, prog, stage, detail, stop_event)
    subs = [os.path.join(out, d) for d in sorted(os.listdir(out))
            if not d.startswith("_") and os.path.isdir(os.path.join(out, d))]
    targets = [p for p in subs if has_video(p)]
    if not targets:
        raise RuntimeError("这个文件夹里没有视频文件")
    total, imported = len(targets), 0
    for i, p in enumerate(targets):
        if stop_event.is_set():
            raise Cancelled()
        log("—— 第 %d/%d 部：%s ——" % (i + 1, total, os.path.basename(p)))
        r = import_existing(
            p, log,
            prog=lambda v, i=i: prog(int(100 * (i + max(0, min(100, v)) / 100.0) / total)),
            stage=stage, detail=lambda t, i=i: detail("%d/%d · %s" % (i + 1, total, t)),
            stop_event=stop_event)
        imported += r.get("imported", 0)
    log("全库导入完成：%d 部作品，共 %d 个片段" % (total, imported))
    prog(100)
    stage("完成")
    detail("全库可搜索")
    return {"imported": imported, "csv": "", "html": ""}


def build_library_index(root_dir, log, prog, stage, detail, stop_event):
    """把总库下各作品的标签表合并成一张总预览页 + 一份总标签表（不动任何视频）。"""
    stage("扫描作品")
    detail("收集各作品的标签表")
    prog(2)
    works = [d for d in sorted(os.listdir(root_dir))
             if not d.startswith("_") and os.path.isdir(os.path.join(root_dir, d))]
    items, csv_rows = [], []
    facets = {k: collections.Counter() for k in ("景别", "人数", "朝向", "光线", "情绪", "场景",
                                                  "风格", "色调", "字幕", "运镜")}
    work_count = 0
    for wi, w in enumerate(works):
        if stop_event.is_set():
            raise Cancelled()
        wp = os.path.join(root_dir, w)
        csvp = os.path.join(wp, "_标签数据", "tags.csv")
        if not os.path.isfile(csvp):
            continue
        try:
            with open(csvp, encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f) if r.get("新文件名")]
        except Exception:
            continue
        work_count += 1
        for r in rows:
            name = r["新文件名"]
            if not os.path.isfile(os.path.join(wp, name)):
                continue
            th = "%s.jpg" % os.path.splitext(name)[0]
            if not os.path.isfile(os.path.join(wp, "_标签数据", "thumbs", th)):
                continue
            items.append({"f": w + "/" + name,
                          "t": "%s/_标签数据/thumbs/%s" % (w, th),
                          "id": str(r.get("编号", "")),
                          "path": os.path.join(wp, name), "bin": w,
                          "s": r.get("景别", ""), "n": r.get("人数", ""),
                          "d": r.get("朝向", ""), "o": r.get("主体", ""),
                          "a": r.get("动作", ""), "p": r.get("场景", ""),
                          "l": r.get("光线", ""), "m": r.get("情绪", ""),
                          "g": r.get("风格", ""), "h": r.get("色调", ""),
                          "u": r.get("字幕", ""), "v": r.get("运镜", ""),
                          "w": w})
            for k in facets:
                for tok in str(r.get(k, "")).split():
                    if tok and tok != "无":
                        facets[k][tok] += 1
            rr = dict(r)
            rr["作品"] = w
            csv_rows.append(rr)
        prog(int(2 + 25 * (wi + 1) / max(1, len(works))))

    if not items:
        raise RuntimeError("这个文件夹下没有找到已导入的作品。\n"
                           "先对各作品执行「导入已有片段」，再回到总库目录生成索引。")
    stage("生成全库预览")
    detail("合并 %d 部作品、%d 个片段" % (work_count, len(items)))
    prog(35)
    facets_all = {k: [{"v": v, "c": n} for v, n in c.most_common(30)]
                  for k, c in facets.items()}
    wc = collections.Counter(it["w"] for it in items)
    facets_all["作品"] = [{"v": v, "c": n} for v, n in wc.most_common(60)]
    data = json.dumps({"items": items, "facets": facets_all}, ensure_ascii=False)
    data = (data.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026"))
    html_path = os.path.join(root_dir, "全库预览.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(GALLERY_HTML.replace("__DATA__", data).replace("__PRPORT__", str(PR_HTTP_PORT)))
    log("全库预览：%s（%d 部作品，%d 个片段）" % (html_path, work_count, len(items)))
    prog(80)

    cols = ["作品", "原文件", "编号"] + TAG_KEYS + ["新文件名", "模型原文", "耗时秒", "源大小", "源修改时间"]
    csv_out = os.path.join(root_dir, "全库标签.csv")
    with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wtr.writeheader()
        for r in csv_rows:
            wtr.writerow({k: r.get(k, "") for k in cols})
    log("全库标签表：%s（%d 条）" % (csv_out, len(csv_rows)))
    prog(100)
    stage("完成")
    detail("全库可搜索")
    return html_path


def run_pipeline(src, out, nframes, make_gallery, resume, lib_mode, log, prog, stage, detail,
                 counts, stop_event, tag_only=False):
    if tag_only:
        out = src  # 只补标签：原地写回素材目录的 _标签数据
    if os.path.basename(out.rstrip("\\/")) == "_标签数据":
        out = os.path.dirname(out.rstrip("\\/"))  # 兜底：管理目录不能当输出目录
    os.makedirs(out, exist_ok=True)
    _p = plan_dirs(out, True if tag_only else lib_mode)
    clips_dir, data_dir = _p["clips"], _p["data"]
    thumbs_dir, frame_dir, csv_path = _p["thumbs"], _p["frames"], _p["csv"]
    if lib_mode and not tag_only and os.path.abspath(out).lower() == os.path.abspath(src).lower():
        raise RuntimeError("并入素材库时，输出目录不能和素材目录相同")
    for d in (clips_dir, thumbs_dir, frame_dir):
        os.makedirs(d, exist_ok=True)

    files = sorted([f for f in os.listdir(src) if f.lower().endswith((".mp4", ".mov", ".mkv", ".avi"))])
    if not files:
        raise RuntimeError("这个文件夹里没有视频文件")

    stage("加载模型")
    detail("正在加载 MiniCPM-V-4-6 …（首次约 10~20 秒）")
    log("素材目录：%s" % src)
    if tag_only:
        log("模式：只补标签（原地写回，不改名、不复制）")
    log("输出目录：%s%s" % (out, "（并入素材库：片段直接入库，标签数据收进 _标签数据）"
                            if (lib_mode and not tag_only) else ""))
    log("共 %d 个片段，每段抽 %d 帧" % (len(files), nframes))
    log("ffmpeg：%s" % ffmpeg_version())

    from PIL import Image
    import torch
    from A_coreUtils.aftertreatment.shot_analyzer import ShotAnalyzer

    an = ShotAnalyzer()
    an.load_model()
    _GPU["an"] = an
    _GPU["torch"] = torch
    an.max_slice_nums = 1
    an.downsample_mode = "4x"
    log("模型就绪")

    # 续跑：读已有结果
    cached = {}
    if resume and os.path.exists(csv_path):
        try:
            bad = 0
            cur = set(files)
            with open(csv_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if tag_only:
                        nm = str(r.get("新文件名") or "").strip()
                        if not nm:
                            continue
                        rr = dict(r)
                        rr["原文件"] = nm      # 原地补标：文件名就是原文件
                        if row_complete(rr, src):
                            cached[nm] = r
                        elif nm in cur:
                            bad += 1
                        continue
                    if row_complete(r, src):
                        cached[r["原文件"]] = r
                    elif r.get("原文件") in cur:
                        # 只统计本批文件；库里其它批次的行不算「不完整」
                        bad += 1
            log("读到上次结果 %d 条可复用；%d 条需重新识别（字段不全或源视频已变）"
                % (len(cached), bad))
        except Exception:
            cached = {}

    # 原地补标：沿用已有编号，新片段从最大编号往后排
    existing, next_num = {}, 0
    if tag_only and os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    nm = str(r.get("新文件名") or "").strip()
                    if nm:
                        existing[nm] = r
                    try:
                        next_num = max(next_num, int(str(r.get("编号") or 0)))
                    except Exception:
                        pass
        except Exception:
            pass

    rows = []
    fails = []  # (文件名, 原因, 模型原文)：跑完统一汇总，原文写进 解析失败.txt
    ok = fail = 0
    t0 = time.time()
    for i, fn in enumerate(files):
        if stop_event.is_set():
            raise Cancelled()
        path = os.path.join(src, fn)
        # 编号取文件名最后一段；切片名形如 07_0062 → 0062。
        # 手写命名的素材（「中景 夜晚 女主咬勋章」）没有编号，用序号兜底，
        # 否则整串文件名会被当成编号拼进新文件名。
        num = os.path.splitext(fn)[0].split("_")[-1].strip()
        if not re.fullmatch(r"\d{1,6}", num):
            num = "%04d" % (i + 1)
        prog(int(3 + 82 * i / len(files)))
        detail("%d / %d · %s" % (i + 1, len(files), fn))
        ts = time.time()

        if fn in cached:
            old = cached[fn]
            row = {k: old.get(k, "") for k in TAG_KEYS}
            row["原文件"] = fn
            raw = old.get("模型原文", "")
            cost = 0.0
        else:
            frames = grab_frames(path, frame_dir, nframes, log=log)
            if not frames:
                fails.append((fn, "抽不到帧（文件可能损坏或没有视频流）", ""))
                log("跳过（抽不到帧）：%s" % fn, ERR)
                fail += 1
                counts(ok, fail, len(files))
                continue
            imgs = []
            for p in frames:
                with Image.open(p) as _im:
                    imgs.append(_im.convert("RGB"))
            content = [{"type": "image", "image": im} for im in imgs]
            content.append({"type": "text", "text": PROMPT.replace("{n}", str(len(imgs)))})
            msgs = [{"role": "user", "content": content}]
            raw = ""
            parsed = None
            for _attempt in range(2):
                if _attempt:
                    # 第一次失败常是格式跑偏，重试时补一句硬约束再要一次
                    content[-1]["text"] += "\n（重答一次：只输出一行 JSON，不要解释、不要多余字段）"
                try:
                    inputs = an._processor.apply_chat_template(
                        msgs, tokenize=True, add_generation_prompt=True,
                        return_dict=True, return_tensors="pt").to(an.device)
                    with torch.no_grad():
                        o = an._model.generate(**inputs, max_new_tokens=320, do_sample=False,
                                               repetition_penalty=1.05,
                                               eos_token_id=an._processor.tokenizer.eos_token_id)
                    raw = an._processor.tokenizer.decode(o[0][inputs["input_ids"].shape[1]:],
                                                         skip_special_tokens=True)
                except Exception as e:
                    raw = "EXC: %s" % e
                parsed = parse_tags(raw)
                if parsed:
                    break
            if not parsed:
                reason = parse_fail_reason(raw)
                fails.append((fn, reason, str(raw or "")))
                log("跳过（解析失败）：%s —— %s" % (fn, reason), ERR)
                fail += 1
                counts(ok, fail, len(files))
                continue
            row = dict(parsed)
            row["原文件"] = fn
            cost = round(time.time() - ts, 1)
            # 帧文件保留到景别复算之后统一清理

        if tag_only:
            row["新文件名"] = fn
            old = existing.get(fn) or {}
            keep = str(old.get("编号") or "").strip()
            if keep:
                row["编号"] = keep
            else:
                next_num += 1
                row["编号"] = "%04d" % next_num
        else:
            row["编号"] = num
        sz, mt = src_stat(path)
        # 模型原文整段存进 CSV；同时记下源文件指纹供续跑比对
        rows.append({"row": row, "raw": str(raw or ""), "sec": cost,
                     "sz": sz, "mt": mt})
        ok += 1
        counts(ok, fail, len(files))
        if fn not in cached:
            log("%s  %s" % (row.get("编号") or num,
                            " · ".join(row.get(k, "") for k in KEYS if row.get(k))))

    if stop_event.is_set():
        raise Cancelled()
    log("推理完成：成功 %d，失败 %d，用时 %.0f 秒" % (ok, fail, time.time() - t0))
    if fails:
        try:
            with open(os.path.join(data_dir, "解析失败.txt"), "w", encoding="utf-8") as _f:
                for _fn, _reason, _raw in fails:
                    _f.write("【%s】%s\n%s\n%s\n\n" % (
                        _fn, _reason, "-" * 40, (_raw or "").strip() or "（无输出）"))
            log("失败原因与模型原文已写入 解析失败.txt（在输出目录里）", "dim")
        except Exception:
            pass

    # 先卸掉 MiniCPM 再加载 DinoV2：两个大模型不再同时占显存
    an = None
    inputs = o = content = imgs = msgs = None
    release_gpu()
    log("已释放打标模型显存，开始判景别")

    hit = 0
    for item in rows:
        sp = os.path.join(src, item["row"]["原文件"])
        if frame_valid(frame_path(frame_dir, item["row"]["原文件"],
                                  nframes, 0, frame_sig(sp)), sp):
            hit += 1
    log("帧缓存：%d / %d 段可直接复用" % (hit, len(rows)))

    # ---- 景别复算 ----
    stage("判景别")
    detail("DinoV2 对每段的两帧分类")
    prog(86)
    from A_coreUtils.aftertreatment.shot_type_classifier import DinoV2ShotClassifier
    clf = DinoV2ShotClassifier()
    clf.load()
    _GPU["clf"] = clf
    import cv2
    dino_ok = 0
    if not rows:
        log("没有可用结果，跳过景别复算", ERR)
    for i, item in enumerate(rows):
        if stop_event.is_set():
            raise Cancelled()
        row = item["row"]
        src_path = os.path.join(src, row["原文件"])
        sig = frame_sig(src_path)
        labs = []
        for k in range(nframes):
            p = frame_path(frame_dir, row["原文件"], nframes, k, sig)
            if not frame_valid(p, src_path):
                # 缓存缺失或源视频已变：按同一套目标时刻规则重新抽这一帧
                vd = dur_of(src_path)
                r = None
                for off in frame_offsets(vd, nframes, k):
                    r = run_quiet([FF, "-y", "-v", "error", "-ss", "%.3f" % off,
                                        "-i", src_path, "-vf", "scale=512:-1",
                                        "-vframes", "1", "-q:v", "3", p],
                                       check=False, capture_output=True)
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        break
                if i < 3 and not os.path.exists(p) and r is not None:
                    log("景别补帧失败 %s：%s" % (
                        row["原文件"], ff_err(r) or "目标时刻附近均无有效画面"), ERR)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                # 用 PIL 读，cv2.imread 在中文路径下会失败
                try:
                    from PIL import Image as _PIL
                    import numpy as _np
                    with _PIL.open(p) as _im:
                        fr = cv2.cvtColor(_np.array(_im.convert("RGB")), cv2.COLOR_RGB2BGR)
                except Exception as e:
                    fr = None
                    if i < 3:
                        log("景别读帧失败 %s：%s" % (row["原文件"], e), ERR)
                if fr is not None:
                    try:
                        labs.append(clf._predict_frame(fr))
                    except Exception as e:
                        if dino_ok == 0 and len(rows) and i < 3:
                            log("景别判定失败 %s：%s" % (row["原文件"], e), ERR)
            else:
                if i < 3:
                    log("景别缺帧 %s：%s" % (row["原文件"], p), ERR)
        if labs:
            if len(labs) > 1 and labs[0][0] == labs[1][0]:
                shot = labs[0][0]
            else:
                shot = max(labs, key=lambda x: x[1])[0]
            shot = str(shot or "").strip()
            if shot not in SHOT_SET:
                # 分类器返回非法标签时先沿用 VLM 结果，都没有才兜底中景
                shot = str(row.get("景别") or "").strip()
                if shot not in SHOT_SET:
                    shot = "中景"
                if i < 3:
                    log("景别结果非法，沿用标签值：%s" % row["原文件"], ERR)
            if row["人数"] == "无人":
                shot = "空镜"
            row["景别"] = shot
            dino_ok += 1
        if rows:
            prog(int(86 + 6 * (i + 1) / len(rows)))
    log("景别复算完成：%d 段采用 DinoV2 结果" % dino_ok)

    # ---- 规范化 + 落地 ----
    stage("整理标签")
    detail("统一词序、拆分同义词")
    prog(93)

    vocabs, freqs = {}, {}
    for f in ("主体", "动作", "场景"):
        toks = []
        for item in rows:
            toks += split_tokens(item["row"].get(f, ""))
        cnt = collections.Counter(toks)
        vocabs[f] = {t for t, n in cnt.items() if n >= 3 and len(t) >= 2}
        freqs[f] = cnt

    for item in rows:
        row = item["row"]
        for f in ("主体", "动作", "场景"):
            toks = []
            for t in split_tokens(row.get(f, "")):
                toks += segment(t, vocabs[f])
            uniq = []
            for t in toks:
                if t not in uniq and t not in NOISE:
                    uniq.append(t)
            uniq.sort(key=lambda t: (-freqs[f].get(t, 0), t))
            row[f] = " ".join(uniq)

    # ---- 复制改名 / 原地补标 ----
    prog(95)
    if tag_only:
        stage("写回标签")
        detail("不改名、不复制，只更新标签表")
        log("只补标签：不复制、不改名，标签直接写回 %s" % csv_path, "dim")
    else:
        stage("生成文件")
        detail("复制原片并按标签改名")
    # 只清理上一次由本程序生成的文件（清单里记着），不碰用户放进来的视频
    manifest = os.path.join(data_dir if lib_mode else clips_dir, "_tagmaster_manifest.json")
    if not tag_only and os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as mf:
                prev = json.load(mf)
            for f in prev:
                if not isinstance(f, str) or not f.lower().endswith(".mp4"):
                    continue
                fp = os.path.join(clips_dir, os.path.basename(f))
                if os.path.isfile(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
        except Exception:
            pass

    done_files = []
    for item in ([] if tag_only else rows):
        row = item["row"]
        src_path = os.path.join(src, row["原文件"])
        if not os.path.exists(src_path):
            row["新文件名"] = ""
            continue
        name = build_name_lib(row) if lib_mode else build_name(row, row["编号"])
        # 输出目录很深时，光截文件名仍可能撞上 MAX_PATH，退化成纯编号名
        if os.name == "nt" and len(os.path.join(clips_dir, name)) > 240:
            name = "%s.mp4" % (re.sub(r'[\\/:*?"<>|]', "", str(row["编号"])) or "0000")
        base, ext = os.path.splitext(name)
        n = 2 if lib_mode else 1
        while os.path.exists(os.path.join(clips_dir, name)):
            # 库模式沿用「-2」后缀，普通模式仍是「_1」；两条路都绝不覆盖同名文件
            name = "%s%s%d%s" % (base, "-" if lib_mode else "_", n, ext)
            n += 1
        try:
            shutil.copy2(src_path, os.path.join(clips_dir, name))
            row["新文件名"] = name
            done_files.append(name)
        except Exception as e:
            row["新文件名"] = ""
            log("复制失败 %s：%s" % (row["原文件"], e), ERR)

    if not tag_only:
        with open(manifest, "w", encoding="utf-8") as mf:
            json.dump(done_files, mf, ensure_ascii=False)

    cols = ["原文件", "编号"] + TAG_KEYS + [
        "新文件名", "模型原文", "耗时秒", "源大小", "源修改时间"]
    prev_rows = []
    if (lib_mode or tag_only) and os.path.exists(csv_path):
        # 库模式：标签表跨批次合并，同名的以本次结果为准，不会把上一部/上一批挤掉
        try:
            with open(csv_path, encoding="utf-8-sig") as f:
                prev_rows = [r for r in csv.DictReader(f)
                             if (r.get("新文件名") or r.get("原文件"))]
        except Exception:
            prev_rows = []
    if tag_only:
        # 原地补标：按文件名合并，同名的以本次结果为准
        cur_keys = {item["row"]["新文件名"] for item in rows}
        kept_prev, stale = [], 0
        for r in prev_rows:
            nm = str(r.get("新文件名") or "")
            if nm in cur_keys:
                continue
            # 文件已改名 / 被删：旧行留着就是指向不存在视频的僵尸记录
            if nm and os.path.exists(os.path.join(clips_dir, nm)):
                kept_prev.append(r)
            else:
                stale += 1
        if stale:
            log("清理失效记录 %d 条（文件已改名或删除）" % stale, "dim")
    else:
        cur_keys = {item["row"]["原文件"] for item in rows}
        kept_prev = [r for r in prev_rows if r.get("原文件") not in cur_keys]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in kept_prev:
            w.writerow({k: r.get(k, "") for k in cols})
        for item in rows:
            d = dict(item["row"])
            d["模型原文"] = item["raw"]
            d["耗时秒"] = item["sec"]
            d["源大小"] = item.get("sz", 0)
            d["源修改时间"] = item.get("mt", 0)
            w.writerow({k: d.get(k, "") for k in cols})
    log("标签表：%s（本次 %d 条，累计 %d 条）"
        % (csv_path, len(rows), len(kept_prev) + len(rows)))
    if not tag_only:
        log(("入库片段：%d 个 → %s" if lib_mode else "改名副本：%d 个 → %s")
            % (len(done_files), clips_dir))

    # ---- 预览页 ----
    html_path = ""
    if make_gallery:
        gallery_rows = ([{"row": r} for r in kept_prev] + rows) if tag_only else rows
        html_path = write_gallery(gallery_rows, data_dir, clips_dir, thumbs_dir,
                                  True if tag_only else lib_mode,
                                  log, prog, stage, detail, stop_event)

    # 帧缓存留在 out/cache/ 供下次续跑复用，不再删除
    clf = None
    release_gpu()

    # 跑完点名失败片段：日志最后一条，一定看得见
    if fails:
        log("本次失败 %d 个：%s" % (len(fails), "、".join(fn for fn, _, _ in fails)), ERR)
        log("勾选「续跑」再点一次开始打标，成功的会直接跳过，只重试这几个", "dim")

    prog(100)
    stage("完成")
    detail("全部完成")
    return {"csv": csv_path, "clips": clips_dir, "html": html_path,
            "ok": ok, "fail": fail, "total": len(files),
            "sec": round(time.time() - t0)}


# ---------------- Windows 原生拖放 ----------------
# 只认这些扩展名是「视频」，用来判断拖进来的东西是不是素材
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".ts", ".wmv", ".flv")


# ---------------- 发送到 Premiere（CEP 面板桥） ----------------
# 接口设计参考 TuanToolKit（原作者）的文件轮询桥：主程序把命令 JSON 写到
# %APPDATA%\TagMaster\bin\tm_cmd.json，PR 里的「TagMaster 素材桥」面板每秒
# 轮询一次，读到就导入并删除文件。命令文件名和包装键刻意与原作者的
# pr_cmd.json / pr_cmd 不同，两套面板同时装也不会互吃对方的命令。
PR_CMD_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TagMaster", "bin")
PR_HTTP_PORT = 31765


class _PRSendHandler(http.server.BaseHTTPRequestHandler):
    """只服务两件事：GET /ping（面板/预览页探活）、POST /send（写 PR 命令）。
    只绑 127.0.0.1，不暴露局域网；请求体上限 4 MB。"""

    def _reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/ping":
            self._reply(200, {"ok": True, "name": "TagMaster", "ver": 1})
        else:
            self._reply(404, {"ok": False, "msg": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/send":
            self._reply(404, {"ok": False, "msg": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 4_000_000:
                raise ValueError("请求体大小异常")
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            cmd = data.get("tm_cmd") if isinstance(data, dict) else None
            items = cmd.get("items") if isinstance(cmd, dict) else None
            clean = [{"path": os.path.abspath(it["path"]), "bin": str(it.get("bin") or "")}
                     for it in (items or []) if isinstance(it, dict) and it.get("path")]
            if not clean:
                raise ValueError("没有可发送的片段")
            os.makedirs(PR_CMD_DIR, exist_ok=True)
            with open(os.path.join(PR_CMD_DIR, "tm_cmd.json"), "w", encoding="utf-8") as f:
                json.dump({"tm_cmd": {"action": "importMany", "items": clean,
                                      "createdAt": int(time.time() * 1000)}},
                          f, ensure_ascii=False)
            self._reply(200, {"ok": True, "count": len(clean)})
        except Exception as e:
            self._reply(400, {"ok": False, "msg": str(e)})

    def log_message(self, *a):
        pass  # 不往控制台刷请求日志


def start_pr_server():
    """起本机发送服务；端口被占就返回 None（预览页会自动回退为下载文件）。"""
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PR_HTTP_PORT), _PRSendHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    except OSError:
        return None


def write_pr_command(paths, bin_name=""):
    """把一批视频路径写成 PR 桥命令，返回 (数量, 命令文件路径)。"""
    items = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p) and p.lower().endswith(VIDEO_EXT):
            items.append({"path": p, "bin": str(bin_name or "")})
    if not items:
        return 0, ""
    os.makedirs(PR_CMD_DIR, exist_ok=True)
    cmd_path = os.path.join(PR_CMD_DIR, "tm_cmd.json")
    with open(cmd_path, "w", encoding="utf-8") as f:
        json.dump({"tm_cmd": {"action": "importMany", "items": items,
                              "createdAt": int(time.time() * 1000)}},
                  f, ensure_ascii=False)
    return len(items), cmd_path


def relay_pr_command_file(src_path):
    """把预览页导出的 tm_cmd*.json 转写进 %APPDATA%，供 PR 面板接收。

    返回 (数量, 提示文本)；文件内容不合法时返回 (0, 原因)。
    """
    try:
        with open(src_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return 0, "文件读不出来：%s" % e
    cmd = data.get("tm_cmd") if isinstance(data, dict) else None
    items = cmd.get("items") if isinstance(cmd, dict) else None
    if not isinstance(items, list):
        return 0, "这不是预览页导出的发送文件（缺少 tm_cmd.items）。"
    clean = []
    for it in items:
        if isinstance(it, dict) and it.get("path"):
            clean.append({"path": os.path.abspath(it["path"]), "bin": str(it.get("bin") or "")})
    if not clean:
        return 0, "文件里没有片段路径。"
    os.makedirs(PR_CMD_DIR, exist_ok=True)
    cmd_path = os.path.join(PR_CMD_DIR, "tm_cmd.json")
    with open(cmd_path, "w", encoding="utf-8") as f:
        json.dump({"tm_cmd": {"action": "importMany", "items": clean,
                              "createdAt": int(time.time() * 1000)}},
                  f, ensure_ascii=False)
    return len(clean), cmd_path



DND_STATUS = ""
# 拖放消息的落点：窗口过程回调里绝不能调 tkinter/Tcl（在 Win32 消息分发中
# 重入解释器会把界面卡死甚至崩进程），路径先落到普通队列，
# 由界面自带的 _pump 定时器在主线程取出。
_DROP_QUEUE = queue.Queue()


def enable_native_dnd(root, on_drop):
    """接收资源管理器拖入的文件/文件夹，只用标准库 ctypes，不需要装任何包。

    安全要点（上一版「拖入就卡死闪退」的根源就在这里，别改回去）：
    1. 只接管 Tk 的客户区窗口（winfo_id()），正文覆盖整个应用区域，
       鼠标落在正文上时 WM_DROPFILES 发给它；最外层框架窗口不再碰。
    2. 窗口过程回调里只做三件事：读路径、释放系统内存、把路径塞进普通队列。
       绝不调用任何 tkinter/Tcl 接口——在 Win32 消息分发中重入解释器
       会把界面卡死甚至崩进程。消费由界面自带的 _pump 定时器在主线程完成。
    3. 整个回调包在 try/except 里，任何异常都降级成转发原窗口过程，
       不把异常抛回 Win32 层。
    """
    global DND_STATUS
    DND_STATUS = ""
    if os.name != "nt":
        DND_STATUS = "不是 Windows"
        return False
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        s32 = ctypes.windll.shell32
        WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4
        LRESULT = ctypes.c_ssize_t

        # 注意：DragAcceptFiles 属于 shell32，不是 user32（之前放错 DLL 导致整个拖放静默失效）
        s32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        u32.CallWindowProcW.restype = LRESULT
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        s32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                       wintypes.LPWSTR, wintypes.UINT]
        s32.DragQueryFileW.restype = wintypes.UINT
        s32.DragFinish.argtypes = [ctypes.c_void_p]

        hwnd = wintypes.HWND(root.winfo_id())
        if not hwnd or not hwnd.value:
            DND_STATUS = "拿不到窗口句柄（窗口还没显示）"
            return False

        setter = getattr(u32, "SetWindowLongPtrW", None) or u32.SetWindowLongW
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        def read_drop(hdrop):
            n = s32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            out = []
            for i in range(n):
                buf = ctypes.create_unicode_buffer(1024)
                s32.DragQueryFileW(hdrop, i, buf, 1024)
                if buf.value:
                    out.append(buf.value)
            return out

        state = {"old": None}

        def wnd_proc(hwnd_, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                hdrop = ctypes.c_void_p(wparam)
                paths = []
                try:
                    paths = read_drop(hdrop)
                except Exception:
                    paths = []
                try:
                    s32.DragFinish(hdrop)
                except Exception:
                    pass
                if paths:
                    try:
                        _DROP_QUEUE.put(tuple(paths))
                    except Exception:
                        pass
                return 0
            try:
                if state["old"] is None:
                    return u32.DefWindowProcW(hwnd_, msg, wparam, lparam)
                return u32.CallWindowProcW(state["old"], hwnd_, msg, wparam, lparam)
            except Exception:
                return 0

        proc = WNDPROC(wnd_proc)
        old = setter(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
        if not old:
            DND_STATUS = "接管窗口过程失败（SetWindowLongPtr 返回 0）"
            return False
        state["old"] = old
        s32.DragAcceptFiles(hwnd, True)

        try:  # 拖放方权限更高（如管理员资源管理器）时让 WM_DROPFILES 穿过 UIPI
            u32.ChangeWindowMessageFilter(WM_DROPFILES, 1)
        except Exception:
            pass

        # 回调对象必须保活，被 GC 回收会让窗口过程指向无效地址
        # （on_drop 参数保留是为了兼容旧调用点；真正的消费在 _pump 里调 _on_drop）
        root._dnd_keepalive = (proc, state)
        DND_STATUS = "已启用"
        return True
    except Exception as e:
        DND_STATUS = "%s: %s" % (type(e).__name__, e)
        return False


# ---------------- 界面 ----------------
# ---------------- PR XML 切片段 ----------------
def url_to_path(u):
    """把 FCP XML 的 pathurl 转成本机路径；兼容 Windows 盘符与 macOS 挂载路径。"""
    from urllib.parse import unquote
    s = (u or "").strip()
    low = s.lower()
    for pre in ("file://localhost/", "file:///", "file://./", "file://"):
        if low.startswith(pre):
            s = s[len(pre):]
            break
    s = unquote(s).replace("\\", "/")
    # 某些 PR 导出会留下前导斜杠：/D:/xxx 在 Windows 上不存在，去掉它
    if re.match(r"^/[A-Za-z]:[\\/]", s):
        s = s[1:]
    if re.match(r"^[A-Za-z]:[\\/]", s):
        return os.path.normpath(s)
    # /Volumes/... 这类 macOS 路径在 Windows 上无法直接定位，
    # 交给 run_cut 的「源视频目录按文件名兜底」处理
    return os.path.normpath(s if s.startswith("/") else "/" + s)


def parse_fcp_xml(xml_path):
    """解析 FCP7(xmeml) 序列，返回 (序列名, fps, 片段列表)"""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(xml_path).getroot()
    except Exception as e:
        raise RuntimeError("XML 解析失败：%s" % e)
    seq = root.find("sequence")
    if seq is None:
        seq = root.find(".//sequence")
    if seq is None:
        raise RuntimeError("这不是 FCP7 序列 XML（找不到 <sequence>）")
    name = (seq.findtext("name") or os.path.splitext(os.path.basename(xml_path))[0]).strip()
    tb, ntsc = 24, False
    rate = seq.find("rate")
    if rate is not None:
        try:
            tb = int(rate.findtext("timebase") or 24)
        except Exception:
            tb = 24
        ntsc = (rate.findtext("ntsc") or "").strip().upper() == "TRUE"
    fps = tb * 1000.0 / 1001.0 if ntsc else float(tb)

    fmap = {}
    for f in root.iter("file"):
        fid, pu = f.get("id"), f.findtext("pathurl")
        if fid and pu and fid not in fmap:
            fmap[fid] = url_to_path(pu)

    items, seen = [], set()
    for tr in root.findall(".//sequence/media/video/track"):
        for ci in tr.findall("clipitem"):
            try:
                i = int(float(ci.findtext("in") or 0))
                o = int(float(ci.findtext("out") or 0))
                st = int(float(ci.findtext("start") or 0))
            except Exception:
                continue
            if o <= i:
                continue
            f = ci.find("file")
            path = ""
            if f is not None:
                path = fmap.get(f.get("id"), "") or url_to_path(f.findtext("pathurl"))
            key = (path, i, o, st)
            if key in seen:
                continue
            seen.add(key)
            items.append({"path": path, "in": i, "out": o, "start": st,
                          "name": (ci.findtext("name") or "").strip()})
    items.sort(key=lambda x: x["start"])
    return name, fps, items


def cut_one(ff, src, ss, dur, dst, keep_audio, try_nvenc=True):
    """返回 (是否成功, ffmpeg 错误信息)：失败时把 stderr 带出去方便排查。"""
    base = [ff, "-y", "-v", "error", "-ss", "%.4f" % ss, "-i", src, "-t", "%.4f" % dur]
    audio = ["-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"]
    v_nv = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    v_cpu = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    tmp = dst + ".tmp.mp4"
    last = ""
    try:
        for v in ([v_nv, v_cpu] if try_nvenc else [v_cpu]):
            r = run_quiet(base + v + audio + [tmp], capture_output=True)
            if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                try:
                    os.replace(tmp, dst)  # 编码成功才替换，失败不会留下损坏的正式文件
                    return True, ""
                except OSError as e:
                    last = "替换正式文件失败（可能正被占用）：%s" % e
                    break
            last = ff_err(r)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False, last


def remap_path(path, old_pre, new_pre):
    """把 XML 里的旧路径前缀替换成新路径前缀（按目录边界，避免误伤同名前缀）。"""
    if not path or not old_pre or not new_pre:
        return path
    p = path.replace("/", "\\")
    o = old_pre.replace("/", "\\").rstrip("\\")
    if o and p.lower().startswith(o.lower()):
        rest = p[len(o):]
        if not rest or rest[0] == "\\":
            return os.path.normpath(new_pre.rstrip("\\/") + rest)
    return path


def run_cut(xml_path, out_dir, src_dir, keep_audio, *, log, prog, stage, detail, counts,
            stop_event, old_pre="", new_pre=""):
    os.makedirs(out_dir, exist_ok=True)
    stage("解析 XML")
    detail("读取序列与剪辑点")
    log("XML：%s" % xml_path)
    log("ffmpeg：%s" % ffmpeg_version())
    seq, fps, items = parse_fcp_xml(xml_path)
    if not items:
        raise RuntimeError("XML 里没有找到任何片段")
    log("序列「%s」 %d 段  帧率 %.3f" % (seq, len(items), fps))

    if old_pre and new_pre:
        hit = 0
        for it in items:
            np_ = remap_path(it["path"], old_pre, new_pre)
            if np_ != it["path"]:
                it["path"] = np_
                hit += 1
        log("路径替换：%d 段改为「%s」下的文件" % (hit, new_pre))

    miss = [it for it in items if not (it["path"] and os.path.exists(it["path"]))]
    if miss and src_dir and os.path.isdir(src_dir):
        fixed = 0
        for it in miss:
            base = os.path.basename((it["path"] or "").replace("\\", "/"))
            cand = os.path.join(src_dir, base)
            if base and os.path.exists(cand):
                it["path"] = cand
                fixed += 1
        log("路径失效 %d 段，用源目录修复 %d 段" % (len(miss), fixed))
        miss = [it for it in items if not (it["path"] and os.path.exists(it["path"]))]
    if len(miss) == len(items):
        raise RuntimeError("全部源文件都找不到。请在「源视频目录」里指定原片所在文件夹。")

    stage("切片段")
    ok = fail = skip = 0
    t0 = time.time()
    total = len(items)
    for k, it in enumerate(items):
        if stop_event.is_set():
            raise Cancelled()
        prog(int(2 + 96.0 * k / total))
        detail("%d / %d · 第 %d 帧 → 第 %d 帧" % (k + 1, total, it["in"], it["out"]))
        if not (it["path"] and os.path.exists(it["path"])):
            skip += 1
            counts(ok, fail, skip)
            continue
        ss = it["in"] / fps
        dur = (it["out"] - it["in"]) / fps
        name = "%s_%04d.mp4" % (re.sub(r'[\\/:*?"<>|]', "", seq), k + 1)
        dst = os.path.join(out_dir, name)
        good, why = cut_one(FF, it["path"], ss, dur, dst, keep_audio)
        if good:
            ok += 1
        else:
            fail += 1
            log("失败：第 %d 段（%s）%s" % (
                k + 1, os.path.basename(it["path"]),
                "｜ffmpeg：" + why if why else ""), ERR)
        counts(ok, fail, skip)
        if (k + 1) % 25 == 0:
            el = time.time() - t0
            log("进度 %d/%d  用时 %.0f 秒  预计剩余 %.0f 秒" % (
                k + 1, total, el, el / (k + 1) * (total - k - 1)))
    sec = round(time.time() - t0)
    prog(100)
    stage("完成")
    detail("成功 %d 段" % ok)
    log("切完：成功 %d，失败 %d，跳过 %d，用时 %d 秒" % (ok, fail, skip, sec))
    return {"out": out_dir, "ok": ok, "fail": fail, "skip": skip,
            "total": total, "sec": sec, "seq": seq}


# ---------------- 环境自检 ----------------
SHOT_PREPROC = """{
  "crop_size": {
    "height": 518,
    "width": 518
  },
  "do_center_crop": true,
  "do_rescale": true,
  "rescale_factor": 0.00392156862745098,
  "do_normalize": true,
  "image_mean": [
    0.485,
    0.456,
    0.406
  ],
  "image_processor_type": "BitImageProcessor",
  "image_std": [
    0.229,
    0.224,
    0.225
  ],
  "resample": 3,
  "size": {
    "height": 518,
    "width": 518
  }
}"""


def model_ready(d):
    """config.json + 至少一份权重；HuggingFace 常把权重拆成多个分片文件。"""
    if not os.path.isfile(os.path.join(d, "config.json")):
        return False
    return bool(glob.glob(os.path.join(d, "*.safetensors")) or
               glob.glob(os.path.join(d, "*.bin")) or
               glob.glob(os.path.join(d, "*.pth")))


def check_env():
    """结构性检查：文件在不在。很快，可随时调用。需要它的页面见 need。"""
    items = []

    items.append({"label": "ffmpeg", "path": FF, "need": ("tag", "cut"),
                  "ok": os.path.exists(FF),
                  "hint": "把整个 ffmpeg 文件夹放到 models\\ffmpeg\\，里面应有 bin\\ffmpeg.exe"})

    items.append({"label": "ffprobe", "path": FFP, "need": ("tag",),
                  "ok": os.path.isfile(FFP),
                  "hint": "和 ffmpeg.exe 放在同一个 bin 目录；缺了抽帧会拿到错误的时长"})

    d = os.path.join(BASE, "models", "MiniCPM-V-4-6")
    items.append({"label": "MiniCPM-V-4-6（打标模型）", "path": d, "need": ("tag",),
                  "ok": model_ready(d),
                  "hint": "整个模型文件夹放到 models\\MiniCPM-V-4-6\\，约 2.5G"})

    d2 = os.path.join(BASE, "models", "aslakey_shot_scale")
    items.append({"label": "aslakey_shot_scale（景别模型）", "path": d2, "need": ("tag",),
                  "ok": model_ready(d2),
                  "hint": "HuggingFace 上的 aslakey/shot-scale，放成 models\\aslakey_shot_scale\\"})

    p3 = os.path.join(d2, "preprocessor_config.json")
    items.append({"label": "景别模型的处理器配置", "path": p3, "need": ("tag",),
                  "ok": os.path.exists(p3), "fix": "preproc",
                  "hint": "原模型里没这个文件，缺了景别会静默失效。点右边「一键生成」即可"})

    for f, lab in (("shot_analyzer.py", "模块 shot_analyzer.py"),
                   ("shot_type_classifier.py", "模块 shot_type_classifier.py")):
        pp = os.path.join(BASE, "A_coreUtils", "aftertreatment", f)
        items.append({"label": lab, "path": pp, "need": ("tag",),
                      "ok": os.path.exists(pp),
                      "hint": "随程序自带；缺失说明只拷了主程序，把整个 A_coreUtils 文件夹一起拷过来"})

    return items


RUNTIME = {"items": None, "done": False, "running": False}


def _rt(label, ok, detail="", hint="", need=("tag",)):
    """运行时检查项。path 与 detail 同值，便于复用一个渲染分支。"""
    return {"label": label, "ok": bool(ok), "detail": detail, "path": detail,
            "hint": hint, "need": need, "runtime": True}


def check_runtime():
    """真正 import 一遍 + 查显卡。慢（5~10 秒），只能在后台线程里调用。

    结构性检查只能证明「文件在」，证明不了「能用」。这一层专抓：
      · 库在但 import 就崩（缺 DLL、版本冲突、装了一半）
      · torch 是 CPU 版，或驱动不可用
      · 显卡架构不被当前 torch 支持（新卡配旧 torch 的典型症状）
      · 显存不够跑打标模型（两个模型已改为分阶段加载）
    """
    import platform
    items = [_rt("Python 解释器", True,
                 "Python %s · %d 位" % (platform.python_version(),
                                        64 if sys.maxsize > 2 ** 32 else 32),
                 need=("tag", "cut"))]

    ok_torch = False
    try:
        import torch
        ok_torch = True
        items.append(_rt("torch", True, "版本 %s" % torch.__version__))

        if torch.cuda.is_available():
            items.append(_rt("torch 的 CUDA 支持", True,
                             "CUDA %s · 可用" % (getattr(torch.version, "cuda", None) or "?")))
            try:
                prop = torch.cuda.get_device_properties(0)
                vram = prop.total_memory / 1024.0 ** 3
                arch = "sm_%d%d" % (prop.major, prop.minor)
                try:
                    archs = torch.cuda.get_arch_list()
                except Exception:
                    archs = []
                if archs and arch not in archs:
                    items.append(_rt(
                        "显卡", False, "%s · %.1fG 显存 · %s" % (prop.name, vram, arch),
                        "这张卡是 %s，但当前 torch 没编进它的内核（支持 %s）。"
                        "换支持该架构的 torch（cu128 及以上）" % (arch, ", ".join(archs[:5]))))
                elif vram < 5.5:
                    items.append(_rt(
                        "显卡", False, "%s · %.1fG 显存 · %s" % (prop.name, vram, arch),
                        "显存不足 6G，连单个打标模型都吃紧（已分阶段加载，仍可能不够）"))
                else:
                    tip = "" if vram >= 7 else "显存偏紧；若报内存不足，把「每段抽帧」改成 1"
                    items.append(_rt("显卡", True,
                                     "%s · %.1fG 显存 · %s" % (prop.name, vram, arch), tip))
            except Exception as e:
                items.append(_rt("显卡", False, "读取显卡信息失败：%s" % e,
                                 "驱动或 torch 安装有问题"))
        else:
            cpu_only = not getattr(torch.version, "cuda", None)
            items.append(_rt(
                "torch 的 CUDA 支持", False,
                "装的是 CPU 版 torch（没有 CUDA 组件）" if cpu_only else "torch 带 CUDA，但显卡不可用",
                "打标必须用显卡。装的是 CPU 版就换成 cu128 版；否则先查 NVIDIA 驱动"))
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else "")
        items.append(_rt("torch", False, msg,
                         "import 阶段就失败，多半是 DLL 缺失或装了一半。"
                         "优先整份换回随包的 python 目录，别在这个环境里 pip 修"))

    try:
        import transformers
        v = transformers.__version__
        parts = v.split(".")
        ver = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        items.append(_rt("transformers", ver >= (4, 45), "版本 %s" % v,
                         "" if ver >= (4, 45) else "版本过低，需要 4.45 以上，模型加载会失败"))
    except Exception as e:
        items.append(_rt("transformers", False,
                         "%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else ""),
                         "与 torch 版本不匹配时最常见。建议整份使用随包的 python 目录"))

    for mod, lab in (("cv2", "opencv-python"), ("PIL", "Pillow"), ("numpy", "numpy")):
        try:
            m = __import__(mod)
            items.append(_rt(lab, True, "版本 %s" % getattr(m, "__version__", "?")))
        except Exception as e:
            items.append(_rt(lab, False,
                             "%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else ""),
                             "抽帧或读图要用它；缺了说明 python 目录不完整"))

    if ok_torch:
        for mod, lab in (("A_coreUtils.aftertreatment.shot_analyzer", "模块 shot_analyzer"),
                         ("A_coreUtils.aftertreatment.shot_type_classifier", "模块 shot_type_classifier")):
            try:
                __import__(mod)
                items.append(_rt(lab, True, "已导入"))
            except Exception as e:
                last = str(e).splitlines()[-1] if str(e) else type(e).__name__
                items.append(_rt(lab, False, "%s: %s" % (type(e).__name__, last),
                                 "文件在但导不进来，看上面 torch / transformers 那几项"))
    return items


def apply_env_fix(key):
    """执行一键修复，返回 (成功, 说明)"""
    if key == "preproc":
        d = os.path.join(BASE, "models", "aslakey_shot_scale")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "preprocessor_config.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(SHOT_PREPROC)
        return True, "已生成 %s" % p
    return False, "没有对应的修复动作"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.q2 = queue.Queue()
        self.q3 = queue.Queue()
        self.cut_stop = threading.Event()
        self.cutting = False
        self.worker = None
        self.cut_worker = None
        self.imp_worker = None
        self._closing = False
        self.stop_event = threading.Event()
        self.running = False
        self.importing = False
        self.last_out = ""

        root.title("TagMaster · 打标高手")
        root.configure(bg=BG)
        try:
            sh = root.winfo_screenheight()
        except Exception:
            sh = 1080
        hh = max(760, min(920, int(sh * 0.84)))
        root.geometry("980x%d" % hh)
        root.minsize(880, 700)

        self._build()
        self._load_settings()
        self._show_page("tag")
        self.root.after(100, self._pump)
        self.root.after(600, self._startup_check)

        # 把文件/文件夹拖到 TagMaster.bat 图标上启动时，路径会作为命令行参数进来
        dropped = [a for a in sys.argv[1:] if os.path.exists(a)]
        if dropped:
            self.root.after(400, lambda: self._on_drop(dropped))

        def _init_dnd():
            if self._closing:
                return
            self.root.update_idletasks()
            if enable_native_dnd(self.root, self._on_drop):
                self._log("拖放已就绪：" + DND_STATUS, "dim")
            else:
                self._log("拖放未启用（%s）；仍可用「浏览」，或把文件拖到 TagMaster.bat 启动"
                          % DND_STATUS, "err")

        self.root.after(600, _init_dnd)

    # ---------- 记住上次选项 ----------
    def _load_settings(self):
        """启动时恢复上次的路径与勾选项；文件缺失或损坏就当没有，不影响使用。"""
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return
        if not isinstance(d, dict):
            return
        for k, var in (("src", self.var_src), ("out", self.var_out),
                       ("xml", self.var_xml), ("cutout", self.var_cutout),
                       ("cutsrc", self.var_cutsrc), ("oldpre", self.var_oldpre),
                       ("newpre", self.var_newpre), ("libdir", self.var_libdir)):
            if isinstance(d.get(k), str):
                var.set(d[k])
        for k, var in (("gallery", self.var_gallery), ("resume", self.var_resume),
                       ("libmode", self.var_libmode), ("audio", self.var_audio),
                       ("tagonly", self.var_tagonly)):
            if isinstance(d.get(k), bool):
                var.set(d[k])
        try:
            n = int(d.get("frames") or 0)
            if 1 <= n <= 4:
                self.var_frames.set(n)
        except Exception:
            pass
        # 恢复成「只补标签」时，对应的控件要跟着置灰
        try:
            self._sync_tagonly()
        except Exception:
            pass

    def _save_settings(self):
        """把当前路径与勾选项写进 设置.json，下次启动自动恢复。"""
        d = {"src": self.var_src.get(), "out": self.var_out.get(),
             "xml": self.var_xml.get(), "cutout": self.var_cutout.get(),
             "cutsrc": self.var_cutsrc.get(), "oldpre": self.var_oldpre.get(),
             "newpre": self.var_newpre.get(), "libdir": self.var_libdir.get(),
             "frames": int(self.var_frames.get() or 2),
             "gallery": bool(self.var_gallery.get()),
             "resume": bool(self.var_resume.get()),
             "libmode": bool(self.var_libmode.get()),
             "audio": bool(self.var_audio.get()),
             "tagonly": bool(self.var_tagonly.get())}
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # --- 组件 ---
    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, bg=PANEL2, fg=TXT, insertbackground=TXT,
                        relief="flat", font=(FONT, 10), highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=ACCENT,
                        disabledbackground=PANEL2, disabledforeground="#6b7480")

    def _btn(self, parent, text, cmd, kind="ghost", width=10):
        if kind == "primary":
            bg, fg = ACCENT, "#1a1408"
            active = "#ffd877"
        else:
            bg, fg = PANEL2, TXT
            active = "#2a323d"
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=active,
                      activeforeground=fg, relief="flat", bd=0, font=(FONT, 10, "bold" if kind == "primary" else "normal"),
                      padx=14, pady=8, cursor="hand2", width=width)
        return b

    def _build(self):
        # 头部
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=26, pady=(18, 8))
        tk.Label(head, text="Tag", bg=BG, fg=ACCENT, font=(FONT, 19, "bold")).pack(side="left")
        tk.Label(head, text="Master", bg=BG, fg=TXT, font=(FONT, 19, "bold")).pack(side="left")
        tk.Label(head, text="打标高手", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="left", padx=(12, 8), pady=(8, 0))
        chips = tk.Frame(head, bg=BG)
        chips.pack(side="left", pady=(6, 0))
        for _name in KEYS:
            tk.Label(chips, text=_name, bg=PANEL2, fg="#9fb0c4", font=(FONT, 8),
                     padx=6, pady=2).pack(side="left", padx=(0, 4))
        self._btn(head, "环境检查", self.env_dialog, width=8).pack(side="right", pady=(2, 0))

        # 分页
        self.tabbar = tk.Frame(self.root, bg=BG)
        self.tabbar.pack(fill="x", padx=26, pady=(2, 0))
        self.tab_btns = {}
        for key, label in (("tag", "① 打标"), ("cut", "② 切片段"), ("lib", "③ 素材库")):
            b = tk.Label(self.tabbar, text=label, bg=BG, fg=DIM,
                         font=(FONT, 10, "bold"), padx=18, pady=7, cursor="hand2")
            b.bind("<Button-1>", lambda e, k=key: self._show_page(k))
            b.pack(side="left", padx=(0, 6))
            self.tab_btns[key] = b
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", padx=26, pady=(0, 6))

        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.page1 = tk.Frame(self.container, bg=BG)
        self.page2 = tk.Frame(self.container, bg=BG)
        self.page3 = tk.Frame(self.container, bg=BG)
        self._build_cut(self.page2)
        self._build_lib(self.page3)

        # 路径
        box = tk.Frame(self.page1, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x", pady=4)
        self.var_src = tk.StringVar()
        self.var_out = tk.StringVar()
        for i, (label, var, cb) in enumerate((
                ("素材目录", self.var_src, self._pick_src),
                ("输出目录", self.var_out, self._pick_out))):
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", padx=14, pady=(12 if i == 0 else 6, 6 if i == 1 else 0))
            tk.Label(row, text=label, bg=PANEL, fg=DIM, font=(FONT, 10), width=7, anchor="w").pack(side="left")
            en = self._entry(row, var)
            en.pack(side="left", fill="x", expand=True, ipady=5)
            bt = self._btn(row, "浏览", cb, width=6)
            bt.pack(side="left", padx=(8, 0))
            if i == 1:
                # 输出目录：勾了「只补标签」就用不上，需要能置灰
                self.ent_out, self.btn_out = en, bt
        # 素材库模式：片段直接放进输出目录，管理文件收进 _标签数据
        self.var_libmode = tk.BooleanVar(value=False)
        self.chk_libmode = tk.Checkbutton(
            box, text="直接存入素材库文件夹（不建 clips 子目录）",
            variable=self.var_libmode, bg=PANEL, fg=TXT, selectcolor=PANEL2,
            activebackground=PANEL, activeforeground=TXT, font=(FONT, 9),
            highlightthickness=0, bd=0, anchor="w",
            cursor="hand2", disabledforeground="#5b6472")
        self.chk_libmode.pack(fill="x", padx=12)
        self.lbl_libmode_tip = tk.Label(
            box, text="勾选后：新片段直接落在输出目录里；标签表、预览页、缓存收进 _标签数据",
            bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w",
            wraplength=860, disabledforeground="#4c5360")
        self.lbl_libmode_tip.pack(fill="x", padx=30, pady=(0, 8))

        # 选项第一行：抽帧数 + 主操作按钮（按钮靠右，单独一行不会被复选框挤掉）
        opt = tk.Frame(self.page1, bg=BG)
        opt.pack(fill="x", pady=(4, 0))
        tk.Label(opt, text="每段抽帧", bg=BG, fg=DIM, font=(FONT, 10)).pack(side="left")
        self.var_frames = tk.IntVar(value=2)
        sp = tk.Spinbox(opt, from_=1, to=4, width=3, textvariable=self.var_frames,
                        bg=PANEL2, fg=TXT, relief="flat", buttonbackground=PANEL2,
                        font=(FONT, 10), highlightthickness=1, highlightbackground=LINE)
        sp.pack(side="left", padx=(8, 18), ipady=3)

        self.btn_run = self._btn(opt, "开始打标", self._start, "primary", width=12)
        self.btn_run.pack(side="right")
        self.btn_stop = self._btn(opt, "停止", self._stop, width=7)
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_stop.configure(state="disabled")

        # 选项第二行：三个复选框
        opt2 = tk.Frame(self.page1, bg=BG)
        opt2.pack(fill="x", pady=(6, 0))
        self.var_gallery = tk.BooleanVar(value=True)
        self.var_resume = tk.BooleanVar(value=False)
        self.var_tagonly = tk.BooleanVar(value=False)
        for text, var in (("生成筛选预览页", self.var_gallery), ("续跑（跳过已打标）", self.var_resume),
                          ("只补标签（不改名、不复制）", self.var_tagonly)):
            tk.Checkbutton(opt2, text=text, variable=var, bg=BG, fg=TXT, selectcolor=PANEL2,
                           activebackground=BG, activeforeground=TXT, font=(FONT, 10),
                           highlightthickness=0, bd=0).pack(side="left", padx=(0, 18))
        # 勾上「只补标签」后，输出目录和素材库模式都用不上，置灰避免误以为还要填
        self.var_tagonly.trace_add("write", lambda *a: self._sync_tagonly())
        self._sync_tagonly()

        # 进度
        pbox = tk.Frame(self.page1, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        pbox.pack(fill="x", pady=(14, 0))
        inner = tk.Frame(pbox, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=12)
        self.lbl_stage = tk.Label(inner, text="待开始", bg=PANEL, fg=ACCENT, font=(FONT, 11, "bold"))
        self.lbl_stage.pack(anchor="w")
        self.lbl_detail = tk.Label(inner, text="选好素材目录后点「开始打标」", bg=PANEL, fg=DIM,
                                   font=(FONT, 9), anchor="w")
        self.lbl_detail.pack(fill="x", pady=(2, 8))
        self.pbar = ttk.Progressbar(inner, mode="determinate", maximum=100)
        self.pbar.pack(fill="x")
        self.lbl_stat = tk.Label(inner, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_stat.pack(fill="x", pady=(8, 0))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TProgressbar", troughcolor="#2b3441", background=ACCENT,
                        bordercolor="#2b3441", lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

        # 底部（先 pack，避免被日志挤掉）
        foot = tk.Frame(self.page1, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(10, 16))
        self.btn_open = self._btn(foot, "打开输出目录", self._open_out, width=12)
        self.btn_open.pack(side="left")
        self.btn_html = self._btn(foot, "打开预览页", self._open_html, width=11)
        self.btn_html.pack(side="left", padx=8)
        self.btn_csv = self._btn(foot, "打开标签表", self._open_csv, width=11)
        self.btn_csv.pack(side="left")
        tk.Label(foot, text="不修改原片，只复制改名", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="right")

        # 日志
        lbox = tk.Frame(self.page1, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        lbox.pack(fill="both", expand=True, pady=(10, 0))
        bar = tk.Frame(lbox, bg=PANEL)
        bar.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(bar, text="运行日志", bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left")
        self.log = tk.Text(lbox, bg="#0c0f13", fg="#c8d2df", insertbackground=TXT, relief="flat",
                           font=(FONT, 10), wrap="none", bd=0, padx=12, pady=8, height=6,
                           cursor="arrow", takefocus=0, insertwidth=0)
        self.log.pack(fill="both", expand=True, padx=14, pady=(6, 10))
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("dim", foreground=DIM)
        self.log.configure(state="disabled")
        sb = tk.Scrollbar(lbox, command=self.log.yview, bg=PANEL, troughcolor=PANEL2,
                          relief="flat", bd=0, width=10)
        self.log.configure(yscrollcommand=sb.set)

    # --- 交互 ---
    # ---------- 分页 ----------
    # ---------- ③ 素材库页 ----------
    def _build_lib(self, parent):
        box = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x", pady=4)
        self.var_libdir = tk.StringVar()
        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(row, text="库文件夹", bg=PANEL, fg=DIM, font=(FONT, 10), width=7,
                 anchor="w").pack(side="left")
        self._entry(row, self.var_libdir).pack(side="left", fill="x", expand=True, ipady=5)
        self._btn(row, "浏览", self._pick_libdir, width=6).pack(side="left", padx=(8, 0))
        tk.Label(box, text="选单部作品文件夹，或包含多部作品的总库目录；"
                           "只读文件名，不改名、不复制、不动原片",
                 bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w",
                 wraplength=860).pack(fill="x", padx=30, pady=(0, 10))

        opt = tk.Frame(parent, bg=BG)
        opt.pack(fill="x", pady=(2, 0))
        self.btn_import3 = self._btn(opt, "导入已有片段", self._run_import, "primary", width=13)
        self.btn_import3.pack(side="left")
        self.btn_importstop = self._btn(opt, "停止", self._stop, width=7)
        self.btn_importstop.pack(side="left", padx=(8, 0))
        self.btn_importstop.configure(state="disabled")
        tk.Label(opt, text="  每个片段约 0.15 秒，主要是生成缩略图", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="left")

        pbox = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        pbox.pack(fill="x", pady=(10, 0))
        inner = tk.Frame(pbox, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=10)
        self.lbl_stage3 = tk.Label(inner, text="待开始", bg=PANEL, fg=ACCENT,
                                   font=(FONT, 11, "bold"))
        self.lbl_stage3.pack(anchor="w")
        self.lbl_detail3 = tk.Label(inner, text="选好库文件夹后点「导入已有片段」",
                                    bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_detail3.pack(fill="x", pady=(2, 6))
        self.pbar3 = ttk.Progressbar(inner, mode="determinate", maximum=100)
        self.pbar3.pack(fill="x")
        self.lbl_stat3 = tk.Label(inner, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_stat3.pack(fill="x", pady=(6, 0))

        foot = tk.Frame(parent, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(8, 12))
        self._btn(foot, "打开文件夹", self._open_libdir, width=11).pack(side="left")
        self._btn(foot, "打开预览页", self._open_html_lib, width=11).pack(side="left", padx=8)
        self._btn(foot, "打开标签表", self._open_csv_lib, width=11).pack(side="left")
        self.btn_sendpr3 = self._btn(foot, "发送到 Premiere", self._send_lib_to_pr, width=14)
        self.btn_sendpr3.pack(side="left", padx=8)
        self._btn(foot, "生成全库预览", self._build_lib_index, "primary", width=14).pack(side="right")

        lbox = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        lbox.pack(fill="both", expand=True, pady=(12, 0))
        bar = tk.Frame(lbox, bg=PANEL)
        bar.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(bar, text="运行日志", bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left")
        self.log3 = tk.Text(lbox, bg="#0c0f13", fg="#c8d2df", insertbackground=TXT, relief="flat",
                            font=(FONT, 10), wrap="none", bd=0, padx=12, pady=8, height=6,
                            cursor="arrow", takefocus=0, insertwidth=0)
        self.log3.pack(fill="both", expand=True, padx=14, pady=(6, 10))
        for tag, color in (("ok", OK), ("err", ERR), ("dim", DIM)):
            self.log3.tag_configure(tag, foreground=color)
        self.log3.configure(state="disabled")

    def _log3(self, text, tag=None):
        self.log3.configure(state="normal")
        self.log3.insert("end", time.strftime("%H:%M ") + text + "\n", tag or ())
        self.log3.see("end")
        self.log3.configure(state="disabled")

    def _pick_libdir(self):
        d = filedialog.askdirectory(title="选择素材库文件夹")
        if d:
            d = os.path.normpath(d)
            if os.path.basename(d) == "_标签数据":
                d = os.path.dirname(d)
            self.var_libdir.set(d)
            try:
                n = sum(1 for f in os.listdir(d)
                        if f.lower().endswith((".mp4", ".mov", ".mkv", ".avi")))
                subs = sum(1 for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
                self.lbl_detail3.configure(
                    text=("单部作品：%d 个视频" % n) if n else ("总库：%d 个子文件夹" % subs))
            except OSError:
                pass
            self._log3("库文件夹：%s" % d, "dim")
            self._save_settings()

    def _open_libdir(self):
        d = self.var_libdir.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "还没有选择库文件夹")

    def _send_to_pr(self, src_dir, bin_name, log):
        """把一个文件夹里的视频整批写进 PR 桥命令，返回是否发送成功。"""
        src_dir = os.path.normpath(src_dir)
        if not os.path.isdir(src_dir):
            messagebox.showinfo("提示", "文件夹不存在：%s" % src_dir)
            return False
        paths = [os.path.join(src_dir, f) for f in sorted(os.listdir(src_dir))
                 if f.lower().endswith(VIDEO_EXT)]
        if not paths:
            messagebox.showinfo("提示", "这个文件夹里没有视频")
            return False
        if len(paths) > 60 and not messagebox.askyesno(
                "发送到 Premiere",
                "将发送 %d 个片段，PR 导入会卡一会儿。\n继续吗？" % len(paths)):
            return False
        n, cmd_path = write_pr_command(paths, bin_name)
        if not n:
            messagebox.showinfo("提示", "没有可发送的片段")
            return False
        log("已发送 %d 个片段到 Premiere 队列（bin：%s）" % (n, bin_name or "片段"), "ok")
        log("PR 的「TagMaster 素材桥」面板约 1 秒内接收；没装面板见使用说明「安装 PR 面板」", "dim")
        return True

    def _send_lib_to_pr(self):
        d = self.var_libdir.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "请先在上方选好库文件夹")
            return
        self._send_to_pr(d, os.path.basename(os.path.normpath(d)),
                         lambda t, tag=None: self._log3(t, tag))

    def _open_html_lib(self):
        d = self.var_libdir.get().strip()
        p = self._find_out_file(d, "preview.html") if d else ""
        if p:
            webbrowser.open("file:///" + p.replace("\\", "/"))
        else:
            messagebox.showinfo("提示", "这个文件夹还没有预览页\n先「导入已有片段」，或对它打标")

    def _open_csv_lib(self):
        d = self.var_libdir.get().strip()
        p = self._find_out_file(d, "tags.csv") if d else ""
        if p:
            os.startfile(p)
        else:
            messagebox.showinfo("提示", "这个文件夹还没有标签表")

    def _build_lib_index(self):
        """把总库下各作品合并成一张总预览页：跨作品搜索、按作品/标签筛选。"""
        d = self.var_libdir.get().strip()
        if d and os.path.basename(d.rstrip("\\/")) == "_标签数据":
            d = os.path.dirname(d.rstrip("\\/"))
            self.var_libdir.set(d)
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "请先选好总库文件夹（包含多个作品子文件夹的那一层）")
            return
        try:
            p = build_library_index(
                d,
                log=self._log3,
                prog=lambda v: self.pbar3.configure(value=v),
                stage=lambda t: self.lbl_stage3.configure(text=t),
                detail=lambda t: self.lbl_detail3.configure(text=t),
                stop_event=self.stop_event)
        except Exception as e:
            self._log3("%s" % e, "err")
            messagebox.showerror("生成失败", str(e))
            return
        webbrowser.open("file:///" + p.replace("\\", "/"))

    def _show_page(self, key):
        self._page = key
        for k, b in self.tab_btns.items():
            b.configure(bg="#2b3441" if k == key else BG, fg=ACCENT if k == key else DIM)
        for p in (self.page1, self.page2, self.page3):
            p.pack_forget()
        {"tag": self.page1, "cut": self.page2, "lib": self.page3}.get(
            key, self.page1).pack(fill="both", expand=True, padx=26)

    # ---------- 拖入自动识别 ----------
    @staticmethod
    def _looks_like_output(p):
        """输出目录特征：有本程序生成的标签表/预览页，或是带 _标签数据 的素材库文件夹。"""
        return (os.path.isfile(os.path.join(p, "tags.csv")) or
                os.path.isfile(os.path.join(p, "preview.html")) or
                os.path.isdir(os.path.join(p, "_标签数据")))

    def _on_drop(self, paths):
        """按拖入内容 + 当前页，把路径填到对应输入框。"""
        notes = []
        for raw in paths:
            p = os.path.normpath(str(raw).strip().strip('"'))
            if not p or not os.path.exists(p):
                continue
            low = p.lower()

            if os.path.isfile(p) and low.endswith(".json") and (
                    "tm_cmd" in os.path.basename(low) or os.path.basename(low) == "pr_cmd.json"):
                # 预览页导出的发送文件：转写进 %APPDATA%，交给 PR 面板
                n, info = relay_pr_command_file(p)
                if n:
                    notes.append("已转发 %d 个片段到 Premiere 队列" % n)
                else:
                    notes.append("发送文件无效：%s" % info)
                continue

            if os.path.isfile(p) and low.endswith(".xml"):
                self._show_page("cut")  # 拖 XML 显然是要切片段
                self.var_xml.set(p)
                if not self.var_cutout.get().strip():
                    self.var_cutout.set(os.path.splitext(p)[0] + "_clips")
                notes.append("XML → %s" % p)
                continue

            if os.path.isfile(p):
                if not low.endswith(VIDEO_EXT):
                    continue
                p = os.path.dirname(p) or p

            if not os.path.isdir(p):
                continue
            if self._page == "lib":
                self.var_libdir.set(p)
                notes.append("库文件夹 → %s" % p)
                continue
            if self._looks_like_output(p):
                if self._page == "tag":
                    self.var_out.set(p)
                    self.var_libmode.set(self._looks_like_library(p))
                    notes.append("输出目录 → %s" % p)
                else:
                    self.var_cutout.set(p)
                    notes.append("切片输出目录 → %s" % p)
            elif self._page == "tag":
                self.var_src.set(p)
                if not self.var_out.get().strip():
                    self.var_out.set(p.rstrip("\\/") + "_打标")
                notes.append("素材目录 → %s" % p)
            else:
                self.var_cutsrc.set(p)
                notes.append("源视频目录 → %s" % p)

        msg = ("拖入识别：" + "；".join(notes)) if notes else \
              "拖入的不是文件夹 / 视频 / PR XML，没有改动路径"
        self._log(msg, "ok" if notes else "dim")
        self._log2(msg, "ok" if notes else "dim")
        self._log3(msg, "ok" if notes else "dim")

    # ---------- 切片段页 ----------
    def _build_cut(self, parent):
        box = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x", pady=4)
        self.var_xml = tk.StringVar()
        self.var_cutout = tk.StringVar()
        self.var_cutsrc = tk.StringVar()
        self.var_oldpre = tk.StringVar()
        self.var_newpre = tk.StringVar()

        for i, (label, var, cb) in enumerate((
                ("XML 文件", self.var_xml, self._pick_xml),
                ("输出目录", self.var_cutout, self._pick_cutout),
                ("源视频目录", self.var_cutsrc, self._pick_cutsrc))):
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", padx=14, pady=(10 if i == 0 else 5, 0))
            tk.Label(row, text=label, bg=PANEL, fg=DIM, font=(FONT, 10), width=9,
                     anchor="w").pack(side="left")
            self._entry(row, var).pack(side="left", fill="x", expand=True, ipady=5)
            self._btn(row, "浏览", cb, width=6).pack(side="left", padx=(8, 0))

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(5, 10))
        tk.Label(row, text="路径替换", bg=PANEL, fg=DIM, font=(FONT, 10), width=9,
                 anchor="w").pack(side="left")
        tk.Label(row, text="旧", bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        self._entry(row, self.var_oldpre).pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(row, text="→ 新", bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left", padx=6)
        self._entry(row, self.var_newpre).pack(side="left", fill="x", expand=True, ipady=4)

        opt = tk.Frame(parent, bg=BG)
        opt.pack(fill="x", pady=(2, 0))
        self.var_audio = tk.BooleanVar(value=False)
        tk.Checkbutton(opt, text="保留音频", variable=self.var_audio, bg=BG, fg=TXT,
                       selectcolor="#2b3441", activebackground=BG, activeforeground=TXT,
                       font=(FONT, 10), highlightthickness=0, bd=0).pack(side="left")
        tk.Label(opt, text="  NVENC 硬件编码，失败自动回退 CPU", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="left")
        self.btn_cut = self._btn(opt, "开始切片段", self._start_cut, "primary", width=11)
        self.btn_cut.pack(side="right")
        self.btn_cutstop = self._btn(opt, "停止", self._stop_cut, width=7)
        self.btn_cutstop.pack(side="right", padx=(0, 8))
        self.btn_cutstop.configure(state="disabled")

        pbox = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        pbox.pack(fill="x", pady=(10, 0))
        inner = tk.Frame(pbox, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=10)
        self.lbl_stage2 = tk.Label(inner, text="待开始", bg=PANEL, fg=ACCENT,
                                   font=(FONT, 11, "bold"))
        self.lbl_stage2.pack(anchor="w")
        self.lbl_detail2 = tk.Label(inner, text="选 PR 导出的 XML（文件 → 导出 → Final Cut Pro XML）",
                                    bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_detail2.pack(fill="x", pady=(2, 6))
        self.pbar2 = ttk.Progressbar(inner, mode="determinate", maximum=100)
        self.pbar2.pack(fill="x")
        self.lbl_stat2 = tk.Label(inner, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w")
        self.lbl_stat2.pack(fill="x", pady=(6, 0))

        foot = tk.Frame(parent, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(8, 12))
        self._btn(foot, "打开输出目录", self._open_cutout, width=12).pack(side="left")
        self._btn(foot, "切完去打标 →", self._goto_tag, width=13).pack(side="left", padx=8)

        lbox = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        lbox.pack(fill="both", expand=True, pady=(12, 0))
        bar = tk.Frame(lbox, bg=PANEL)
        bar.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(bar, text="运行日志", bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left")
        self.log2 = tk.Text(lbox, bg="#0c0f13", fg="#c8d2df", insertbackground=TXT, relief="flat",
                            font=(FONT, 10), wrap="none", bd=0, padx=12, pady=8, height=6,
                            cursor="arrow", takefocus=0, insertwidth=0)
        self.log2.pack(fill="both", expand=True, padx=14, pady=(6, 10))
        self.log2.tag_configure("ok", foreground=OK)
        self.log2.tag_configure("err", foreground=ERR)
        self.log2.tag_configure("dim", foreground=DIM)
        self.log2.configure(state="disabled")

    def _log2(self, text, tag=None):
        self.log2.configure(state="normal")
        self.log2.insert("end", time.strftime("%H:%M ") + str(text) + "\n", tag or ())
        self.log2.see("end")
        self.log2.configure(state="disabled")

    def _pick_xml(self):
        p = filedialog.askopenfilename(
            title="选择 PR 导出的 XML",
            filetypes=[("FCP XML", "*.xml"), ("所有文件", "*.*")])
        if not p:
            return
        p = os.path.normpath(p)
        self.var_xml.set(p)
        if not self.var_cutout.get():
            self.var_cutout.set(os.path.splitext(p)[0] + "_clips")
        try:
            seq, fps, items = parse_fcp_xml(p)
            self._log2("读到序列「%s」 %d 段  %.3f fps" % (seq, len(items), fps), "ok")
            if items and not self.var_cutsrc.get():
                d = os.path.dirname(items[0]["path"])
                if d and os.path.isdir(d):
                    self.var_cutsrc.set(d)
                else:
                    self._log2("源文件已不在 XML 记录的路径上，请填「源视频目录」或「路径替换」", "err")
        except Exception as e:
            self._log2(str(e), "err")

    def _pick_cutout(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_cutout.set(os.path.normpath(d))

    def _pick_cutsrc(self):
        d = filedialog.askdirectory(title="选择原片所在目录")
        if d:
            self.var_cutsrc.set(os.path.normpath(d))

    def _start_cut(self):
        if self.cutting or self.importing:
            return
        if not self._env_ready("cut"):
            return
        xml = self.var_xml.get().strip()
        if not xml or not os.path.isfile(xml):
            messagebox.showwarning("提示", "请先选择 PR 导出的 XML 文件")
            return
        out = self.var_cutout.get().strip() or os.path.splitext(xml)[0] + "_clips"
        self.var_cutout.set(out)
        self.cutting = True
        self.cut_stop.clear()
        self.btn_cut.configure(state="disabled", text="运行中…")
        self.btn_cutstop.configure(state="normal")
        self.pbar2["value"] = 0
        args = (xml, out, self.var_cutsrc.get().strip(), self.var_audio.get())
        # Tk 变量只能在主线程读，先复制成普通字符串再交给 worker
        old_pre = self.var_oldpre.get().strip()
        new_pre = self.var_newpre.get().strip()
        self._save_settings()

        def target():
            try:
                res = run_cut(*args,
                              log=lambda t, tag=None: self.q2.put(("log", t, tag)),
                              prog=lambda v: self.q2.put(("prog", v)),
                              stage=lambda t: self.q2.put(("stage", t)),
                              detail=lambda t: self.q2.put(("detail", t)),
                              counts=lambda a, b, c: self.q2.put(("count", a, b, c)),
                              stop_event=self.cut_stop,
                              old_pre=old_pre,
                              new_pre=new_pre)
                self.q2.put(("done", res))
            except Cancelled:
                self.q2.put(("cancelled", None))
            except Exception:
                self.q2.put(("error", traceback.format_exc()))

        self.cut_worker = threading.Thread(target=target, daemon=True)
        self.cut_worker.start()

    def _stop_cut(self):
        if self.cutting:
            self.cut_stop.set()
            self._log2("正在停止…", "dim")
            self.btn_cutstop.configure(state="disabled")

    def _finish_cut(self, res):
        self.cutting = False
        self.btn_cut.configure(state="normal", text="开始切片段")
        self.btn_cutstop.configure(state="disabled")
        self.pbar2["value"] = 100
        self.lbl_stage2.configure(text="完成")
        self.lbl_detail2.configure(text="%d 段，用时 %d 分 %d 秒" % (
            res["total"], res["sec"] // 60, res["sec"] % 60))
        self.lbl_stat2.configure(text="成功 %d · 失败 %d · 跳过 %d" % (
            res["ok"], res["fail"], res["skip"]))
        self._log2("完成：%d 段 → %s" % (res["ok"], res["out"]), "ok")

    def _open_cutout(self):
        d = self.var_cutout.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "还没有输出目录")

    def _goto_tag(self):
        d = self.var_cutout.get().strip()
        self._show_page("tag")
        if d and os.path.isdir(d):
            self.var_src.set(d)
            self.var_out.set(d.rstrip("\\/") + "_打标")

    # ---------- 环境自检 ----------
    def _all_items(self, page=None):
        items = list(check_env())
        rt = RUNTIME.get("items")
        if rt:
            items = items + list(rt)
        if page is None:
            return items
        return [it for it in items if page in it["need"]]

    def _env_missing(self, page):
        return [it for it in self._all_items(page) if not it["ok"]]

    def _env_ready(self, page):
        miss = self._env_missing(page)
        if not miss:
            return True
        self.env_dialog(page)
        return False

    def _probe_runtime(self, on_done=None):
        """在后台线程里做 import 与显卡检测，完成后回调（主线程）。"""
        if RUNTIME.get("running"):
            return
        RUNTIME["running"] = True

        def work():
            try:
                RUNTIME["items"] = check_runtime()
            except Exception:
                RUNTIME["items"] = [_rt("运行时检测", False,
                                        traceback.format_exc(limit=1).strip().splitlines()[-1],
                                        "检测过程本身出错，可把诊断报告发出来")]
            RUNTIME["running"] = False
            RUNTIME["done"] = True
            if on_done:
                # 走队列回主线程，避免在后台线程里直接操作 Tk
                self.q.put(("env_done", on_done))

        threading.Thread(target=work, daemon=True).start()

    def _startup_check(self):
        if self._env_missing("tag") or self._env_missing("cut"):
            self.env_dialog(None)
            return

        def after():
            if [it for it in (RUNTIME.get("items") or []) if not it["ok"]]:
                self.env_dialog(None)

        self._probe_runtime(on_done=after)

    def _export_diag(self):
        import platform
        lines = ["TagMaster 环境诊断",
                 "时间: " + time.strftime("%Y-%m-%d %H:%M:%S"),
                 "程序目录: " + BASE,
                 "系统: %s %s" % (platform.system(), platform.release()),
                 "Python: " + sys.version.replace("\n", " "),
                 ""]
        for it in self._all_items(None):
            lines.append("[%s] %s" % ("OK" if it["ok"] else "缺失", it["label"]))
            d = it.get("detail") or it.get("path") or ""
            if d:
                lines.append("       " + str(d))
            if not it["ok"] and it.get("hint"):
                lines.append("       解决: " + str(it["hint"]))
        out = os.path.join(BASE, "诊断报告.txt")
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.startfile(out)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def env_dialog(self, page=None):
        top = tk.Toplevel(self.root)
        top.title("环境检查")
        top.configure(bg=BG)
        top.transient(self.root)
        try:
            top.grab_set()
        except Exception:
            pass

        body = tk.Frame(top, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(18, 0))

        title = tk.Label(body, text="", bg=BG, fg=TXT, font=(FONT, 13, "bold"), anchor="w")
        title.pack(fill="x")
        sub = tk.Label(body, text="路径都相对于程序目录；补齐后点「重新检测」",
                       bg=BG, fg=DIM, font=(FONT, 9), anchor="w")
        sub.pack(fill="x", pady=(2, 10))

        wrap = tk.Frame(body, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                           bg="#3d4a5c", activebackground="#4d5c70",
                           troughcolor="#202836", relief="flat", bd=0, width=12)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        listbox = tk.Frame(canvas, bg=BG)
        _win = canvas.create_window((0, 0), window=listbox, anchor="nw")
        listbox.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(_win, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        def rebuild():
            for w in listbox.winfo_children():
                w.destroy()
            canvas.yview_moveto(0)
            items = self._all_items(page)
            bad = [it for it in items if not it["ok"]]
            pending = not RUNTIME.get("done")
            if page is None:
                title.configure(text=("一切正常，%d 项全部就绪" % len(items)) if not bad
                               else ("有 %d 项缺失，补齐后才能正常使用" % len(bad)))
                title.configure(fg=OK if not bad else ERR)
            else:
                title.configure(text=("可以开始，%d 项就绪" % len(items)) if not bad
                               else ("还缺 %d 项，补上才能用这一页" % len(bad)))
                title.configure(fg=OK if not bad else ERR)
            if pending:
                tk.Label(listbox, text="正在检测 Python 库与显卡（首次约 5~10 秒）…",
                         bg=BG, fg=DIM, font=(FONT, 9), anchor="w").pack(
                    fill="x", padx=6, pady=(8, 2))
            for it in items:
                row = tk.Frame(listbox, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
                row.pack(fill="x", pady=3)
                mark = "✓" if it["ok"] else "✕"
                l1 = tk.Frame(row, bg=PANEL)
                l1.pack(fill="x", padx=12, pady=(9, 0))
                tk.Label(l1, text=mark, bg=PANEL, fg=OK if it["ok"] else ERR,
                         font=(FONT, 11, "bold"), width=2).pack(side="left")
                tk.Label(l1, text=it["label"], bg=PANEL, fg=TXT if it["ok"] else TXT,
                         font=(FONT, 10, "bold")).pack(side="left")
                if (not it["ok"]) and it.get("fix"):
                    tk.Button(l1, text="一键生成", command=lambda k=it["fix"]: do_fix(k),
                              bg=ACCENT, fg="#1a1408", relief="flat", bd=0,
                              font=(FONT, 9, "bold"), padx=10, pady=2,
                              cursor="hand2").pack(side="right")
                if not it["ok"]:
                    tk.Label(row, text=it["path"], bg=PANEL, fg="#7f8b9c", font=(FONT, 8),
                             anchor="w", wraplength=640, justify="left").pack(
                        fill="x", padx=(40, 12), pady=(3, 0))
                    tk.Label(row, text=it["hint"], bg=PANEL, fg=DIM, font=(FONT, 9),
                             anchor="w", wraplength=620, justify="left").pack(
                        fill="x", padx=(40, 12), pady=(2, 9))
                elif it.get("detail"):
                    tk.Label(row, text=it["detail"], bg=PANEL, fg="#6f7b8c", font=(FONT, 8),
                             anchor="w", wraplength=640, justify="left").pack(
                        fill="x", padx=(40, 12), pady=(2, 8))
                else:
                    tk.Frame(row, bg=PANEL, height=4).pack()

            if pending and not RUNTIME.get("running"):
                self._probe_runtime(on_done=rebuild)

        def do_fix(key):
            try:
                ok, msg = apply_env_fix(key)
            except Exception as e:
                ok, msg = False, str(e)
            messagebox.showinfo("修复" if ok else "修复失败", msg)
            rebuild()

        def recheck():
            # 真正重新检测：清掉上次结果，让 rebuild 里的 pending 分支再跑一次
            RUNTIME["done"] = False
            RUNTIME["items"] = None
            RUNTIME["running"] = False
            rebuild()

        rebuild()

        foot = tk.Frame(top, bg=BG)
        foot.pack(fill="x", padx=22, pady=14)
        tk.Label(foot, text="另需 NVIDIA 显卡，显存 8G 以上", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="left")
        self._btn(foot, "导出诊断报告", self._export_diag, width=13).pack(side="right", padx=(0, 8))
        self._btn(foot, "关闭", top.destroy, width=8).pack(side="right")
        self._btn(foot, "重新检测", recheck, "primary", width=10).pack(side="right", padx=(0, 8))

        w, h = 780, 620
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + 60
        top.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

    @staticmethod
    def _looks_like_library(p):
        """作品文件夹特征：里面已经有视频，或存在本程序的 _标签数据。用来自动勾选库模式。"""
        try:
            if os.path.isdir(os.path.join(p, "_标签数据")):
                return True
            return any(f.lower().endswith(VIDEO_EXT) for f in os.listdir(p))
        except OSError:
            return False

    def _pick_src(self):
        d = filedialog.askdirectory(title="选择素材目录")
        if d:
            d = os.path.normpath(d)
            self.var_src.set(d)
            if not self.var_out.get():
                self.var_out.set(d.rstrip("\\/") + "_打标")

    def _pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            d = os.path.normpath(d)
            # 选进 _标签数据 里时自动退回作品文件夹（那里是程序的管理目录，不是输出目录）
            if os.path.basename(d) == "_标签数据":
                d = os.path.dirname(d)
                self._log("输出目录不能是 _标签数据，已改为：%s" % d, "err")
            self.var_out.set(d)
            # 选到作品文件夹（已有视频或 _标签数据）自动切成并入素材库
            self.var_libmode.set(self._looks_like_library(d))

    def _sync_tagonly(self):
        """只补标签模式下，输出目录与素材库模式都用不上，置灰并保留原值。"""
        on = bool(self.var_tagonly.get())
        state = "disabled" if on else "normal"
        for w in (self.ent_out, self.btn_out, self.chk_libmode, self.lbl_libmode_tip):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        try:
            # 直接说明为什么是灰的，避免以为程序坏了
            self.lbl_libmode_tip.configure(
                text=("只补标签模式下不使用上面两项：视频原地不动，标签写回素材目录的 _标签数据"
                      if on else
                      "勾选后：新片段直接落在输出目录里；标签表、预览页、缓存收进 _标签数据"))
        except tk.TclError:
            pass

    def _log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M ") + text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        if self.running or self.cutting or self.importing:
            return
        release_gpu()  # 每次开始前先清掉上一轮残留的显存缓存
        if not self._env_ready("tag"):
            return
        src = self.var_src.get().strip()
        out = self.var_out.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showwarning("提示", "请先选择素材目录")
            return
        tag_only = self.var_tagonly.get()
        if tag_only:
            # 只补标签：原地写回素材目录，输出目录这一格不覆盖
            # （覆盖了以后取消勾选会留下素材目录当输出，容易把 clips 建进库里）
            out = src
            self._log("只补标签模式：标签写回 %s\\_标签数据，不改名、不复制，忽略输出目录"
                      % src.rstrip("\\/"), "dim")
        else:
            if out and os.path.basename(out.rstrip("\\/")) == "_标签数据":
                out = os.path.dirname(out.rstrip("\\/"))
                self.var_out.set(out)
                self._log("输出目录不能是 _标签数据，已自动改为：%s" % out, "err")
            if not out:
                out = src.rstrip("\\/") + "_打标"
                self.var_out.set(out)
            # 手填的路径也认出来，避免漏勾「并入素材库」
            if not self.var_libmode.get() and self._looks_like_library(out):
                self.var_libmode.set(True)
                self._log("输出目录看起来是素材库，已自动勾选「并入素材库」", "dim")
        self._save_settings()  # 万一中途崩溃，下次启动也能恢复这次的路径
        self.running = True
        self.stop_event.clear()
        self.btn_run.configure(state="disabled", text="运行中…")
        self.btn_stop.configure(state="normal")
        self.pbar["value"] = 0
        self.last_out = out

        args = (src, out, self.var_frames.get(), self.var_gallery.get(), self.var_resume.get(),
                self.var_libmode.get())

        def target():
            try:
                res = run_pipeline(
                    *args,
                    tag_only=tag_only,
                    log=lambda t, tag=None: self.q.put(("log", t, tag)),
                    prog=lambda v: self.q.put(("prog", v)),
                    stage=lambda t: self.q.put(("stage", t)),
                    detail=lambda t: self.q.put(("detail", t)),
                    counts=lambda a, b, c: self.q.put(("count", a, b, c)),
                    stop_event=self.stop_event)
                self.q.put(("done", res))
            except Cancelled:
                self.q.put(("cancelled", None))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.running or self.importing:
            self.stop_event.set()
            self._log("正在停止…当前片段处理完就退出", "dim")
            self.btn_stop.configure(state="disabled")
            self.btn_importstop.configure(state="disabled")

    def _run_import(self):
        """素材库页：把已有视频按文件名导入标签表（不改名、不复制、不动原片）。"""
        if self.running or self.cutting or self.importing:
            return
        out = self.var_libdir.get().strip()
        if out and os.path.basename(out.rstrip("\\/")) == "_标签数据":
            out = os.path.dirname(out.rstrip("\\/"))
            self.var_libdir.set(out)
        if not out or not os.path.isdir(out):
            messagebox.showwarning("提示", "请先在「库文件夹」里选好素材库文件夹")
            return
        if not os.path.exists(FF):
            messagebox.showwarning("提示", "缺少 ffmpeg，生成不了缩略图：\n%s" % FF)
            return
        self._save_settings()
        self.importing = True
        self.stop_event.clear()
        self.btn_import3.configure(state="disabled", text="导入中…")
        self.btn_importstop.configure(state="normal")
        self.pbar3["value"] = 0
        self.lbl_stage3.configure(text="准备中")
        self._log3("开始导入：只读文件名，不改名、不复制、不动原文件", "dim")

        def target():
            try:
                res = import_tree(
                    out,
                    log=lambda t, tag=None: self.q3.put(("log", t, tag)),
                    prog=lambda v: self.q3.put(("prog", v)),
                    stage=lambda t: self.q3.put(("stage", t)),
                    detail=lambda t: self.q3.put(("detail", t)),
                    stop_event=self.stop_event)
                self.q3.put(("imp_done", res))
            except Cancelled:
                self.q3.put(("imp_cancel", None))
            except Exception:
                self.q3.put(("imp_err", traceback.format_exc()))

        self.imp_worker = threading.Thread(target=target, daemon=True)
        self.imp_worker.start()

    def _pump_pick(self):
        """PR 面板「浏览」代理：面板写请求文件 → 这里弹 tkinter 目录框（置顶）→ 结果回传。"""
        req = os.path.join(PR_CMD_DIR, "tm_pick.json")
        res = os.path.join(PR_CMD_DIR, "tm_pick_result.json")
        if not os.path.isfile(req) or os.path.isfile(res):
            return
        d = ""
        try:
            with open(req, encoding="utf-8") as f:
                if json.load(f).get("req"):
                    d = filedialog.askdirectory(
                        title="TagMaster · 选择素材文件夹（结果回传给 PR 面板）",
                        initialdir=self.var_libdir.get() or self.var_src.get() or "") or ""
        except Exception:
            d = ""
        try:
            with open(res, "w", encoding="utf-8") as rf:
                json.dump({"dir": os.path.normpath(d) if d else ""}, rf, ensure_ascii=False)
        except Exception:
            pass
        try:
            os.remove(req)
        except OSError:
            pass

    def _pump(self):
        self._pump_pick()
        try:  # 消费拖放消息：窗口过程只入队，这里在主线程统一处理
            while True:
                paths = _DROP_QUEUE.get_nowait()
                try:
                    self._on_drop(paths)
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:  # 素材库页的队列
            while True:
                msg = self.q3.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log3(msg[1], msg[2])
                elif kind == "prog":
                    self.pbar3["value"] = msg[1]
                elif kind == "stage":
                    self.lbl_stage3.configure(text=msg[1])
                elif kind == "detail":
                    self.lbl_detail3.configure(text=msg[1])
                elif kind in ("imp_done", "imp_cancel", "imp_err"):
                    self.importing = False
                    self.btn_import3.configure(state="normal", text="导入已有片段")
                    self.btn_importstop.configure(state="disabled")
                    if kind == "imp_done":
                        n = (msg[1] or {}).get("imported") or 0
                        self._log3("导入完成：%d 个片段进了标签表" % n, "ok")
                        self.lbl_stage3.configure(text="完成")
                        self.lbl_stat3.configure(text="累计导入 %d 个" % n)
                    elif kind == "imp_cancel":
                        self._log3("已停止（已写入的部分保留）", "err")
                        self.lbl_stage3.configure(text="已停止")
                    else:
                        self._log3(msg[1], "err")
                        self.lbl_stage3.configure(text="出错")
                        messagebox.showerror("导入出错",
                                             msg[1].strip().splitlines()[-1][:300])
        except queue.Empty:
            pass
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1], msg[2])
                elif kind == "prog":
                    self.pbar["value"] = msg[1]
                elif kind == "stage":
                    self.lbl_stage.configure(text=msg[1])
                elif kind == "detail":
                    self.lbl_detail.configure(text=msg[1])
                elif kind == "count":
                    self.lbl_stat.configure(
                        text="成功 %d · 失败 %d · 共 %d" % (msg[1], msg[2], msg[3]))
                elif kind == "done":
                    release_gpu()
                    self._finish(msg[1])
                elif kind == "cancelled":
                    release_gpu()
                    self._reset("已停止")
                    self._log("已停止（已处理的片段结果不会保存）", "err")
                elif kind == "error":
                    release_gpu()
                    self._reset("出错")
                    self._log(msg[1], "err")
                    messagebox.showerror("出错了", msg[1].strip().splitlines()[-1][:300])
                elif kind == "env_done":
                    try:
                        msg[1]()
                    except Exception:
                        pass
        except queue.Empty:
            pass
        try:
            while True:
                msg = self.q2.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log2(msg[1], msg[2])
                elif kind == "prog":
                    self.pbar2["value"] = msg[1]
                elif kind == "stage":
                    self.lbl_stage2.configure(text=msg[1])
                elif kind == "detail":
                    self.lbl_detail2.configure(text=msg[1])
                elif kind == "count":
                    self.lbl_stat2.configure(text="成功 %d · 失败 %d · 跳过 %d" % (msg[1], msg[2], msg[3]))
                elif kind == "done":
                    self._finish_cut(msg[1])
                elif kind == "cancelled":
                    self.cutting = False
                    self.btn_cut.configure(state="normal", text="开始切片段")
                    self.btn_cutstop.configure(state="disabled")
                    self.lbl_stage2.configure(text="已停止")
                    self._log2("已停止，已切出的片段会保留", "err")
                elif kind == "error":
                    self.cutting = False
                    self.btn_cut.configure(state="normal", text="开始切片段")
                    self.btn_cutstop.configure(state="disabled")
                    self.lbl_stage2.configure(text="出错")
                    self._log2(msg[1], "err")
                    messagebox.showerror("出错了", msg[1].strip().splitlines()[-1][:300])
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _finish(self, res):
        self._reset("完成")
        self.pbar["value"] = 100
        self.lbl_detail.configure(
            text="%d 个片段，用时 %d 分 %d 秒" % (res["total"], res["sec"] // 60, res["sec"] % 60))
        self.lbl_stat.configure(text="成功 %d · 失败 %d" % (res["ok"], res["fail"]))
        self._log("完成：%d 个片段 -> %s" % (res["ok"], res["clips"]), "ok")

    def _on_close(self):
        """关窗：先发停止信号，等后台线程真正结束，最后才释放显存。"""
        if self._closing:
            return
        if self.running or self.cutting or self.importing:
            if not messagebox.askokcancel(
                    "退出", "还有任务在运行，退出可能留下未写完的文件。\n确定退出吗？"):
                return
        self._closing = True
        self._save_settings()  # 关窗前记住这次的路径与勾选项
        self.stop_event.set()
        self.cut_stop.set()
        self._wait_workers()

    def _wait_workers(self, tries=0):
        """用 after 轮询，不阻塞 GUI；最多等约 10 秒让当前推理收尾。"""
        alive = any(w is not None and w.is_alive()
                    for w in (self.worker, self.cut_worker, self.imp_worker))
        if alive and tries < 100:
            self.root.after(100, lambda: self._wait_workers(tries + 1))
            return
        release_gpu()
        self.root.destroy()

    def _reset(self, stage_text=""):
        self.running = False
        self.btn_run.configure(state="normal", text="开始打标")
        self.btn_stop.configure(state="disabled")
        if stage_text:
            self.lbl_stage.configure(text=stage_text)

    @staticmethod
    def _find_out_file(d, name):
        """管理文件定位：普通模式在输出目录根，并入素材库模式在 _标签数据 里。"""
        for cand in (os.path.join(d, name), os.path.join(d, "_标签数据", name)):
            if os.path.isfile(cand):
                return cand
        return ""

    def _open_out(self):
        d = self.last_out or self.var_out.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "还没有输出目录")

    def _open_html(self):
        d = self.last_out or self.var_out.get().strip()
        p = self._find_out_file(d, "preview.html") if d else ""
        if p:
            webbrowser.open("file:///" + p.replace("\\", "/"))
        else:
            messagebox.showinfo("提示", "还没有生成预览页")

    def _open_csv(self):
        d = self.last_out or self.var_out.get().strip()
        p = self._find_out_file(d, "tags.csv") if d else ""
        if p:
            os.startfile(p)
        else:
            messagebox.showinfo("提示", "还没有标签表")


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    # 预览页直发服务：只绑 127.0.0.1，端口被占也不影响其它功能（预览页会回退下载）
    app.pr_srv = start_pr_server()
    if app.pr_srv:
        app._log("发送服务已启动：预览页可一键发送到 Premiere（端口 %d）" % PR_HTTP_PORT, "dim")
    else:
        app._log("发送服务端口 %d 被占用：预览页发送将回退为下载文件" % PR_HTTP_PORT, "dim")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # pythonw 启动时看不到控制台，把错误落到文件里
        import traceback as _tb
        err = _tb.format_exc()
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "启动错误.log"), "w", encoding="utf-8") as f:
            f.write(err)
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("启动失败", err.strip().splitlines()[-1][:400])
            r.destroy()
        except Exception:
            pass
