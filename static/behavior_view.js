/* ==============================================================================
 * behavior_view.js —— 「注意与表情行为」实时渲染
 * 2026-08-13 新建。替代原「情绪时间线」（那是一条写死坐标的 SVG 曲线，无数据源）。
 *
 * 本模块把首页的「头姿 & 视线」与「面部表情 & Action Units」两卡融合进
 * 心理综合评估面板，数据源为 /get_openface。
 *
 * ---- 只显示真实字段（这是本文件最重要的约束）----
 * 采用：
 *   pose.yaw / pose.pitch   MLT 模型 gaze 回归头输出（度）
 *   gaze_state              由 yaw/pitch 按 ±15° 判定的可读状态
 *   gaze_stability          60s 窗口 exp(-(var_yaw+var_pitch)/200)，0~1
 *   pose_deviation_60s      60s 内"偏移"帧占比，0~1
 *   au_intensity            8 维 AU 强度（AU01/02/04/06/09/12/25/26）
 *   au_dominant             最强 AU 的中文名
 *
 * 明确【不采用】的字段，及其原因（已逐个查源码核实）：
 *   blink_rate / perclos / ear_l / ear_r
 *     flask_openface_patch.py 中 ear 恒为常量 0.30（L54/123/172 三处），
 *     而 PERCLOS 判据是 ear<0.20 —— 0.30>0.20 恒真，于是 PERCLOS 恒为
 *     0.000、眨眼恒为 0.0/min、EAR 恒为 0.30/0.30。模型本身没有 AU45，
 *     也没有眼睑关键点，算不出真眨眼。显示它们等于显示一个永不变化的假值。
 *   pose.roll        源码注释原文："OpenFace 3.0 目前 CSV 里没 roll，占位 0"
 *   au_symmetry      硬编码 0.96
 *   au_activity      判据写成 max(AU)>1.0，但 AU 回归头输出上限约 0.6，恒为 0%
 *
 * 失败模式优先级：宁可显示"—"，也不显示一个看起来合理的数字。
 * 用户无法分辨"真实的 0.00"和"坏掉的 0.00"，所以坏掉的必须缺席而不是归零。
 * ============================================================================== */
(function () {
  'use strict';

  var POLL_MS = 2000;      // 与 emotion_view 一致，避免两个模块各自节奏打架

  /* ============================ 零点校准 ============================
     2026-08-13：录屏逐帧实测暴露两个真实缺陷。

     【缺陷 1：模型 gaze 回归头有系统性偏置】
     实测：人正对镜头时 yaw = -11.0°（真值应为 0°）；
     右转头 -4.7°，左转头 -14.6°——方向正确但整体向负偏。
     模型输出是无激活的裸回归值，上游未做任何标定，
     后端 _rad_to_deg 也只是拿 abs(v)<=3.2 猜它是弧度再乘 57.3。
     后果：光点永远被钉在左半区，从不过中线。

     【缺陷 2：真实动态范围远小于 ±30°】
     实测从“明显右转”到“左转”全程仅约 10°跨度，
     而原满量程沏用首页的 ±30°，等于只用了罗盘 ~16% 宽度，
     光点看上去几乎不动。

     【修复】会话内自动估计基线并减去，同时收紧满量程。
     基线用中位数而非均值：均值会被“采样期间持续偏头”带跑，
     中位数对离群值鲁棒。另要求样本离散度足够小，
     否则说明用户一直在大幅转头，此时不能把任意姿态当成“正视”。
     ========================================================================= */
  var CAL_N        = 12;   // 校准窗口样本数（2s/次 → 约 24s）
  var CAL_MIN      = 5;    // 至少凑齐这么多样本才敢减基线
  var CAL_MAX_SPAN = 25;   // 样本极差超过此值认为用户在大幅转头，不做校准
  var YAW_CAP = 15;        // 收紧至 ±15°，与后端 gaze_state 的 ±15° 判定同源，
                           // 避免“后端说正视 / 前端画偏左”自相矛盾
  /* 罗盘可用半径不写死：CSS 在 max-height 断点里会缩小罗盘，
     若此处固定 34px，小屏下圆点会被推到圆外。
     改为每次渲染从元素实际尺寸推导，使 JS 与 CSS 解耦。 */
  var R_RATIO  = 0.37;     // 可用半径 / 罗盘直径（34/92，留边避免圆点贴边）
  var R_FALLBACK = 34;     // 元素未布局（面板隐藏）时的退路值

  function dotRadius(el) {
    // offsetWidth 在面板 display:none 时为 0，此时用退路值，不让圆点塔陷到圆心。
    var host = el && el.parentNode;
    var w = host && host.offsetWidth ? host.offsetWidth : 0;
    return w > 0 ? w * R_RATIO : R_FALLBACK;
  }

  function median(arr) {
    if (!arr.length) return null;
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  function fmtDeg(v) {
    return (typeof v === 'number' && isFinite(v)) ? (v.toFixed(1) + '\u00B0') : '\u2014';
  }
  function fmtPct(v) {
    return (typeof v === 'number' && isFinite(v)) ? (Math.round(v * 100) + '%') : '\u2014';
  }
  function fmt2(v) {
    return (typeof v === 'number' && isFinite(v)) ? v.toFixed(2) : '\u2014';
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* 稳定性 0~1 → 文案 + 色调。
     只在拿到数值时给结论；拿不到就不猜。 */
  function stabTone(s) {
    if (typeof s !== 'number' || !isFinite(s)) return { t: '\u2014', c: '' };
    if (s >= 0.75) return { t: s.toFixed(2) + ' \u00B7 稳定', c: 'good' };
    if (s >= 0.45) return { t: s.toFixed(2) + ' \u00B7 一般', c: 'mid' };
    return { t: s.toFixed(2) + ' \u00B7 游移', c: 'bad' };
  }

  function BehaviorView(root) {
    // 零点校准状态：样本缓冲 + 已确定的基线（null = 尚未校准）
    this.calYaw = [];
    this.calPitch = [];
    this.baseYaw = null;
    this.basePitch = null;

    this.root = root;
    this.timer = null;
    this.el = {
      dot:    root.querySelector('[data-beh-dot]'),
      state:  root.querySelector('[data-beh-state]'),
      yaw:    root.querySelector('[data-beh-yaw]'),
      pitch:  root.querySelector('[data-beh-pitch]'),
      stab:   root.querySelector('[data-beh-stab]'),
      dev:    root.querySelector('[data-beh-dev]'),
      audom:  root.querySelector('[data-beh-audom]'),
      aulist: root.querySelector('[data-beh-aulist]'),
      win:    root.querySelector('[data-beh-win]'),
      note:   root.querySelector('[data-beh-note]')
    };
    this.rows = this.el.aulist
      ? Array.prototype.slice.call(this.el.aulist.querySelectorAll('[data-au-code]'))
      : [];
  }

  /* 无人脸 / 无会话 / 请求失败 —— 全部走这里。
     关键：把数值区清成"—"，不保留上一帧的旧值。
     否则人离开画面后，页面会继续显示最后那一帧，看起来像"仍在检测"。 */
  BehaviorView.prototype.blank = function (msg) {
    // 断流/换人后旧基线不再成立，必须重估，否则会把上一个人的头位当成新人的正视。
    this.calYaw = [];
    this.calPitch = [];
    this.baseYaw = null;
    this.basePitch = null;
    var e = this.el;
    if (e.state) { e.state.textContent = msg || '\u7B49\u5F85\u68C0\u6D4B\u2026'; e.state.className = 'st'; }
    if (e.yaw)   e.yaw.textContent   = '\u2014';
    if (e.pitch) e.pitch.textContent = '\u2014';
    if (e.stab)  { e.stab.textContent = '\u2014'; e.stab.className = 'v'; }
    if (e.dev)   e.dev.textContent   = '\u2014';
    if (e.audom) e.audom.textContent = '\u2014';
    if (e.dot)   e.dot.style.transform = 'translate(-50%,-50%)';
    this.rows.forEach(function (row) {
      var fill = row.querySelector('[data-au-fill]');
      var val  = row.querySelector('[data-au-val]');
      if (fill) fill.style.width = '0%';
      if (val)  val.textContent = '\u2014';
    });
  };

  BehaviorView.prototype.render = function (d) {
    var e = this.el;
    var pose = d.pose || {};
    var yaw   = (typeof pose.yaw   === 'number') ? pose.yaw   : null;
    var pitch = (typeof pose.pitch === 'number') ? pose.pitch : null;

    /* 没有 yaw/pitch 就等于这一帧没有可用的视线数据。
       此时不能把圆点留在中心 —— 中心的含义是"正视"，是一个具体结论。 */
    if (yaw === null || pitch === null) {
      this.blank('\u672A\u68C0\u6D4B\u5230\u4EBA\u8138');
      return;
    }

    if (e.state) {
      var st = d.gaze_state || '\u2014';
      e.state.textContent = st;
      e.state.className = 'st' + (st.indexOf('\u6B63\u89C6') >= 0 ? ' ok' : (st.indexOf('\u504F\u79FB') >= 0 ? ' warn' : ''));
    }
    var tone = stabTone(d.gaze_stability);
    if (e.stab) { e.stab.textContent = tone.t; e.stab.className = 'v ' + tone.c; }
    if (e.dev)  e.dev.textContent = fmtPct(d.pose_deviation_60s);

    /* ---- 零点校准：累积样本 → 估基线 → 后续帧减去它 ----
       只在未校准时累积；一旦定下就不再漂移，
       否则用户持续看向一侧时基线会慢慢跟过去，偏移永远回零。 */
    if (this.baseYaw === null) {
      this.calYaw.push(yaw);
      this.calPitch.push(pitch);
      if (this.calYaw.length >= CAL_N) {
        var span = Math.max.apply(null, this.calYaw) - Math.min.apply(null, this.calYaw);
        if (span <= CAL_MAX_SPAN) {
          this.baseYaw   = median(this.calYaw);
          this.basePitch = median(this.calPitch);
        } else {
          // 采样期内头部摆幅过大，丢掉最早一半重来，不强行定基线
          this.calYaw   = this.calYaw.slice(-CAL_MIN);
          this.calPitch = this.calPitch.slice(-CAL_MIN);
        }
      }
    }
    // 未完成校准前先用当前已有样本的中位数作临时基线，
    // 避免开头几秒光点先钉在偏位再“跳”回中心。
    /* 校准未完成时的临时基线：用已有样本的中位数。
       注意不能在样本不足时退化为 0——那会让开头几帧把
       未减偏置的原始值（实测约 -11°）直接画成左偏 0.75 半径，
       随后又突然跳回中心。样本不足时宁可用首帧作为粗略基线：
       它至少保证“开场即居中”，且会被后续中位数平滑修正。 */
    var bY = (this.baseYaw   !== null) ? this.baseYaw
           : (this.calYaw.length   ? median(this.calYaw)   : yaw);
    var bP = (this.basePitch !== null) ? this.basePitch
           : (this.calPitch.length ? median(this.calPitch) : pitch);
    var rYaw   = yaw   - bY;   // relative：相对于“该用户的正视”
    var rPitch = pitch - bP;

    /* Yaw/Pitch 文字必须与光点用同一套值。
       若文字用原始值而光点用校准值，会出现
       “写着 -11° 但光点在中心”的新矛盾，
       而消除这类矛盾正是本次修复的目的。 */
    if (e.yaw)   e.yaw.textContent   = fmtDeg(rYaw);
    if (e.pitch) e.pitch.textContent = fmtDeg(rPitch);

    if (e.dot) {
      var R  = dotRadius(e.dot);
      var dx = clamp(rYaw   / YAW_CAP, -1, 1) * R;
      var dy = clamp(rPitch / YAW_CAP, -1, 1) * R;
      e.dot.style.transform = 'translate(calc(-50% + ' + dx.toFixed(1) + 'px), calc(-50% + ' + dy.toFixed(1) + 'px))';
    }

    /* 校准状态提示：校准期内数值会随基线微调，
       不告知的话用户会以为是读数不稳。 */
    if (e.note) {
      e.note.textContent = (this.baseYaw === null)
        ? '正在校准坐姿基线（请自然正对屏幕）… ' + this.calYaw.length + '/' + CAL_N
        : '角度为相对于您正视基线的偏转量；数据由摄像头逐帧解析，非自评';
    }

    if (e.audom) e.audom.textContent = d.au_dominant || '\u2014';

    var ai = d.au_intensity || {};
    this.rows.forEach(function (row) {
      var code = row.getAttribute('data-au-code');
      var cap  = parseFloat(row.getAttribute('data-au-cap')) || 0.3;
      var v;
      if (code === 'BROW') {
        // 眉毛 = AU01(内眉上扬) + AU02(外眉上扬) 均值。
        // 模型只有 8 维 AU，把这两项合并成一行是首页既有做法，保持一致。
        var a1 = ai.AU01, a2 = ai.AU02;
        var xs = [a1, a2].filter(function (x) { return typeof x === 'number' && isFinite(x); });
        v = xs.length ? xs.reduce(function (a, b) { return a + b; }, 0) / xs.length : null;
      } else {
        v = (typeof ai[code] === 'number' && isFinite(ai[code])) ? ai[code] : null;
      }
      var fill = row.querySelector('[data-au-fill]');
      var val  = row.querySelector('[data-au-val]');
      if (v === null) {
        if (fill) fill.style.width = '0%';
        if (val)  val.textContent = '\u2014';
        return;
      }
      // sqrt 映射：AU 绝大多数时间在 0~0.1，线性映射下横条几乎不动。
      var pct = Math.sqrt(clamp(v / cap, 0, 1)) * 100;
      if (fill) fill.style.width = pct.toFixed(1) + '%';
      if (val)  val.textContent = fmt2(v);
    });

    if (e.win) {
      var n = d.frame_count, w = d.window_sec;
      e.win.textContent = (typeof w === 'number' && w > 0)
        ? ('\u7EDF\u8BA1\u7A97 ' + w + 's' + (n ? ' \u00B7 ' + n + ' \u5E27' : ''))
        : '\u2014';
    }
  };

  BehaviorView.prototype.tick = function () {
    var self = this;
    fetch('/get_openface', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (d) {
        if (!d || d.status === 'error') { self.blank('\u4F1A\u8BDD\u672A\u5C31\u7EEA'); return; }
        if (d.status === 'idle')        { self.blank('\u7B49\u5F85\u68C0\u6D4B\u2026'); return; }
        self.render(d);
      })
      .catch(function () {
        // 网络/解析失败同样清空。静默但不保留旧值。
        self.blank('\u6570\u636E\u6682\u4E0D\u53EF\u7528');
      });
  };

  BehaviorView.prototype.start = function () {
    this.tick();
    var self = this;
    this.timer = setInterval(function () { self.tick(); }, POLL_MS);
  };

  BehaviorView.prototype.stop = function () {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  };

  var current = null;

  window.BehaviorView = {
    mount: function (root) {
      if (!root) return;
      // 结构不存在（例如面板改版后本卡被移除）时静默返回，不抛错阻断其它模块。
      if (!root.querySelector('[data-beh-dot]')) return;
      if (current) current.stop();
      current = new BehaviorView(root);
      current.blank();
      current.start();
      return current;
    },
    unmount: function () {
      if (current) { current.stop(); current = null; }
    }
  };
})();
