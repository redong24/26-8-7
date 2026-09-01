/* ==================================================================
   voice_recorder.js —— 语音任务录音模块（心理综合评估 v3 · 语音卡）

   为什么单独一个文件
   ------------------
   shell_panels.js 已 1000+ 行且承载 5 个面板的结构定义；录音涉及
   AudioWorklet、WAV 封装、状态机、上传与结果渲染，塞进去会让那个
   文件继续膨胀且难以定位问题。本文件只暴露一个入口
   window.VoiceRecorder.mount(root)，由 psy 面板的 mount 调用。

   与后端的契约（务必对齐，改动前先看 audio_client.py）
   ---------------------------------------------------
   GET  /audio/task_spec  -> { sample_rate:48000, format, stages:[...],
                               disabled_stages:[...] }
        stages[i] 关键字段：
          id                 阶段标识，上传时作为 ?stage= 传回
          duration_sec       锁定时长（元音段=5）；null 表示不锁定
          duration_mode      'until_user_done' 时不倒计时，由用户点完成
          duration_hint_sec  仅用于给用户「大约多久」的预期，不是硬限制
          max_duration_sec   硬上限，超过后端直接 413
          sustained_vowel    true 时后端跳过 ASR/情绪
          text/text_char_count  朗读文本与汉字数
   POST /audio/upload?stage=<id>
        请求体是【裸 WAV 字节】，Content-Type: application/octet-stream。
        注意不是 multipart/form-data —— 后端用
        request.get_data(parse_form_data=False) 读原始 body，
        用 FormData 会把 multipart 边界当成 WAV 内容导致解析失败。
   GET  /audio/result     -> 取回本会话已完成各阶段的结果

   音频格式：48kHz / 16bit / 单声道 / WAV(PCM)
   —— 采样率必须与 task_spec.sample_rate 一致。后端按 48000 估算时长
   做 413 前置拦截，且 audio_client 的注释明确「与前端封装参数一致」。
   ================================================================== */
(function () {
  'use strict';

  /* ---------- 常量 ---------- */
  var UPLOAD_MIME = 'application/octet-stream';
  var BITS = 16;
  var CHANNELS = 1;

  /* AudioWorklet 处理器源码。
     用 Blob URL 动态注入，避免为一个几十行的处理器单独加一个静态文件
     （静态资源无版本号，多一个文件就多一处缓存风险）。
     职责仅限「把 Float32 帧原样投递到主线程」，不做任何重采样或降噪：
     采样率由 AudioContext 指定，重采样交给浏览器实现。 */
  var WORKLET_SRC = [
    'class PCMCollector extends AudioWorkletProcessor {',
    '  process (inputs) {',
    '    var input = inputs[0];',
    '    if (input && input[0]) {',
    // 必须复制：底层 buffer 会被复用，直接 postMessage 引用会拿到被覆写的数据
    '      this.port.postMessage(new Float32Array(input[0]));',
    '    }',
    '    return true;',
    '  }',
    '}',
    'registerProcessor("pcm-collector", PCMCollector);'
  ].join('\n');

  /* ---------- 工具 ---------- */

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }

  /* 把若干 Float32Array 拼成一个 */
  function concatFloat32(chunks, total) {
    var out = new Float32Array(total), off = 0;
    for (var i = 0; i < chunks.length; i++) {
      out.set(chunks[i], off);
      off += chunks[i].length;
    }
    return out;
  }

  /**
   * Float32 [-1,1] -> WAV(PCM 16bit) 字节。
   * 前端封装 WAV 是既定方案（用户决策）：后端只接裸 WAV，
   * 不引入服务端转码依赖。
   */
  function encodeWAV(samples, sampleRate) {
    var bytesPerSample = BITS / 8;
    var blockAlign = CHANNELS * bytesPerSample;
    var dataSize = samples.length * bytesPerSample;
    var buf = new ArrayBuffer(44 + dataSize);
    var v = new DataView(buf);

    function str(off, s) {
      for (var i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
    }
    str(0, 'RIFF');
    v.setUint32(4, 36 + dataSize, true);
    str(8, 'WAVE');
    str(12, 'fmt ');
    v.setUint32(16, 16, true);        // fmt chunk 长度
    v.setUint16(20, 1, true);         // PCM
    v.setUint16(22, CHANNELS, true);
    v.setUint32(24, sampleRate, true);
    v.setUint32(28, sampleRate * blockAlign, true);
    v.setUint16(32, blockAlign, true);
    v.setUint16(34, BITS, true);
    str(36, 'data');
    v.setUint32(40, dataSize, true);

    // 截幅后转 int16。乘 0x7FFF 而非 0x8000，避免 +1.0 溢出成 -32768
    var off = 44;
    for (var i = 0; i < samples.length; i++, off += 2) {
      var s = samples[i];
      s = s < -1 ? -1 : (s > 1 ? 1 : s);
      v.setInt16(off, Math.round(s * 0x7FFF), true);
    }
    return buf;
  }

  /* 数值渲染：null/undefined 一律显示占位，绝不显示 0。
     后端对「没测到」明确返回 null（例如零识别时 cpm=null），
     若这里退化成 0，就会把「未测量」伪装成一个真实数据点。 */
  function num(x, digits, unit) {
    if (x === null || x === undefined || (typeof x === 'number' && !isFinite(x))) {
      return '—';
    }
    var s = (digits === 0) ? String(Math.round(x)) : Number(x).toFixed(digits);
    return s + (unit || '');
  }

  /* ---------- 主体 ---------- */

  function VoiceTask(root) {
    this.root = root;
    this.spec = null;
    this.stages = [];
    this.stageIdx = 0;
    this.sampleRate = 48000;

    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.srcNode = null;
    this.analyser = null;

    this.chunks = [];
    this.total = 0;
    this.recording = false;
    this.startedAt = 0;
    this.rafId = 0;
    this.tickId = 0;
    this.results = {};
    this.busy = false;

    this.el = {};
  }

  VoiceTask.prototype.q = function (sel) {
    return this.root.querySelector(sel);
  };

  /* ---- 初始化：拉取 task_spec ---- */
  VoiceTask.prototype.init = function () {
    var self = this;
    this.cacheEls();
    this.bind();
    this.setStatus('正在获取任务定义…', 'wait');

    return fetch('/audio/task_spec', { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('task_spec HTTP ' + r.status);
        return r.json();
      })
      .then(function (spec) {
        self.spec = spec;
        self.sampleRate = spec.sample_rate || 48000;
        self.stages = (spec.stages || []).filter(function (s) { return !!s; });
        if (!self.stages.length) throw new Error('task_spec 未返回任何阶段');
        self.stageIdx = 0;
        self.renderStage();
        self.setStatus('准备就绪', 'idle');
      })
      .catch(function (err) {
        // 拿不到任务定义时禁用按钮：宁可不能用，也不要用前端硬编码的
        // 时长/文本去录一段与后端契约不一致的音频。
        self.setStatus('无法获取任务定义：' + err.message, 'err');
        self.disableMic('任务定义获取失败');
      });
  };

  VoiceTask.prototype.cacheEls = function () {
    this.el.tabs = this.q('[data-vt-tabs]');
    this.el.lbl = this.q('[data-vt-lbl]');
    this.el.text = this.q('[data-vt-text]');
    this.el.wave = this.q('[data-vt-wave]');
    this.el.cur = this.q('[data-vt-cur]');
    this.el.tot = this.q('[data-vt-tot]');
    this.el.mic = this.q('[data-vt-mic]');
    this.el.mode = this.q('[data-vt-mode]');
    this.el.metaSr = this.q('[data-vt-sr]');
    this.el.metaF0 = this.q('[data-vt-f0]');
    this.el.status = this.q('[data-vt-status]');
    this.el.metrics = this.q('[data-vt-metrics]');
    this.bars = this.el.wave
      ? Array.prototype.slice.call(this.el.wave.querySelectorAll('.bar'))
      : [];
  };

  VoiceTask.prototype.bind = function () {
    var self = this;
    if (this.el.mic) {
      this.el.mic.addEventListener('click', function () {
        if (self.busy) return;
        if (self.recording) self.stop();
        else self.start();
      });
    }
    // 面板关闭时释放麦克风：不释放会让浏览器一直显示录音指示灯
    document.addEventListener('psy-panel-close', function () {
      self.teardown();
    });
  };

  VoiceTask.prototype.cur = function () {
    return this.stages[this.stageIdx] || null;
  };

  /* ---- 渲染当前阶段的提示与时长 ---- */
  /**
   * 判断某阶段是否「锁定时长」。
   * 判定依据是 duration_sec 是否为 null（唯一权威来源），
   * 同时校验它与 duration_mode 是否自相矛盾：后端约定
   * duration_mode==='until_user_done' 必须搭配 duration_sec===null。
   * 若两者冲突，说明前后端契约已经漂移，此时【以不锁定为准】——
   * 宁可让用户自己点完成，也不要在没读完时把录音切断（切断会导致
   * 语速分子仍按全文算而系统性高估，正是本次改动要消除的失败模式）。
   */
  VoiceTask.prototype.isLocked = function (s) {
    var hasDur = (s.duration_sec !== null && s.duration_sec !== undefined);
    var untilDone = (s.duration_mode === 'until_user_done');
    if (hasDur && untilDone) {
      console.warn('[voice] 契约冲突：duration_mode=until_user_done 但 '
        + 'duration_sec=' + s.duration_sec + '，按「不锁定」处理');
      return false;
    }
    return hasDur;
  };

  VoiceTask.prototype.renderStage = function () {
    var s = this.cur();
    if (!s) return;
    var locked = this.isLocked(s);

    if (this.el.tabs) {
      var self = this;
      Array.prototype.forEach.call(
        this.el.tabs.querySelectorAll('[data-vt-tab]'), function (t) {
          t.classList.toggle('on', t.getAttribute('data-vt-tab') === s.id);
        });
    }
    if (this.el.lbl) {
      this.el.lbl.textContent = s.prompt
        || (s.sustained_vowel ? '请持续发一个稳定的元音' : '请朗读以下文字');
    }
    if (this.el.text) {
      if (s.text) {
        this.el.text.textContent = s.text;
        this.el.text.classList.remove('vowel');
      } else {
        // 元音段没有文本，用一个大字母提示，而不是留空
        this.el.text.textContent = '啊 —— /a/';
        this.el.text.classList.add('vowel');
      }
    }
    if (this.el.tot) {
      // 锁定时长显示「/ 总时长」；不锁定时显示「约 xx」而非硬性总时长，
      // 避免用户以为到点会被切断（duration_mode=until_user_done）
      this.el.tot.textContent = locked
        ? ('/ ' + fmtTime(s.duration_sec))
        : ('约 ' + fmtTime(s.duration_hint_sec || 0));
    }
    if (this.el.cur) this.el.cur.textContent = '00:00';
    if (this.el.metaSr) {
      this.el.metaSr.textContent = (this.sampleRate / 1000) + 'kHz';
    }
    this.setMode(locked ? '待录音 · 到点自动停止' : '待录音 · 读完点完成');
    this.paintIdleWave();
  };

  VoiceTask.prototype.setMode = function (t) {
    if (this.el.mode) this.el.mode.textContent = t;
  };

  VoiceTask.prototype.setStatus = function (msg, kind) {
    if (!this.el.status) return;
    this.el.status.textContent = msg;
    this.el.status.className = 'pv3-vt-status' + (kind ? ' ' + kind : '');
  };

  VoiceTask.prototype.disableMic = function (why) {
    if (!this.el.mic) return;
    this.el.mic.classList.add('disabled');
    this.el.mic.setAttribute('title', why || '不可用');
  };

  VoiceTask.prototype.enableMic = function () {
    if (!this.el.mic) return;
    this.el.mic.classList.remove('disabled');
    this.el.mic.setAttribute('title', '开始 / 停止录音');
  };

  /* ---- 开始录音 ---- */
  VoiceTask.prototype.start = function () {
    var self = this;
    var s = this.cur();
    if (!s) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.setStatus('当前浏览器不支持录音（需 HTTPS + getUserMedia）', 'err');
      return;
    }

    this.busy = true;
    this.setStatus('正在请求麦克风权限…', 'wait');

    navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: CHANNELS,
        sampleRate: this.sampleRate,
        // 关掉浏览器的自动增益/降噪：它们会改变响度与频谱，
        // 而本任务要测的正是 jitter/shimmer/HNR 这类声学量。
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false
      }
    }).then(function (stream) {
      self.stream = stream;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) throw new Error('浏览器不支持 AudioContext');
      self.ctx = new AC({ sampleRate: self.sampleRate });

      if (!self.ctx.audioWorklet) {
        throw new Error('浏览器不支持 AudioWorklet');
      }
      var url = URL.createObjectURL(
        new Blob([WORKLET_SRC], { type: 'application/javascript' }));
      return self.ctx.audioWorklet.addModule(url).then(function () {
        URL.revokeObjectURL(url);
      });
    }).then(function () {
      self.chunks = [];
      self.total = 0;

      self.srcNode = self.ctx.createMediaStreamSource(self.stream);
      self.node = new AudioWorkletNode(self.ctx, 'pcm-collector');
      self.node.port.onmessage = function (e) {
        if (!self.recording) return;
        self.chunks.push(e.data);
        self.total += e.data.length;
      };
      // 波形用 AnalyserNode 取时域包络，比自己算 RMS 省事且不阻塞采集链
      self.analyser = self.ctx.createAnalyser();
      self.analyser.fftSize = 512;

      self.srcNode.connect(self.analyser);
      self.srcNode.connect(self.node);
      // 不接 destination：接了会把麦克风声音回放出来形成啸叫

      self.recording = true;
      self.busy = false;
      self.startedAt = (self.ctx.currentTime || 0);
      self.el.mic && self.el.mic.classList.add('rec');

      var st = self.cur();
      var locked = self.isLocked(st);
      self.setMode(locked ? '录音中 · 到点自动停止' : '录音中 · 读完点完成');
      self.setStatus(locked
        ? ('正在录音，' + st.duration_sec + ' 秒后自动停止')
        : '正在录音，读完后点按钮结束（不限时）', 'rec');

      self.loopWave();
      self.loopTimer();
    }).catch(function (err) {
      self.busy = false;
      self.teardown();
      var m = String(err && (err.name || err.message) || err);
      if (m.indexOf('NotAllowed') >= 0 || m.indexOf('Permission') >= 0) {
        self.setStatus('麦克风权限被拒绝，请在浏览器地址栏允许后重试', 'err');
      } else if (m.indexOf('NotFound') >= 0) {
        self.setStatus('未检测到麦克风设备', 'err');
      } else {
        self.setStatus('录音启动失败：' + m, 'err');
      }
    });
  };

  /* ---- 波形动画 ---- */
  VoiceTask.prototype.loopWave = function () {
    var self = this;
    if (!this.analyser || !this.bars.length) return;
    var buf = new Uint8Array(this.analyser.fftSize);

    function frame() {
      if (!self.recording) return;
      self.analyser.getByteTimeDomainData(buf);
      var n = self.bars.length;
      var per = Math.floor(buf.length / n) || 1;
      for (var i = 0; i < n; i++) {
        var peak = 0;
        for (var j = i * per; j < (i + 1) * per && j < buf.length; j++) {
          var dev = Math.abs(buf[j] - 128) / 128;
          if (dev > peak) peak = dev;
        }
        var h = 3 + Math.min(1, peak * 2.2) * 28;
        self.bars[i].style.height = h.toFixed(1) + 'px';
        self.bars[i].style.opacity = (0.30 + Math.min(1, peak * 2) * 0.65).toFixed(2);
      }
      self.rafId = requestAnimationFrame(frame);
    }
    this.rafId = requestAnimationFrame(frame);
  };

  VoiceTask.prototype.paintIdleWave = function () {
    for (var i = 0; i < this.bars.length; i++) {
      this.bars[i].style.height = '3px';
      this.bars[i].style.opacity = '0.16';
    }
  };

  /* ---- 计时：锁定段到点自停；不锁定段只累计并在接近上限时提醒 ---- */
  VoiceTask.prototype.loopTimer = function () {
    var self = this;
    if (this.tickId) clearInterval(this.tickId);

    this.tickId = setInterval(function () {
      if (!self.recording) return;
      var s = self.cur();
      var el = self.elapsed();
      if (self.el.cur) self.el.cur.textContent = fmtTime(el);

      var locked = self.isLocked(s);
      if (locked && el >= s.duration_sec) {
        self.stop();
        return;
      }
      if (!locked) {
        var max = s.max_duration_sec || 0;
        if (max) {
          // 到硬上限前主动停：让用户看到「已达上限」，
          // 而不是录完上传时才吃一个 413
          if (el >= max) {
            self.setStatus('已达录音上限 ' + max + ' 秒，自动停止', 'warn');
            self.stop();
            return;
          }
          if (max - el <= 15) {
            self.setStatus('接近上限，还可录 ' + Math.ceil(max - el) + ' 秒', 'warn');
          }
        }
      }
    }, 250);
  };

  VoiceTask.prototype.elapsed = function () {
    // 用累计样本数换算，比 Date.now() 更贴合实际采到的音频长度
    return this.total / (this.sampleRate || 48000);
  };

  /* ---- 停止并上传 ---- */
  VoiceTask.prototype.stop = function () {
    var self = this;
    if (!this.recording) return;
    this.recording = false;
    this.busy = true;
    if (this.rafId) cancelAnimationFrame(this.rafId), this.rafId = 0;
    if (this.tickId) clearInterval(this.tickId), this.tickId = 0;
    this.el.mic && this.el.mic.classList.remove('rec');
    this.paintIdleWave();

    var s = this.cur();
    var samples = concatFloat32(this.chunks, this.total);
    var secs = samples.length / this.sampleRate;
    this.chunks = [];

    this.teardownAudio();
    this.setMode('已停止');

    // 太短的录音没有分析价值，直接在前端拦下并说明原因，
    // 避免用户以为「上传成功了但结果是空的」
    var minSec = s.sustained_vowel ? 1.0 : 3.0;
    if (secs < minSec) {
      this.busy = false;
      this.setStatus('录音仅 ' + secs.toFixed(1) + ' 秒，太短无法分析（至少 '
        + minSec + ' 秒），请重新录制', 'err');
      return;
    }

    this.setStatus('正在分析（' + secs.toFixed(1) + ' 秒音频）…', 'wait');
    var wav = encodeWAV(samples, this.sampleRate);

    fetch('/audio/upload?stage=' + encodeURIComponent(s.id), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': UPLOAD_MIME },
      body: wav
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    }).then(function (res) {
      self.busy = false;
      if (!res.ok || res.body.status === 'error') {
        var msg = (res.body && (res.body.message || res.body.hint))
          || ('HTTP ' + res.status);
        self.setStatus('分析失败：' + msg, 'err');
        return;
      }
      self.results[s.id] = res.body;
      self.renderResult(s, res.body);
      self.advance();
    }).catch(function (err) {
      self.busy = false;
      self.setStatus('上传失败：' + (err && err.message || err), 'err');
    });
  };

  /* ---- 结果渲染 ---- */
  VoiceTask.prototype.renderResult = function (stage, body) {
    var sm = body.summary || {};

    if (this.el.metaF0) {
      this.el.metaF0.textContent = '基频 F0 · ' + num(sm.f0_mean, 0, ' Hz');
    }

    // usable=false 时不展示任何指标：后端已判定这段音频不可用，
    // 展示出来的数字会被当成结论
    if (sm.usable === false) {
      this.setStatus('本段音频不可用：' + (sm.unusable_reason || '未说明原因')
        + '，建议重录', 'err');
      if (this.el.metrics) this.el.metrics.innerHTML = '';
      return;
    }

    var rows = [];
    if (stage.sustained_vowel) {
      // 元音段：后端跳过 ASR/情绪，只有嗓音质量有意义
      rows.push(['基频 F0', num(sm.f0_mean, 1, ' Hz')]);
      rows.push(['基频稳定度', num(sm.f0_semitone_std, 2, ' st')]);
      rows.push(['Jitter', num(sm.jitter_local === null ? null
        : sm.jitter_local * 100, 2, '%')]);
      rows.push(['Shimmer', num(sm.shimmer_local === null ? null
        : sm.shimmer_local * 100, 2, '%')]);
      rows.push(['HNR', num(sm.hnr_db, 1, ' dB')]);
    } else {
      var sr = sm.speech_rate || {};
      // 语速要区分四种裁定：complete/incomplete/no_speech/unknown。
      // cpm 为 null 时显示「未测到」，绝不显示 0 —— 0 会作为
      // 「语速为零」这个真实数据点进入均值与趋势。
      var rateTxt = num(sm.speech_rate_cpm, 1, ' 字/分');
      var note = '';
      if (sr.verdict === 'incomplete') {
        note = '（未读完，按实际识别 ' + (sr.asr_chars || 0) + '/'
          + (sr.expected_chars || 0) + ' 字计，仅供参考）';
      } else if (sr.verdict === 'no_speech') {
        note = '（未识别到语音）';
      } else if (sr.verdict === 'unknown') {
        note = '（未获取识别结果）';
      }
      rows.push(['语速', rateTxt + note]);
      rows.push(['实际用时', num(sm.span_sec, 2, ' s')]);
      rows.push(['基频 F0', num(sm.f0_mean, 1, ' Hz')]);
      rows.push(['停顿次数', num(sm.pause_count, 0, ' 次')]);
      rows.push(['停顿占比', num(sm.pause_ratio === null ? null
        : sm.pause_ratio * 100, 1, '%')]);
      if (sm.emotion_label) {
        rows.push(['语音情绪', sm.emotion_label
          + (sm.emotion_reliable === false ? '（置信度低）' : '')]);
      }
    }

    if (this.el.metrics) {
      this.el.metrics.innerHTML = rows.map(function (r) {
        return '<div class="pv3-vt-row"><span class="k">' + r[0]
          + '</span><span class="v">' + r[1] + '</span></div>';
      }).join('');
    }

    // 可靠性提示：后端给了 *_reliable 与 reasons，如实转达
    var warn = [];
    if (stage.sustained_vowel && sm.voice_quality_reliable === false) {
      warn = warn.concat(sm.voice_quality_reasons || ['嗓音质量测量不稳定']);
    }
    if (!stage.sustained_vowel && sm.rhythm_reliable === false) {
      warn.push('节奏测量不稳定');
    }
    this.setStatus(warn.length ? ('已完成，但需注意：' + warn.join('；'))
      : '本段已完成', warn.length ? 'warn' : 'ok');
  };

  /* ---- 进入下一阶段 / 全部完成 ---- */
  VoiceTask.prototype.advance = function () {
    var self = this;
    if (this.stageIdx < this.stages.length - 1) {
      setTimeout(function () {
        self.stageIdx++;
        self.renderStage();
        self.setStatus('下一段：' + (self.cur().sustained_vowel
          ? '持续元音' : '朗读'), 'idle');
      }, 1600);
    } else {
      setTimeout(function () {
        self.setMode('全部完成');
        self.setStatus('两段语音任务均已完成', 'ok');
      }, 1200);
    }
  };

  /* ---- 资源释放 ---- */
  VoiceTask.prototype.teardownAudio = function () {
    try { if (this.node) this.node.port.onmessage = null; } catch (e) {}
    try { if (this.srcNode) this.srcNode.disconnect(); } catch (e) {}
    try { if (this.node) this.node.disconnect(); } catch (e) {}
    try { if (this.analyser) this.analyser.disconnect(); } catch (e) {}
    try {
      if (this.stream) {
        this.stream.getTracks().forEach(function (t) { t.stop(); });
      }
    } catch (e) {}
    try { if (this.ctx && this.ctx.state !== 'closed') this.ctx.close(); } catch (e) {}
    this.node = this.srcNode = this.analyser = null;
    this.stream = null;
    this.ctx = null;
  };

  VoiceTask.prototype.teardown = function () {
    this.recording = false;
    if (this.rafId) cancelAnimationFrame(this.rafId), this.rafId = 0;
    if (this.tickId) clearInterval(this.tickId), this.tickId = 0;
    this.el.mic && this.el.mic.classList.remove('rec');
    this.teardownAudio();
  };

  /* ---------- 对外入口 ---------- */
  window.VoiceRecorder = {
    mount: function (root) {
      if (!root || !root.querySelector('[data-vt-mic]')) return null;
      var vt = new VoiceTask(root);
      vt.init();
      return vt;
    }
  };
})();
