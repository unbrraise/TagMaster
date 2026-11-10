# -*- coding: utf-8 -*-
"""TagMaster · 打标高手

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
import threading
import subprocess
import traceback
import collections
import webbrowser

BASE = r"D:\TagMaster"
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.chdir(BASE)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

FF = os.path.join(BASE, "models", "ffmpeg", "bin", "ffmpeg.exe")
FFP = os.path.join(os.path.dirname(FF), "ffprobe.exe")

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
SHOT_SET = {"大特写", "特写", "近景", "中景", "全景", "远景", "空镜"}
SHOT_MAP = {"小特写": "特写", "极大特写": "大特写", "中近景": "近景", "中全景": "全景",
            "大远景": "远景", "近特写": "特写", "脸部特写": "特写", "面部特写": "特写",
            "半身": "中景", "全身": "全景"}
NOISE = {"背景模糊", "模糊", "画面", "可见", "部分", "局部", "其他", "若干", "一些",
         "整体", "似乎", "可能", "看起来", "人物们", "东西", "物体", "人物", "人们"}
LIGHT_SET = {"白天", "夜晚", "黄昏", "阳光", "逆光", "昏暗"}
PEOPLE_SET = {"无人", "单人", "双人", "多人"}
FACE_SET = {"正面", "侧脸", "背影"}

PROMPT = """这是一段动画短片的 {n} 张连续画面。逐项如实标注，不确定就填"无"，不要编造。所有字段必须用中文。只输出一行 JSON，不要解释、不要重复。

"人数"：数画面里有几个人，填 无人/单人/双人/多人
"景别"：人数是"无人"时填"空镜"；有人时按人的大小判断——只有脸部局部填"大特写"，头肩填"特写"，上半身填"近景"，腰以上填"中景"，全身填"全景"，人在画面里很小填"远景"
"朝向"：主要人物的朝向，填 正面/侧脸/背影；人数是"无人"时填"无"
"主体"：主要人物或物体的特征，1-3个中文词空格分隔
"动作"：正在做的动作或姿态，1-2个中文词，没有动作填"无"
"场景"：环境地点，1-2个中文词（教室/走廊/街道/房间/天台/天空/水面/森林/废墟）
"光线"：填 白天/夜晚/黄昏/阳光/逆光/昏暗
"情绪"：从这里选一个 平静/温柔/孤独/悲伤/紧张/温暖/空灵/压抑/愤怒/惊讶/恐惧/欢快

JSON 示例：
{{"人数":"单人","景别":"近景","朝向":"侧脸","主体":"少女 金发","动作":"回头","场景":"街道","光线":"夜晚","情绪":"孤独"}}"""


# ---------------- 工具函数 ----------------
def dur_of(path):
    try:
        r = subprocess.run([FFP, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        return float((r.stdout or "0").strip() or 0) or 2.0
    except Exception:
        return 2.0


def grab_frames(path, frame_dir, n=2):
    """按时长的 1/3、2/3 抽帧，短片段从开头补"""
    stem = os.path.splitext(os.path.basename(path))[0]
    d = dur_of(path)
    out = []
    for i in range(n):
        t = d * (i + 1) / (n + 1)
        p = os.path.join(frame_dir, "%s__%d.jpg" % (stem, i))
        for off in (t, 0.0, 0.1, 0.2):
            subprocess.run([FF, "-y", "-v", "error", "-ss", "%.3f" % off, "-i", path,
                            "-vf", "scale=512:-1", "-vframes", "1", "-q:v", "3", p],
                           check=False, capture_output=True)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                break
        if os.path.exists(p) and os.path.getsize(p) > 0:
            out.append(p)
    return out


def clean_field(s):
    s = str(s or "").strip().strip('"')
    s = re.sub(r"[A-Za-z]+", "", s)
    s = re.sub(r"[/、,，;；]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" _-")
    return s


def merge_json_objects(text):
    merged, i = {}, 0
    while True:
        a = text.find("{", i)
        if a == -1:
            break
        b = text.find("}", a)
        if b == -1:
            break
        try:
            d = json.loads(text[a:b + 1])
            if isinstance(d, dict):
                merged.update(d)
        except json.JSONDecodeError:
            pass
        i = b + 1
    return merged


def parse_tags(text):
    s = (text or "").strip()
    a = s.find("{")
    if a == -1:
        return None
    b = s.rfind("}")
    d = {}
    if b > a:
        try:
            d = json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            d = {}
    if not isinstance(d, dict) or not d:
        d = merge_json_objects(s)
    if not d:
        return None
    out = {k: clean_field(d.get(k, "")) for k in KEYS}

    shot = SHOT_MAP.get(out["景别"], out["景别"])
    if shot not in SHOT_SET:
        shot = next((k for k in SHOT_SET if k and k in shot), "")
    out["景别"] = shot
    if out["人数"] not in PEOPLE_SET:
        out["人数"] = ""
    if out["人数"] == "无人":
        out["朝向"] = "无"
        if out["景别"]:
            out["景别"] = "空镜"
    if not out["景别"]:
        return None
    return out


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


def build_name(row, num):
    parts = [row.get(k, "") for k in FIELD_ORDER if row.get(k)]
    name = "_".join(parts) + "_" + num + ".mp4"
    return re.sub(r'[\\/:*?"<>|]', "", name)


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
.reset{margin-left:auto;background:none;border:1px solid var(--line);color:var(--dim);padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px}
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
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;transition:.15s}
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
.empty{color:var(--dim);padding:60px 0;text-align:center;font-size:13px}
</style></head><body>
<header>
  <h1>素材库 · 标签浏览 <span>点卡片直接播放原片</span></h1>
  <div class="toolbar">
    <input type="search" id="q" placeholder="搜索关键词：少女 / 森林 / 背影 …">
    <div class="count">命中 <b id="cnt">0</b> / <span id="tot">0</span></div>
    <button class="reset" id="reset">清空筛选</button>
  </div>
  <div class="facets" id="facets"></div>
</header>
<main><div class="grid" id="grid"></div></main>
<script>
const DATA = __DATA__;
const CLIPDIR = "__CLIPDIR__";
const KEYMAP = {景别:'s', 人数:'n', 朝向:'d', 光线:'l', 情绪:'m', 场景:'p'};
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
    el.innerHTML = v + '<span class="n">' + c + '</span>';
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
function match(it) {
  for (const [label, key] of Object.entries(KEYMAP)) {
    const s = state[label];
    if (s.size) { const toks = String(it[key] || '').split(' ');
      if (!toks.some(t => s.has(t))) return false; }
  }
  if (q) { const hay = [it.f, it.o, it.a, it.p, it.m, it.d, it.s, it.n].join(' ').toLowerCase();
    if (!hay.includes(q)) return false; }
  return true;
}
function render() {
  const list = DATA.items.filter(match); cnt.textContent = list.length;
  if (!list.length) { grid.innerHTML = '<div class="empty">没有符合条件的片段</div>'; return; }
  const frag = document.createDocumentFragment();
  list.forEach(it => {
    const card = document.createElement('div'); card.className = 'card'; card.title = it.f;
    card.onclick = () => window.open(CLIPDIR + encodeURIComponent(it.f));
    const badge = [it.s ? '<i class="s">' + it.s + '</i>' : '', it.n ? '<i>' + it.n + '</i>' : '',
                   it.d && it.d !== '无' ? '<i>' + it.d + '</i>' : ''].join('');
    const mood = it.m ? '<i class="m">' + it.m + '</i>' : '';
    card.innerHTML = '<div class="thumb"><img loading="lazy" src="thumbs/' + it.t + '" alt="">' +
      '<div class="badge">' + badge + '</div>' +
      (mood ? '<div class="badge" style="left:auto;right:7px">' + mood + '</div>' : '') + '</div>' +
      '<div class="meta"><div class="m1">' + (it.o || '——') + (it.a ? ' · ' + it.a : '') + '</div>' +
      '<div class="m2">' + [it.p, it.l].filter(Boolean).join(' / ') +
      ' <span style="opacity:.45">· ' + it.f.replace('.mp4', '').split('_').pop() + '</span></div></div>';
    frag.appendChild(card);
  });
  grid.innerHTML = ''; grid.appendChild(frag);
}
render();
</script></body></html>"""


class Cancelled(Exception):
    pass


# ---------------- 流水线 ----------------
def run_pipeline(src, out, nframes, make_gallery, resume, log, prog, stage, detail, counts, stop_event):
    os.makedirs(out, exist_ok=True)
    clips_dir = os.path.join(out, "clips")
    thumbs_dir = os.path.join(out, "thumbs")
    frame_dir = os.path.join(out, "_frames")
    for d in (clips_dir, thumbs_dir, frame_dir):
        os.makedirs(d, exist_ok=True)

    csv_path = os.path.join(out, "tags.csv")
    files = sorted([f for f in os.listdir(src) if f.lower().endswith((".mp4", ".mov", ".mkv", ".avi"))])
    if not files:
        raise RuntimeError("这个文件夹里没有视频文件")

    stage("加载模型")
    detail("正在加载 MiniCPM-V-4-6 …（首次约 10~20 秒）")
    log("素材目录：%s" % src)
    log("输出目录：%s" % out)
    log("共 %d 个片段，每段抽 %d 帧" % (len(files), nframes))

    from PIL import Image
    import torch
    from A_coreUtils.aftertreatment.shot_analyzer import ShotAnalyzer

    an = ShotAnalyzer()
    an.load_model()
    an.max_slice_nums = 1
    an.downsample_mode = "4x"
    log("模型就绪")

    # 续跑：读已有结果
    cached = {}
    if resume and os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("原文件") and r.get("景别"):
                        cached[r["原文件"]] = r
            log("读到上次结果 %d 条，将跳过这些片段的推理" % len(cached))
        except Exception:
            cached = {}

    rows = []
    ok = fail = 0
    t0 = time.time()
    for i, fn in enumerate(files):
        if stop_event.is_set():
            raise Cancelled()
        path = os.path.join(src, fn)
        num = os.path.splitext(fn)[0].split("_")[-1]
        prog(int(3 + 82 * i / len(files)))
        detail("%d / %d · %s" % (i + 1, len(files), fn))
        ts = time.time()

        if fn in cached:
            old = cached[fn]
            row = {k: old.get(k, "") for k in KEYS}
            row["原文件"] = fn
            raw = old.get("模型原文", "")
            cost = 0.0
        else:
            frames = grab_frames(path, frame_dir, nframes)
            if not frames:
                log("跳过（抽不到帧）：%s" % fn, ERR)
                fail += 1
                counts(ok, fail, len(files))
                continue
            imgs = [Image.open(p).convert("RGB") for p in frames]
            content = [{"type": "image", "image": im} for im in imgs]
            content.append({"type": "text", "text": PROMPT.replace("{n}", str(len(imgs)))})
            msgs = [{"role": "user", "content": content}]
            raw = ""
            parsed = None
            for _ in range(2):
                try:
                    inputs = an._processor.apply_chat_template(
                        msgs, tokenize=True, add_generation_prompt=True,
                        return_dict=True, return_tensors="pt").to(an.device)
                    with torch.no_grad():
                        o = an._model.generate(**inputs, max_new_tokens=160, do_sample=False,
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
                log("跳过（解析失败）：%s" % fn, ERR)
                fail += 1
                counts(ok, fail, len(files))
                continue
            row = dict(parsed)
            row["原文件"] = fn
            cost = round(time.time() - ts, 1)
            # 帧文件保留到景别复算之后统一清理

        row["编号"] = num
        rows.append({"row": row, "raw": (raw or "")[:200], "sec": cost})
        ok += 1
        counts(ok, fail, len(files))
        if fn not in cached:
            log("%s  %s" % (num, " · ".join(row.get(k, "") for k in KEYS if row.get(k))))

    if stop_event.is_set():
        raise Cancelled()
    log("推理完成：成功 %d，失败 %d，用时 %.0f 秒" % (ok, fail, time.time() - t0))

    # ---- 景别复算 ----
    stage("判景别")
    detail("DinoV2 对每段的两帧分类")
    prog(86)
    from A_coreUtils.aftertreatment.shot_type_classifier import DinoV2ShotClassifier
    clf = DinoV2ShotClassifier()
    clf.load()
    import cv2
    dino_ok = 0
    if not rows:
        log("没有可用结果，跳过景别复算", ERR)
    for i, item in enumerate(rows):
        if stop_event.is_set():
            raise Cancelled()
        row = item["row"]
        stem = os.path.splitext(row["原文件"])[0]
        labs = []
        for k in range(nframes):
            p = os.path.join(frame_dir, "%s__%d.jpg" % (stem, k))
            if not (os.path.exists(p) and os.path.getsize(p) > 0):
                for off in (0.5 + 0.8 * k, 0.0, 0.2):
                    subprocess.run([FF, "-y", "-v", "error", "-ss", "%.2f" % off,
                                    "-i", os.path.join(src, row["原文件"]), "-vf", "scale=512:-1",
                                    "-vframes", "1", "-q:v", "3", p], check=False, capture_output=True)
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        break
            if os.path.exists(p):
                # 用 PIL 读，cv2.imread 在中文路径下会失败
                try:
                    from PIL import Image as _PIL
                    import numpy as _np
                    fr = cv2.cvtColor(_np.array(_PIL.open(p).convert("RGB")), cv2.COLOR_RGB2BGR)
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

    # ---- 复制改名 ----
    stage("生成文件")
    detail("复制原片并按标签改名")
    prog(95)
    for f in os.listdir(clips_dir):
        if f.lower().endswith(".mp4"):
            try:
                os.remove(os.path.join(clips_dir, f))
            except OSError:
                pass

    done_files = []
    for item in rows:
        row = item["row"]
        src_path = os.path.join(src, row["原文件"])
        if not os.path.exists(src_path):
            row["新文件名"] = ""
            continue
        name = build_name(row, row["编号"])
        base, ext = os.path.splitext(name)
        n = 1
        while os.path.exists(os.path.join(clips_dir, name)):
            name = "%s_%d%s" % (base, n, ext)
            n += 1
        try:
            shutil.copy2(src_path, os.path.join(clips_dir, name))
            row["新文件名"] = name
            done_files.append(name)
        except Exception as e:
            row["新文件名"] = ""
            log("复制失败 %s：%s" % (row["原文件"], e), ERR)

    cols = ["原文件", "编号"] + KEYS + ["新文件名", "模型原文", "耗时秒"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for item in rows:
            d = dict(item["row"])
            d["模型原文"] = item["raw"]
            d["耗时秒"] = item["sec"]
            w.writerow({k: d.get(k, "") for k in cols})
    log("标签表：%s" % csv_path)
    log("改名副本：%d 个 → %s" % (len(done_files), clips_dir))

    # ---- 预览页 ----
    html_path = ""
    if make_gallery:
        stage("生成预览页")
        detail("抽缩略图并生成筛选页")
        prog(97)
        items, facets_src = [], {k: collections.Counter() for k in ("景别", "人数", "朝向", "光线", "情绪", "场景")}
        keymap = {"景别": "s", "人数": "n", "朝向": "d", "光线": "l", "情绪": "m", "场景": "p"}
        for i, item in enumerate(rows):
            if stop_event.is_set():
                raise Cancelled()
            row = item["row"]
            name = row.get("新文件名")
            if not name:
                continue
            th = "%s.jpg" % row["编号"]
            tp = os.path.join(thumbs_dir, th)
            if not os.path.exists(tp):
                for off in ("0.6", "0.3", "0.0"):
                    subprocess.run([FF, "-y", "-v", "error", "-ss", off,
                                    "-i", os.path.join(clips_dir, name),
                                    "-vf", "scale=320:-1", "-vframes", "1", "-q:v", "5", tp],
                                   check=False, capture_output=True)
                    if os.path.exists(tp):
                        break
            if not os.path.exists(tp):
                continue
            items.append({"f": name, "t": th,
                          "s": row.get("景别", ""), "n": row.get("人数", ""), "d": row.get("朝向", ""),
                          "o": row.get("主体", ""), "a": row.get("动作", ""),
                          "p": row.get("场景", ""), "l": row.get("光线", ""), "m": row.get("情绪", "")})
            for label, key in keymap.items():
                for tok in str(row.get(label, "")).split():
                    if tok and tok != "无":
                        facets_src[label][tok] += 1
            prog(int(97 + 2.5 * (i + 1) / len(rows)))

        facets = {label: [{"v": v, "c": n} for v, n in c.most_common(24)] for label, c in facets_src.items()}
        data = json.dumps({"items": items, "facets": facets}, ensure_ascii=False)
        html = GALLERY_HTML.replace("__DATA__", data).replace("__CLIPDIR__", "clips/")
        html_path = os.path.join(out, "preview.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        log("预览页：%s" % html_path)

    try:
        shutil.rmtree(frame_dir, ignore_errors=True)
    except Exception:
        pass

    prog(100)
    stage("完成")
    detail("全部完成")
    return {"csv": csv_path, "clips": clips_dir, "html": html_path,
            "ok": ok, "fail": fail, "total": len(files),
            "sec": round(time.time() - t0)}


# ---------------- 界面 ----------------
# ---------------- PR XML 切片段 ----------------
def url_to_path(u):
    from urllib.parse import unquote
    s = (u or "").strip()
    for pre in ("file://localhost/", "file:///", "file://"):
        if s.lower().startswith(pre):
            s = s[len(pre):]
            break
    s = unquote(s)
    if len(s) > 1 and s[1] == ":":
        return os.path.normpath(s)
    return os.path.normpath("/" + s)


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
    base = [ff, "-y", "-v", "error", "-ss", "%.4f" % ss, "-i", src, "-t", "%.4f" % dur]
    audio = ["-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"]
    v_nv = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    v_cpu = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    for v in ([v_nv, v_cpu] if try_nvenc else [v_cpu]):
        r = subprocess.run(base + v + audio + [dst], capture_output=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True
    return False


def remap_path(path, old_pre, new_pre):
    """把 XML 里的旧路径前缀替换成新路径前缀"""
    if not path or not old_pre or not new_pre:
        return path
    p = path.replace("/", "\\")
    o = old_pre.replace("/", "\\").rstrip("\\")
    if o and p.lower().startswith(o.lower()):
        return os.path.normpath(new_pre.rstrip("\\/") + p[len(o):])
    return path


def run_cut(xml_path, out_dir, src_dir, keep_audio, log, prog, stage, detail, counts, stop_event,
            old_pre="", new_pre=""):
    os.makedirs(out_dir, exist_ok=True)
    stage("解析 XML")
    detail("读取序列与剪辑点")
    log("XML：%s" % xml_path)
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
        if cut_one(FF, it["path"], ss, dur, dst, keep_audio):
            ok += 1
        else:
            fail += 1
            log("失败：第 %d 段（%s）" % (k + 1, os.path.basename(it["path"])), ERR)
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


def check_env():
    """返回环境检查项列表。need: 哪一页需要它（tag / cut）"""
    import importlib.util as iu
    items = []

    items.append({"label": "ffmpeg", "path": FF, "need": ("tag", "cut"),
                  "ok": os.path.exists(FF),
                  "hint": "把整个 ffmpeg 文件夹放到 models\\ffmpeg\\，里面应有 bin\\ffmpeg.exe"})

    d = os.path.join(BASE, "models", "MiniCPM-V-4-6")
    items.append({"label": "MiniCPM-V-4-6（打标模型）", "path": d, "need": ("tag",),
                  "ok": all(os.path.exists(os.path.join(d, f))
                            for f in ("config.json", "model.safetensors")),
                  "hint": "整个模型文件夹放到 models\\MiniCPM-V-4-6\\，约 2.5G"})

    d2 = os.path.join(BASE, "models", "aslakey_shot_scale")
    items.append({"label": "aslakey_shot_scale（景别模型）", "path": d2, "need": ("tag",),
                  "ok": all(os.path.exists(os.path.join(d2, f))
                            for f in ("config.json", "model.safetensors")),
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

    for mod, lab in (("torch", "Python 库 torch"), ("transformers", "Python 库 transformers"),
                     ("cv2", "Python 库 opencv-python"), ("PIL", "Python 库 Pillow")):
        items.append({"label": lab, "path": mod, "need": ("tag", "cut"),
                      "ok": iu.find_spec(mod) is not None,
                      "hint": "应由随包的 python\\ 环境自带；缺失说明 python 目录不完整"})
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
        self.cut_stop = threading.Event()
        self.cutting = False
        self.worker = None
        self.stop_event = threading.Event()
        self.running = False
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
        self._show_page("tag")
        self.root.after(100, self._pump)
        self.root.after(600, self._startup_check)

    # --- 组件 ---
    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, bg=PANEL2, fg=TXT, insertbackground=TXT,
                        relief="flat", font=(FONT, 10), highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=ACCENT)

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
        tk.Label(head, text="打标高手 · 景别 / 人数 / 朝向 / 主体 / 动作 / 场景 / 光线 / 情绪",
                 bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left", padx=(12, 0), pady=(8, 0))
        self._btn(head, "环境检查", self.env_dialog, width=8).pack(side="right", pady=(2, 0))

        # 分页
        self.tabbar = tk.Frame(self.root, bg=BG)
        self.tabbar.pack(fill="x", padx=26, pady=(2, 0))
        self.tab_btns = {}
        for key, label in (("tag", "① 打标"), ("cut", "② 切片段")):
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
        self._build_cut(self.page2)

        # 路径
        box = tk.Frame(self.page1, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x", pady=4)
        self.var_src = tk.StringVar()
        self.var_out = tk.StringVar()
        for i, (label, var, cb) in enumerate((
                ("素材目录", self.var_src, self._pick_src),
                ("输出目录", self.var_out, self._pick_out))):
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", padx=14, pady=(12 if i == 0 else 6, 12 if i == 1 else 0))
            tk.Label(row, text=label, bg=PANEL, fg=DIM, font=(FONT, 10), width=7, anchor="w").pack(side="left")
            self._entry(row, var).pack(side="left", fill="x", expand=True, ipady=5)
            self._btn(row, "浏览", cb, width=6).pack(side="left", padx=(8, 0))

        # 选项
        opt = tk.Frame(self.page1, bg=BG)
        opt.pack(fill="x", pady=(4, 0))
        tk.Label(opt, text="每段抽帧", bg=BG, fg=DIM, font=(FONT, 10)).pack(side="left")
        self.var_frames = tk.IntVar(value=2)
        sp = tk.Spinbox(opt, from_=1, to=4, width=3, textvariable=self.var_frames,
                        bg=PANEL2, fg=TXT, relief="flat", buttonbackground=PANEL2,
                        font=(FONT, 10), highlightthickness=1, highlightbackground=LINE)
        sp.pack(side="left", padx=(8, 18), ipady=3)
        self.var_gallery = tk.BooleanVar(value=True)
        self.var_resume = tk.BooleanVar(value=False)
        for text, var in (("生成筛选预览页", self.var_gallery), ("续跑（跳过已打标）", self.var_resume)):
            tk.Checkbutton(opt, text=text, variable=var, bg=BG, fg=TXT, selectcolor=PANEL2,
                           activebackground=BG, activeforeground=TXT, font=(FONT, 10),
                           highlightthickness=0, bd=0).pack(side="left", padx=(0, 16))

        self.btn_run = self._btn(opt, "开始打标", self._start, "primary", width=12)
        self.btn_run.pack(side="right")
        self.btn_stop = self._btn(opt, "停止", self._stop, width=7)
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_stop.configure(state="disabled")

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
        style.configure("TProgressbar", troughcolor=PANEL2, background=ACCENT,
                        bordercolor=PANEL2, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

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
    def _show_page(self, key):
        self._page = key
        for k, b in self.tab_btns.items():
            b.configure(bg="#2b3441" if k == key else BG, fg=ACCENT if k == key else DIM)
        self.page1.pack_forget()
        self.page2.pack_forget()
        (self.page1 if key == "tag" else self.page2).pack(fill="both", expand=True, padx=26)

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
        if self.cutting:
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
        args = (xml, out, self.var_cutsrc.get().strip(), self.var_audio.get(),
                self.var_oldpre.get().strip(), self.var_newpre.get().strip())

        def target():
            try:
                res = run_cut(*args,
                              log=lambda t, tag=None: self.q2.put(("log", t, tag)),
                              prog=lambda v: self.q2.put(("prog", v)),
                              stage=lambda t: self.q2.put(("stage", t)),
                              detail=lambda t: self.q2.put(("detail", t)),
                              counts=lambda a, b, c: self.q2.put(("count", a, b, c)),
                              stop_event=self.cut_stop)
                self.q2.put(("done", res))
            except Cancelled:
                self.q2.put(("cancelled", None))
            except Exception:
                self.q2.put(("error", traceback.format_exc()))

        threading.Thread(target=target, daemon=True).start()

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
    def _startup_check(self):
        if self._env_missing("tag") or self._env_missing("cut"):
            self.env_dialog(None)

    def _env_missing(self, page):
        return [it for it in check_env() if (not it["ok"]) and (page in it["need"])]

    def _env_ready(self, page):
        miss = self._env_missing(page)
        if not miss:
            return True
        self.env_dialog(page)
        return False

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
                           bg=PANEL, troughcolor=PANEL2, relief="flat", bd=0, width=10)
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
            items = check_env() if page is None else [it for it in check_env() if page in it["need"]]
            bad = [it for it in items if not it["ok"]]
            if page is None:
                title.configure(text=("一切正常，%d 项全部就绪" % len(items)) if not bad
                               else ("有 %d 项缺失，补齐后才能正常使用" % len(bad)))
                title.configure(fg=OK if not bad else ERR)
            else:
                title.configure(text=("可以开始，%d 项就绪" % len(items)) if not bad
                               else ("还缺 %d 项，补上才能用这一页" % len(bad)))
                title.configure(fg=OK if not bad else ERR)
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
                             anchor="w").pack(fill="x", padx=(40, 12), pady=(3, 0))
                    tk.Label(row, text=it["hint"], bg=PANEL, fg=DIM, font=(FONT, 9),
                             anchor="w", wraplength=620, justify="left").pack(
                        fill="x", padx=(40, 12), pady=(2, 9))
                else:
                    tk.Frame(row, bg=PANEL, height=4).pack()

        def do_fix(key):
            try:
                ok, msg = apply_env_fix(key)
            except Exception as e:
                ok, msg = False, str(e)
            messagebox.showinfo("修复" if ok else "修复失败", msg)
            rebuild()

        rebuild()

        foot = tk.Frame(top, bg=BG)
        foot.pack(fill="x", padx=22, pady=14)
        tk.Label(foot, text="另需 NVIDIA 显卡，显存 8G 以上", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(side="left")
        self._btn(foot, "关闭", top.destroy, width=8).pack(side="right")
        self._btn(foot, "重新检测", rebuild, "primary", width=10).pack(side="right", padx=(0, 8))

        w, h = 780, 620
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + 60
        top.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

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
            self.var_out.set(os.path.normpath(d))

    def _log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M ") + text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        if self.running:
            return
        if not self._env_ready("tag"):
            return
        src = self.var_src.get().strip()
        out = self.var_out.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showwarning("提示", "请先选择素材目录")
            return
        if not out:
            out = src.rstrip("\\/") + "_打标"
            self.var_out.set(out)
        self.running = True
        self.stop_event.clear()
        self.btn_run.configure(state="disabled", text="运行中…")
        self.btn_stop.configure(state="normal")
        self.pbar["value"] = 0
        self.last_out = out

        args = (src, out, self.var_frames.get(), self.var_gallery.get(), self.var_resume.get())

        def target():
            try:
                res = run_pipeline(
                    *args,
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
        if self.running:
            self.stop_event.set()
            self._log("正在停止…当前片段处理完就退出", "dim")
            self.btn_stop.configure(state="disabled")

    def _pump(self):
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
                    self._finish(msg[1])
                elif kind == "cancelled":
                    self._reset("已停止")
                    self._log("已停止（已处理的片段结果不会保存）", "err")
                elif kind == "error":
                    self._reset("出错")
                    self._log(msg[1], "err")
                    messagebox.showerror("出错了", msg[1].strip().splitlines()[-1][:300])
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

    def _reset(self, stage_text=""):
        self.running = False
        self.btn_run.configure(state="normal", text="开始打标")
        self.btn_stop.configure(state="disabled")
        if stage_text:
            self.lbl_stage.configure(text=stage_text)

    def _open_out(self):
        d = self.last_out or self.var_out.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "还没有输出目录")

    def _open_html(self):
        d = self.last_out or self.var_out.get().strip()
        p = os.path.join(d, "preview.html") if d else ""
        if p and os.path.exists(p):
            webbrowser.open("file:///" + p.replace("\\", "/"))
        else:
            messagebox.showinfo("提示", "还没有生成预览页")

    def _open_csv(self):
        d = self.last_out or self.var_out.get().strip()
        p = os.path.join(d, "tags.csv") if d else ""
        if p and os.path.exists(p):
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
    App(root)
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
