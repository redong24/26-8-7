/* =====================================================================
   情绪构成 / 主导情绪 —— 接入真实 /get_openface
   （2026-08-12 新增；原实现是设计稿写死的 6 条示例百分比）

   后端契约（权威定义在 openface_service/openface_parser.py +
   flask_openface_patch.py，本文件不再自行发明字段）
   ---------------------------------------------------------------------
   GET /get_openface   （需 session cookie，无会话返回 400）

   三种「没有数据」的形态，必须分别处理，且都不能显示百分比：
     1) {status:"idle",  message:"openface not called yet"}
        → 还没开始采集（用户没开摄像头 / 没进检测页）
     2) {status:"error", message:...}
        → 微服务调用失败
     3) HTTP 400 {status:"error", message:"Invalid session"}
        → 会话失效
     4) face_count === 0
        → 有会话有响应，但这一帧没检测到人脸。此时 emo_distribution
          可能是上一帧残留或全 0，把它当作「当前情绪」展示是错的。

   有数据时的字段：
     emo_distribution : 固定 8 项数组，每项 {index,label,label_cn,icon,prob}
                        prob 是 0~1 的概率（已做 EMA 时间平滑）
                        ⚠ 后端注释明确「按固定顺序排列（不排序），保证前端
                          每次渲染时每个类别的横条位置固定」——所以本文件
                          绝不能再按概率排序，否则横条会逐帧跳动。
     emotion          : 平滑后的主导情绪中文名
     top_emotion      : 平滑后的主导情绪英文 label
     emo_prob         : 主导情绪概率（0~1）
     emo_dominant_duration_sec : 该主导情绪已持续多久（秒）
     emo_stability    : 情绪稳定度
     face_count       : 检测到的人脸数

   轮询频率
   ---------------------------------------------------------------------
   取 2000ms。参考 _preview 里记录的性能事故复盘：openface 微服务曾因
   每请求开新线程被轮询压垮。情绪构成是「近 60s 加权」的统计量，本身
   变化很慢，没有必要高频拉取；并且加 inFlight 防重入，避免上一次没
   回来又发下一个请求造成堆积。

   只暴露 window.EmotionView.mount(root)。
   ===================================================================== */
(function () {
  'use strict';

  var POLL_MS = 2000;

  /* 后端 EMO_LABELS 的顺序 —— 仅用于「拿不到 emo_distribution 时」
     渲染 8 条占位行，保证卡片高度稳定、不会突然塌陷。
     有真实数据时一律以后端返回的数组顺序为准。 */
  var FALLBACK = [
    { label: 'neutral',  label_cn: '平静' },
    { label: 'happy',    label_cn: '愉快' },
    { label: 'sad',      label_cn: '悲伤' },
    { label: 'surprise', label_cn: '惊讶' },
    { label: 'fear',     label_cn: '恐惧' },
    { label: 'disgust',  label_cn: '厌恶' },
    { label: 'angry',    label_cn: '愤怒' },
    { label: 'contempt', label_cn: '轻蔑' }
  ];

  /* label → CSS 类。calm/happy/sad/surprise/anger/fear 是既有配色；
     disgust/contempt 为本次新增（后端有 8 类，原前端只画了 6 类）。 */
  var FILL = {
    neutral: 'calm', happy: 'happy', sad: 'sad', surprise: 'surprise',
    fear: 'fear', disgust: 'disgust', angry: 'anger', contempt: 'contempt'
  };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* 百分比渲染：null/undefined/非有限 一律占位符。
     绝不退化成 0% —— 「没测到」与「概率为零」是两件事，后者会作为
     一个真实数据点被阅读。 */
  function pct(p) {
    if (p === null || p === undefined) return null;
    var v = Number(p);
    if (!isFinite(v)) return null;
    if (v < 0) v = 0;
    if (v > 1) v = 1;          // 后端给的是 0~1 概率
    return v * 100;
  }

  function mmss(sec) {
    if (sec === null || sec === undefined) return '—';
    var v = Number(sec);
    if (!isFinite(v) || v < 0) return '—';
    var m = Math.floor(v / 60), s = Math.floor(v % 60);
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }

  /* ------------------------------------------------------------------ */

  function EmotionView(root) {
    this.root = root;
    this.listEl = root.querySelector('[data-emo-list]');
    this.domEl = root.querySelector('[data-emo-dominant]');
    this.chipEl = root.querySelector('[data-emo-chip]');
    this.timer = null;
    this.inFlight = false;
    this.stopped = false;
  }

  /* 8 条占位行：没有数据时也保持卡片结构与高度，避免面板忽高忽低。
     条形宽度为 0，数值列显示「—」。 */
  EmotionView.prototype.renderEmpty = function (note) {
    if (!this.listEl) return;
    var html = FALLBACK.map(function (e) {
      return '<div class="pv3-e-row">'
        + '<div class="pv3-e-name dim">' + esc(e.label_cn) + '</div>'
        + '<div class="pv3-e-bar"><div class="pv3-e-fill ' + FILL[e.label]
        + '" style="width:0%;"></div></div>'
        + '<div class="pv3-e-pct dim">—</div></div>';
    }).join('');
    html += '<div class="pv3-e-empty">' + esc(note) + '</div>';
    this.listEl.innerHTML = html;

    if (this.domEl) {
      this.domEl.innerHTML =
        '<div class="pv3-de-label">主导情绪</div>'
        + '<div class="pv3-de-row"><span class="pv3-de-name">—</span>'
        + '<span class="pv3-de-pct">—</span></div>'
        + '<div class="pv3-de-meta"><span>置信度<b>—</b></span>'
        + '<span>持续<b>—</b></span></div>';
    }
    if (this.chipEl) this.chipEl.textContent = '待采集';
  };

  EmotionView.prototype.renderData = function (d) {
    var dist = d.emo_distribution;
    if (!dist || !dist.length) {
      // 有会话、有响应，但没有分布字段：属于后端契约异常，如实说明，
      // 不要用 fallback 顺序凑出一组 0% 假装「测到了都是 0」
      this.renderEmpty('未获取到情绪分布数据');
      return;
    }

    if (this.listEl) {
      // 严格按后端给的顺序遍历（后端保证固定顺序），不排序
      this.listEl.innerHTML = dist.map(function (e) {
        var p = pct(e.prob);
        var cls = FILL[e.label] || 'calm';
        // 主导项高亮：概率最高的那条不加 dim
        var dim = (p === null || p < 10) ? ' dim' : '';
        return '<div class="pv3-e-row">'
          + '<div class="pv3-e-name' + dim + '">'
          + esc(e.label_cn || e.label) + '</div>'
          + '<div class="pv3-e-bar"><div class="pv3-e-fill ' + cls
          + '" style="width:' + (p === null ? 0 : p.toFixed(1)) + '%;"></div></div>'
          + '<div class="pv3-e-pct' + dim + '">'
          + (p === null ? '—' : p.toFixed(0) + '%') + '</div></div>';
      }).join('');
    }

    if (this.domEl) {
      var dp = pct(d.emo_prob);
      var st = pct(d.emo_stability);
      this.domEl.innerHTML =
        '<div class="pv3-de-label">主导情绪</div>'
        + '<div class="pv3-de-row">'
        + '<span class="pv3-de-name">' + esc(d.emotion || '—') + '</span>'
        + '<span class="pv3-de-pct">'
        + (dp === null ? '—' : dp.toFixed(0) + '%') + '</span></div>'
        + '<div class="pv3-de-meta">'
        + '<span>稳定度<b>' + (st === null ? '—' : st.toFixed(0) + '%') + '</b></span>'
        + '<span>持续<b>' + mmss(d.emo_dominant_duration_sec) + '</b></span>'
        + '</div>';
    }
    if (this.chipEl) this.chipEl.textContent = '实时 · 已平滑';
  };

  EmotionView.prototype.tick = function () {
    var self = this;
    if (this.inFlight || this.stopped) return;
    this.inFlight = true;

    fetch('/get_openface', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (res) {
        // 无会话时后端返回 400 —— 这是可预期状态，不能当网络错误处理，
        // 否则「没开摄像头」会被显示成「读取失败」
        if (!res.ok) {
          if (res.status === 400) {
            self.renderEmpty('会话未建立：请先进入检测页开启摄像头');
            return null;
          }
          throw new Error('HTTP ' + res.status);
        }
        return res.json();
      })
      .then(function (d) {
        if (!d || self.stopped) return;

        if (d.status === 'idle') {
          self.renderEmpty('尚未开始采集：请在检测页开启摄像头');
          return;
        }
        if (d.status === 'error') {
          self.renderEmpty('面部分析暂不可用：' + (d.message || '未说明原因'));
          return;
        }
        // 有响应但这一帧没人脸：分布可能是上一帧残留，不能当当前情绪展示
        if (d.face_count === 0) {
          self.renderEmpty('未检测到人脸：请正对摄像头并确保光照充足');
          return;
        }
        self.renderData(d);
      })
      .catch(function (e) {
        if (self.stopped) return;
        self.renderEmpty('读取面部数据失败：' + (e && e.message ? e.message : e));
      })
      .then(function () { self.inFlight = false; });
  };

  EmotionView.prototype.start = function () {
    var self = this;
    this.renderEmpty('正在读取…');
    this.tick();
    this.timer = setInterval(function () { self.tick(); }, POLL_MS);
  };

  EmotionView.prototype.teardown = function () {
    this.stopped = true;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  };

  /* ------------------------------------------------------------------ */

  window.EmotionView = {
    mount: function (root) {
      if (!root) return;
      var view = new EmotionView(root);
      if (!view.listEl && !view.domEl) return;   // 该面板没有情绪卡

      var onClose = function () {
        view.teardown();
        document.removeEventListener('psy-panel-close', onClose);
      };
      // 面板关闭/切换后必须停轮询：否则会一直打后端并保持 session 活跃
      document.addEventListener('psy-panel-close', onClose);

      view.start();
      return view;
    }
  };
})();
