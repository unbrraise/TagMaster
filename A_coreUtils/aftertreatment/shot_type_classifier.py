# -*- coding: utf-8 -*-
"""景别分类器（DinoV2）

接口设计参考 TuanToolKit（原作者）的 A_coreUtils/aftertreatment/shot_type_classifier.py，
本文件为独立实现，不包含其源码，仅保留相同的类名与方法签名以便调用。

模型：models/aslakey_shot_scale/（HuggingFace 上的 aslakey/shot-scale）
五类：大特写 / 特写 / 中景 / 全景 / 远景（没有「空镜」，无人画面由调用方另行处理）

注意：原模型仓库通常不带 preprocessor_config.json，缺了会报
"Unrecognized image processor"。本项目会由环境自检自动生成该文件
（transformers 5.x 里 dinov2 映射到 BitImageProcessor）。
"""

import os
import sys
from typing import List, Tuple

import cv2
import numpy as np

_current_file = os.path.abspath(__file__)
_aftertreatment_dir = os.path.dirname(_current_file)
_a_core_utils_dir = os.path.dirname(_aftertreatment_dir)
_project_root = os.path.dirname(_a_core_utils_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# 模型输出的英文类名 → 中文
LABELS_CN = {
    "extreme_close_up": "大特写",
    "close_up":         "特写",
    "medium":           "中景",
    "full":             "全景",
    "wide":             "远景",
}


class DinoV2ShotClassifier:
    """景别分类器：一张图 → (景别, 置信度)"""

    MODEL_NAME = "aslakey_shot_scale"

    def __init__(self, model_path: str = None):
        self._model_path = model_path or os.path.join(_project_root, "models", self.MODEL_NAME)
        self._model = None
        self._processor = None
        self._device = None
        self._loaded = False

    # ---------------- 加载 ----------------
    def load(self):
        if self._loaded:
            return
        import torch
        from transformers import AutoImageProcessor, AutoModelForImageClassification

        if not os.path.isdir(self._model_path):
            raise FileNotFoundError(
                "景别模型目录不存在：%s\n请把 aslakey/shot-scale 放到 models\\%s"
                % (self._model_path, self.MODEL_NAME))

        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print("[DinoV2] 加载：%s" % self._model_path)

        try:
            self._processor = AutoImageProcessor.from_pretrained(
                self._model_path, local_files_only=True)
        except Exception as e:
            raise RuntimeError(
                "景别模型的处理器加载失败：%s\n"
                "多半是缺 preprocessor_config.json，打开程序右上角「环境检查」一键生成。" % e)

        self._model = AutoModelForImageClassification.from_pretrained(
            self._model_path, local_files_only=True).to(self._device)
        self._model.eval()
        self._loaded = True
        print("[DinoV2] 就绪，设备：%s" % self._device)

    def unload(self):
        if self._model is not None:
            del self._model
            self._model = None
        self._processor = None
        self._loaded = False
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ---------------- 推理 ----------------
    def predict_frame(self, frame_bgr: np.ndarray) -> Tuple[str, float]:
        """单帧 BGR (OpenCV 格式) → (中文景别, 置信度)"""
        if not self._loaded:
            self.load()

        import torch
        from PIL import Image

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        inputs = self._processor(images=Image.fromarray(rgb), return_tensors="pt").to(self._device)

        with torch.no_grad():
            logits = self._model(**inputs).logits
            probs = torch.softmax(logits, dim=1)
            idx = int(torch.argmax(probs, dim=1).item())
            conf = float(probs[0, idx].item())

        label_en = self._model.config.id2label.get(idx, str(idx))
        return LABELS_CN.get(label_en, label_en), conf

    # 兼容旧调用名
    _predict_frame = predict_frame

    # ---------------- 视频 ----------------
    def extract_frames(self, video_path: str, num_frames: int = 5) -> List[np.ndarray]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []
        if total > 0:
            for i in range(num_frames):
                pos = int(total * (i + 1) / (num_frames + 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ok, frame = cap.read()
                if ok and frame is not None:
                    frames.append(frame)
        else:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(frame)
                if len(frames) >= num_frames:
                    break
        cap.release()
        return frames

    def analyze(self, file_path: str) -> dict:
        """视频：抽若干帧投票；图片：直接分类。主流程不调用，留给单独使用。"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            frame = cv2.imread(file_path)
            if frame is None:
                return {"景别": "未知", "confidence": 0.0}
            label, conf = self.predict_frame(frame)
            return {"景别": label, "confidence": round(conf, 3)}

        frames = self.extract_frames(file_path, 5)
        if not frames:
            return {"景别": "未知", "confidence": 0.0}
        votes = {}
        for fr in frames:
            label, conf = self.predict_frame(fr)
            votes.setdefault(label, 0.0)
            votes[label] += conf
        best = max(votes, key=votes.get)
        return {"景别": best, "confidence": round(votes[best] / len(frames), 3)}
