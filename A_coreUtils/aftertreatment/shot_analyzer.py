# -*- coding: utf-8 -*-
"""MiniCPM-V 模型加载与推理封装

接口设计参考 TuanToolKit（原作者）的 A_coreUtils/aftertreatment/shot_analyzer.py，
本文件为独立实现，不包含其源码，仅保留相同的类名与方法签名以便调用。

只做三件事：
  1. 把 MiniCPM-V 的权重和 processor 加载到显存
  2. 暴露 _processor / _model 供调用方自己拼 prompt
  3. 提供一个通用的 image_generate() 快捷方法

模型路径按 __file__ 反推：<根>/A_coreUtils/aftertreatment/ → <根>/models/<模型名>
"""

import os
import sys

import torch

_current_file = os.path.abspath(__file__)
_aftertreatment_dir = os.path.dirname(_current_file)
_a_core_utils_dir = os.path.dirname(_aftertreatment_dir)
_project_root = os.path.dirname(_a_core_utils_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class ShotAnalyzer:
    """MiniCPM-V 模型封装（单例：多次实例化只加载一次）"""

    DEFAULT_MODEL = "MiniCPM-V-4-6"

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, model_name: str = None, device: str = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.model_name = model_name or self.DEFAULT_MODEL
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model = None
        self._processor = None
        self.model_loaded = False

        # 视觉处理参数：调用方可以改，改变了就影响下一次 processor 调用
        self.downsample_mode = "4x"   # "16x" 更快 / "4x" 更细
        self.max_slice_nums = 1       # 单图切成几块；切片越多越慢越占显存
        self.use_image_id = False

        # 生成参数
        self.max_new_tokens = 160
        self.do_sample = False
        self.repetition_penalty = 1.05

    # ---------------- 路径 ----------------
    @property
    def model_path(self):
        return os.path.join(_project_root, "models", self.model_name)

    # ---------------- 加载 / 卸载 ----------------
    def load_model(self):
        if self.model_loaded:
            return

        if not os.path.isdir(self.model_path):
            raise FileNotFoundError(
                "模型目录不存在：%s\n请把 %s 整个文件夹放到 models\\ 下"
                % (self.model_path, self.model_name))

        print("[MiniCPM] 加载：%s" % self.model_path)
        from transformers import AutoProcessor, AutoModelForImageTextToText

        self._processor = AutoProcessor.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=True)

        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=True,
            dtype=dtype,
        ).to(self.device)
        self._model.eval()

        self.model_loaded = True
        print("[MiniCPM] 就绪，设备：%s" % self.device)

    def unload_model(self):
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        self.model_loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ---------------- 推理 ----------------
    def image_generate(self, images, prompt, max_new_tokens=None):
        """多图 + 一段文字 → 模型回答

        Args:
            images: PIL.Image 列表，或单个 PIL.Image
            prompt: 提问文字
        Returns:
            模型输出的字符串
        """
        if not self.model_loaded:
            self.load_model()

        if not isinstance(images, (list, tuple)):
            images = [images]

        content = [{"type": "image", "image": im} for im in images]
        content.append({"type": "text", "text": prompt})
        msgs = [{"role": "user", "content": content}]

        inputs = self._processor.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
                do_sample=self.do_sample,
                repetition_penalty=self.repetition_penalty,
                eos_token_id=self._processor.tokenizer.eos_token_id,
            )
        resp = out[0][inputs["input_ids"].shape[1]:]
        return self._processor.tokenizer.decode(resp, skip_special_tokens=True)

    @property
    def tokenizer(self):
        return self._processor.tokenizer if self._processor else None
