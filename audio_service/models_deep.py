# -*- coding: utf-8 -*-
"""
轨 B：深度音频模型封装（SenseVoiceSmall / GPU）
================================================================
职责边界
--------
本模块只做一件事：把波形送进 SenseVoiceSmall，取出
  1) 语音情绪的【概率分布】（7 类 + unk）
  2) 声学事件标签（BGM / Laughter / Cough / Applause ...）
  3) ASR 文本（用于校验用户是否读了指定文本、以及统计实际字数）
不做任何心理状态推断 —— 状态量合成在上层完成。

关键实现说明（均已在本机实测确认，非文档推测）
--------------------------------------------------
1) 为什么不用 AutoModel.generate()？
   官方 generate() 只返回一个【离散标签】字符串，例如
       "<|zh|><|NEUTRAL|><|BGM|><|withitn|>我。"
   而「情绪构成」板块需要的是各情绪的【占比】。因此本模块直接调用
   encoder + ctc.log_softmax，在情绪 token 位上取 softmax 概率，
   得到真正的分布。已验证该分布的 argmax 与官方 generate() 的
   离散标签一致（both -> neutral），即取法正确、未偏离模型语义。

2) 情绪 token 的位置【已实测定位】
   SenseVoiceSmall.inference 的 prompt 拼接顺序为：
       [language_query, event_query, emo_query, textnorm_query, fbank...]
   其中 emo_query 是 embed([[1,2]]) 的第 2 个，即序列 index=1。
   实测 argmax(前6帧) = [24884, 25004, 24995, 25016, 0, 0]
                        ^lang  ^emo   ^event ^itn
   故情绪概率取 index=1 帧。同时保留「全序列最大池化」作为
   对照通道，两者不一致时会在输出里标记出来。

3) 情绪标签只有 7 类，且 unk 是一个真实的可能输出
   {happy, sad, angry, neutral, fearful, disgusted, surprised} + unk。
   unk 概率高说明模型对该段无把握，此时【不应】把剩余概率强行
   重分配到 7 类上制造「看起来确定」的分布 —— unk 会被原样返回，
   由上层决定是否采用。

4) 采样率
   SenseVoice 的 frontend 固定 16kHz。前端上传 48kHz WAV 时，
   本模块内部重采样到 16k 供模型使用；而轨 A 的 jitter/shimmer
   仍在原始 48kHz 上计算（周期扰动测量需要高采样率）。
   两条轨用不同采样率是刻意设计，不是 bug。
"""
from __future__ import annotations

import math
import os
import threading

import numpy as np

# ---------------------------------------------------------------- 常量

DEFAULT_MODEL_DIR = os.environ.get(
    "SENSEVOICE_DIR",
    "/home/lsz/audio_service/models/models/iic--SenseVoiceSmall/snapshots/master",
)
MODELSCOPE_CACHE = os.environ.get(
    "MODELSCOPE_CACHE", "/home/lsz/audio_service/models")

MODEL_SR = 16000          # SenseVoice frontend 固定 16k
EMO_QUERY_POS = 1         # 情绪 token 所在帧（已实测定位，见模块头注）

# ---------------------------------------------------------------- token 表
# ⚠️ 这些 id【必须从 tokens.json 读取】，不得硬编码。
# 教训：本模块初版按「情绪 id 连续，事件紧随其后」的直觉硬编码了事件 id，
# 结果 bgm 被写成 25010 —— 而 25010 实际是 <|Cry|>，<|BGM|> 真实 id 是 24995。
# 该错误不会报错、不会崩，只会让「录音环境是否干净」这一路指标
# 长期读到另一个事件的概率。故改为从模型自带的 tokens.json 解析，
# 并在解析失败时明确报错，而不是回退到可能错误的常量。
EMO_LABELS = {
    "happy":     "<|HAPPY|>",
    "sad":       "<|SAD|>",
    "angry":     "<|ANGRY|>",
    "neutral":   "<|NEUTRAL|>",
    "fearful":   "<|FEARFUL|>",
    "disgusted": "<|DISGUSTED|>",
    "surprised": "<|SURPRISED|>",
    "unk":       "<|EMO_UNKNOWN|>",
}

EVENT_LABELS = {
    "bgm":          "<|BGM|>",
    "speech":       "<|Speech|>",
    "applause":     "<|Applause|>",
    "laughter":     "<|Laughter|>",
    "cry":          "<|Cry|>",
    "sneeze":       "<|Sneeze|>",
    "breath":       "<|Breath|>",
    "cough":        "<|Cough|>",
    "sing":         "<|Sing|>",
    "speech_noise": "<|Speech_Noise|>",
}


def load_token_ids(model_dir):
    """
    从模型自带的 tokens.json 解析 token id。
    返回 (emo_ids, event_ids)，任一缺失即抛异常 —— 宁可启动失败，
    也不要带着错误的 id 静默运行。
    """
    import json
    p = os.path.join(model_dir, "tokens.json")
    with open(p, "r", encoding="utf-8") as f:
        toks = json.load(f)
    index = {t: i for i, t in enumerate(toks) if isinstance(t, str)}

    def resolve(mapping, what):
        out, missing = {}, []
        for name, tok in mapping.items():
            if tok in index:
                out[name] = index[tok]
            else:
                missing.append(tok)
        if missing:
            raise KeyError(f"{what} token 未在 tokens.json 中找到: {missing}")
        return out

    return (resolve(EMO_LABELS, "情绪"), resolve(EVENT_LABELS, "声学事件"))

# unk 概率超过此值 -> 认为模型对该段情绪无把握
UNK_DOMINANT_TH = 0.50


def _safe(v, nd=4):
    """nan/inf -> None，保证 JSON 可序列化。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


# ---------------------------------------------------------------- 模型持有者

class SenseVoiceRunner:
    """
    SenseVoiceSmall 的线程安全封装。

    模型加载耗时约 1~2s、占显存约 1GB，因此【进程内只加载一次】，
    由 Flask 在启动时预热。GPU 推理不可重入，故用锁串行化
    （实测 RTF≈0.058，即 3s 音频约 0.17s，串行完全够用；
      与 openface_service 的做法一致）。
    """

    def __init__(self, model_dir=DEFAULT_MODEL_DIR, device="cuda:0"):
        os.environ.setdefault("MODELSCOPE_CACHE", MODELSCOPE_CACHE)
        self.model_dir = model_dir
        self.device = device
        self._lock = threading.Lock()
        self._model = None
        self._frontend = None
        self._torch = None
        self._extract_fbank = None
        self.load_error = None
        self.emo_ids = None      # 从 tokens.json 解析，见 load_token_ids
        self.event_ids = None

    # -------------------------------------------------- 加载
    def load(self):
        """加载模型。失败时不抛异常，记录 load_error 供 /health 暴露。"""
        if self._model is not None or self.load_error is not None:
            return self._model is not None
        try:
            import torch
            from funasr import AutoModel
            from funasr.utils.load_utils import extract_fbank

            if not os.path.isdir(self.model_dir):
                raise FileNotFoundError(f"模型目录不存在: {self.model_dir}")

            # 先解析 token id：解析不出来就没必要加载 936MB 权重
            self.emo_ids, self.event_ids = load_token_ids(self.model_dir)

            dev = self.device
            if dev.startswith("cuda") and not torch.cuda.is_available():
                # 明确降级并记录，而不是静默用 CPU（CPU 上 RTF 会差一个量级）
                dev = "cpu"

            am = AutoModel(model=self.model_dir, trust_remote_code=False,
                           device=dev, disable_update=True)
            self._torch = torch
            self._extract_fbank = extract_fbank
            self._model = am.model
            self._frontend = am.kwargs["frontend"]
            self._model.eval()
            self.device = dev
            return True
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {e}"
            self._model = None
            return False

    @property
    def ready(self):
        return self._model is not None

    def info(self):
        d = {"ready": self.ready, "device": self.device,
             "model_dir": self.model_dir, "model_sr": MODEL_SR}
        if self.load_error:
            d["load_error"] = self.load_error
        if self.emo_ids:
            # 暴露实际解析到的 id，便于发现模型换版导致的 token 漂移
            d["emo_token_ids"] = self.emo_ids
            d["event_token_ids"] = self.event_ids
            d["emo_query_pos"] = EMO_QUERY_POS
        if self.ready and self._torch is not None:
            try:
                if self.device.startswith("cuda"):
                    d["gpu_name"] = self._torch.cuda.get_device_name(0)
            except Exception:
                pass
        return d

    # -------------------------------------------------- 前处理
    def _to_model_sr(self, y, sr):
        """重采样到 16k（模型 frontend 要求）。"""
        y = np.asarray(y, dtype=np.float32).ravel()
        if sr == MODEL_SR:
            return y
        import librosa
        return librosa.resample(y.astype(np.float64),
                                orig_sr=sr,
                                target_sr=MODEL_SR).astype(np.float32)

    def _ctc_logprob(self, y16):
        """
        跑 encoder + ctc，返回 [T, V] 的 log 概率。
        prompt 拼接顺序严格复刻 SenseVoiceSmall.inference（见模块头注），
        任何顺序改动都会让情绪 token 的位置漂移。
        """
        torch = self._torch
        mm, fe, dev = self._model, self._frontend, self.device
        sp, spl = self._extract_fbank([torch.from_numpy(y16)],
                                      data_type="sound", frontend=fe)
        sp = sp.to(dev)
        spl = spl.to(dev)

        tn = mm.embed(torch.LongTensor(
            [[mm.textnorm_dict["withitn"]]]).to(dev)).repeat(sp.size(0), 1, 1)
        sp = torch.cat((tn, sp), dim=1)
        spl = spl + 1

        ee = mm.embed(torch.LongTensor([[1, 2]]).to(dev)).repeat(sp.size(0), 1, 1)
        lq = mm.embed(torch.LongTensor(
            [[mm.lid_dict["zh"]]]).to(dev)).repeat(sp.size(0), 1, 1)
        sp = torch.cat((torch.cat((lq, ee), dim=1), sp), dim=1)
        spl = spl + 3

        enc, enl = mm.encoder(sp, spl)
        if isinstance(enc, tuple):
            enc = enc[0]
        lg = mm.ctc.log_softmax(enc)
        n = int(enl[0].item())
        return lg[0, :n, :]

    # -------------------------------------------------- 推理
    def analyze(self, y, sr, want_text=True):
        """
        返回 dict：
          emotion   : {label, confidence, distribution{8类}, unk_dominant,
                       reliable, agreement}
          events    : {检出的声学事件: 概率}
          asr       : {text, char_count}   （want_text=False 时为 None）
          error     : 失败原因（其余字段为 None）
        """
        out = {"emotion": None, "events": None, "asr": None, "error": None}

        if not self.ready:
            out["error"] = self.load_error or "模型未加载"
            return out

        try:
            y16 = self._to_model_sr(y, sr)
            if y16.size < MODEL_SR * 0.3:
                out["error"] = "音频过短（模型侧要求 >= 0.3s）"
                return out

            torch = self._torch
            with self._lock, torch.no_grad():
                lg = self._ctc_logprob(y16)

                names = list(self.emo_ids.keys())
                ids = [self.emo_ids[k] for k in names]

                # 主通道：情绪 query 所在帧
                pos = min(EMO_QUERY_POS, lg.size(0) - 1)
                pr = lg[pos, ids].exp()
                pr = (pr / pr.sum()).cpu().numpy()

                # 对照通道：全序列最大池化（用于交叉校验）
                pr2 = lg[:, ids].exp().max(dim=0).values
                pr2 = (pr2 / pr2.sum()).cpu().numpy()

                # 事件通道：各事件在全序列上的最高概率
                ev_names = list(self.event_ids.keys())
                ev_ids = [self.event_ids[k] for k in ev_names]
                ev = lg[:, ev_ids].exp().max(dim=0).values.cpu().numpy()

                text = None
                if want_text:
                    seq = torch.unique_consecutive(lg.argmax(dim=-1), dim=-1)
                    seq = seq[seq != self._model.blank_id].tolist()
                    text = self._decode(seq)

            dist = {k: _safe(v) for k, v in zip(names, pr)}
            dist2 = {k: _safe(v) for k, v in zip(names, pr2)}

            # 只在 7 个真实情绪里取 label（unk 不是情绪，是「说不准」）
            real = {k: v for k, v in dist.items() if k != "unk"}
            label = max(real, key=real.get)
            unk_p = dist.get("unk") or 0.0
            unk_dom = unk_p >= UNK_DOMINANT_TH

            real2 = {k: v for k, v in dist2.items() if k != "unk"}
            label2 = max(real2, key=real2.get)

            reasons = []
            if unk_dom:
                reasons.append(f"模型对该段情绪无把握（unk={unk_p}）")
            if label != label2:
                reasons.append(
                    f"主通道({label})与对照通道({label2})不一致，结果不稳定")

            out["emotion"] = {
                "label": label,
                "confidence": dist.get(label),
                "distribution": dist,
                "distribution_maxpool": dist2,
                "unk_prob": _safe(unk_p),
                "unk_dominant": bool(unk_dom),
                "agreement": bool(label == label2),
                "reliable": len(reasons) == 0,
                "unreliable_reasons": reasons,
                "labels_supported": [k for k in names if k != "unk"],
            }
            out["events"] = {k: _safe(v) for k, v in zip(ev_names, ev)}
            if want_text:
                clean = self._strip_tags(text or "")
                # char_count 保留原口径（全部字符），另加 han_count。
                # 朗读覆盖率必须用 han_count 与文本全文的汉字数比较 ——
                # 口径不一致（一边含标点一边不含）会让覆盖率失真，
                # 进而把读完的用户误判成未读完。
                out["asr"] = {"text": clean,
                              "char_count": len(clean),
                              "han_count": sum(
                                  1 for ch in clean
                                  if "\u4e00" <= ch <= "\u9fff"
                                  or "\u3400" <= ch <= "\u4dbf"),
                              "raw": text}
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out

    # -------------------------------------------------- 文本工具
    def _decode(self, token_ids):
        try:
            from funasr.tokenizer.sentencepiece_tokenizer import (
                SentencepiecesTokenizer)
            if not hasattr(self, "_tk"):
                bpe = os.path.join(
                    self.model_dir, "chn_jpn_yue_eng_ko_spectok.bpe.model")
                self._tk = SentencepiecesTokenizer(bpemodel=bpe)
            return self._tk.decode(token_ids)
        except Exception:
            return None

    @staticmethod
    def _strip_tags(text):
        """去掉 <|zh|><|NEUTRAL|> 这类控制标签，只留可读文本。"""
        import re
        return re.sub(r"<\|[^|]*\|>", "", text or "").strip()


# 进程级单例（Flask 启动时 load()，请求内直接用）
RUNNER = SenseVoiceRunner()
