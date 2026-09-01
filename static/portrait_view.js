/* =====================================================================
   采集完成度清单 + 五维画像门控 —— 接入 /portrait/* （2026-08-13 新增）

   为什么需要这一层
   ---------------------------------------------------------------------
   五维画像需要三份数据同时在手：面部行为、语音任务、量表自评。
   但这三份数据在改造前【不共存】：
     · 面部  metrics_aggregator.FrameBuffer(fps=15, window_sec=60)
             = deque(maxlen=900)，60 秒滚动窗口，更早的帧被淘汰。
     · 语音  audio_client 的 AudioSessionState.stages，本来就是快照式。
     · 量表  DASS21_STATE.answers 是本文件同目录 shell_panels.js 里的
             【模块级浏览器变量】—— 刷新页面 21 题全丢，后端从未见过。
   所以「答完量表再去录音，录完再看画像」这个再自然不过的流程，
   实际上拿不到一份完整数据。后端 portrait_state.py 固化了三份快照，
   本文件负责把前端接上去。

   后端契约（权威定义在 portrait_state.py，本文件不自行发明字段）
   ---------------------------------------------------------------------
   POST /portrait/scale     body {answers:{1..21:0..3}, scored:{...}}
        -> 200 {status, scored, client_agrees, readiness}
        -> 400 {status:"error", message}   校验失败（越界/缺题/重复）
        面部数据【不】由前端上传：前端可篡改，且拿不到 5002 的 60s 聚合值。
   POST /portrait/face      无 body，后端自读 latest_openface
        -> 200 {status, readiness}
        -> 409 {status:"error", message}   摄像头未就绪 / 窗口不足 20s
   GET  /portrait/readiness -> {steps:[{id,label,done,reason,...}×3],
                                ready, blocking:[...],
                                hr, hr_available, policy, formula_status}
   POST /portrait/reset     body {what:"face"|"scale"|"all"}

   两条纪律
   ---------------------------------------------------------------------
   1) 量表得分一律以【后端返回的 scored】回填，不用前端 scoreDASS21 的结果。
      前端 pct = score/42 已实测出错：score=12 同时对应「焦虑 中度」与
      「压力 正常」，两者都显示 29%。后端 level_norm 才是对齐后的严重度。
   2) 三项未全齐时，五维画像保持占位。缺项不做权重重分配 ——
      重分配会让同一个人在不同完成度下得到不同分数，纵向比较失去意义。
      （这条规则的判定唯一地在后端 readiness()，前端只做展示。）
   ===================================================================== */
(function () {
  'use strict';

  var POLL_MS = 4000;      // 完成度轮询间隔。语音/面部由用户操作驱动，不必更快。
  var STEP_ORDER = ['face', 'voice', 'scale'];

  /* 步骤补充说明。后端 reason 讲的是「缺什么」，这里讲「怎么做」——
     只显示原因会让用户知道没完成却不知道该点哪里。 */
  var STEP_HINT = {
    face:  '开启摄像头并正视屏幕 20 秒以上，然后点「固化面部数据」',
    voice: '在右侧「语音任务」完成 5 秒持续元音与固定文本朗读',
    scale: '在右侧完成 DASS-21 全部 21 题'
  };

  function jget(url) {
    return fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        return r.json().catch(function () { return { status: 'error', message: 'HTTP ' + r.status }; })
          .then(function (j) { j.__http = r.status; return j; });
      });
  }

  function jpost(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return { status: 'error', message: 'HTTP ' + r.status }; })
        .then(function (j) { j.__http = r.status; return j; });
    });
  }

  window.PortraitView = {
    mount: function (root) {
      var ckEl   = root.querySelector('[data-ready-list]');
      if (!ckEl) return;                 // 结构变更时静默跳过，不阻断面板

      var chipEl  = root.querySelector('[data-ready-chip]');
      var noteEl  = root.querySelector('[data-ready-note]');
      var faceBtn = root.querySelector('[data-portrait-face]');
      var timer = null, busy = false;

      /* ---------------- 渲染完成度清单 ---------------- */
      function paint(rd) {
        var steps = (rd && rd.steps) || [];
        var byId = {};
        steps.forEach(function (s) { byId[s.id] = s; });

        ckEl.innerHTML = STEP_ORDER.map(function (id) {
          var s = byId[id] || { id: id, label: id, done: false, reason: '状态未知' };
          var cls = s.done ? 'ok' : 'wait';
          /* 未完成时优先显示后端 reason（缺什么），再补 hint（怎么做）。
             已完成时显示采集时刻/窗口长度这类可核对的事实。 */
          var detail;
          if (s.done) {
            if (id === 'face') {
              detail = '窗口 ' + (s.window_sec != null ? Math.round(s.window_sec) + 's' : '—')
                     + (s.captured_at ? ' · ' + fmtTime(s.captured_at) : '');
            } else if (id === 'voice') {
              detail = '已完成 ' + ((s.completed_stages || []).length) + '/'
                     + ((s.required_stages || []).length) + ' 项';
            } else {
              detail = s.submitted_at ? '提交于 ' + fmtTime(s.submitted_at) : '已提交';
            }
          } else {
            detail = (s.reason || '') + (STEP_HINT[id] ? ' · ' + STEP_HINT[id] : '');
          }
          return '<div class="pv3-ck-row ' + cls + '" data-ck-row="' + id + '">'
               +   '<span class="ic">' + (s.done ? '✔' : '○') + '</span>'
               +   '<span class="tx"><b>' + esc(s.label) + '</b>'
               +     '<i>' + esc(detail) + '</i></span>'
               + '</div>';
        }).join('');

        var n = steps.filter(function (s) { return s.done; }).length;
        if (chipEl) {
          chipEl.textContent = n + ' / ' + STEP_ORDER.length + ' 已完成';
          chipEl.className = 'pv3-chip ' + (rd && rd.ready ? 'ok' : 'gray');
        }

        if (noteEl) {
          if (rd && rd.ready) {
            /* 三项齐了也【不】出分：公式本身还没定稿。
               把后端 formula_status 原文显示出来，而不是自己编一句
               「计算中」—— 那会让人以为马上就有分数。 */
            noteEl.textContent = '三项数据已齐备。' + (rd.formula_status || '');
          } else {
            var miss = (rd && rd.blocking) || [];
            noteEl.textContent = miss.length
              ? '尚缺 ' + miss.length + ' 项，五维画像暂不计算（缺项不做权重重分配）'
              : '正在读取采集状态…';
          }
        }

        /* 心率可用性单独提示：它随面部采集一同固化，缺它会让
           放松度/压力值/活力值的生理项失效。"0" 是「未测量」哨兵，
           后端已转成 null —— 若把它当 0 心率算，结果是
           「深度放松、零压力」，是最危险的假阳性方向。 */
        var hrEl = root.querySelector('[data-ready-hr]');
        if (hrEl) {
          if (rd && rd.hr_available) {
            hrEl.textContent = '心率 ' + rd.hr.heart_rate + ' bpm'
              + (rd.hr.respiration_rate_available ? ' · 呼吸 ' + rd.hr.respiration_rate + ' /min' : '');
            hrEl.className = 'rt ok';
          } else {
            hrEl.textContent = '心率未测得';
            hrEl.className = 'rt';
          }
        }

        if (faceBtn) {
          var fs = byId.face || {};
          faceBtn.disabled = busy;
          faceBtn.textContent = busy ? '固化中…' : (fs.done ? '重新固化面部数据' : '固化面部数据');
        }
      }

      function fmtTime(ts) {
        try {
          var d = new Date(ts * 1000);
          return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2)
               + ':' + ('0' + d.getSeconds()).slice(-2);
        } catch (e) { return '—'; }
      }

      function esc(s) {
        return String(s == null ? '' : s)
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }

      /* ---------------- 五维画像渲染（2026-08-13 批次 3b 接线） ----------
         批次 1 只接了完成度条，五维那 5 行一直是静态占位（分数格恒为
         「—」、进度条恒 0%）。批次 3b 把公式实现在后端之后，前端并没有
         人去取 —— 于是出现「三项都已完成、心率也有值，五维却全是 —」。
         这里补上这段接线。

         注意渲染的是后端 /portrait/portrait 的结果，前端【不做任何计算】：
         公式只能有一处实现，两处实现迟早不一致，而这种不一致没有报错。 */
      function paintPortrait(pt) {
        if (!pt) return;
        var rows = root.querySelectorAll('.pv3-p-row[data-dim]');
        var byId = {};
        (pt.dimensions || []).forEach(function (d) { byId[d.id] = d; });

        Array.prototype.forEach.call(rows, function (row) {
          var d = byId[row.getAttribute('data-dim')];
          var fill = row.querySelector('.pv3-p-fill');
          var sc = row.querySelector('[data-dim-score]');
          if (!d || !sc) return;

          if (d.value === null || d.value === undefined) {
            /* 缺项显示「—」并把缺什么写进 title —— 用户能自己看出
               还差哪一步，不必猜。绝不显示 0：0 是一个测量结果，
               而这里根本没测到。 */
            sc.textContent = '—';
            sc.className = 'pv3-p-score dim';
            if (fill) fill.style.width = '0%';
            row.title = (d.missing && d.missing.length)
              ? ('暂缺：' + d.missing.join('；'))
              : '暂无数据';
            return;
          }
          sc.textContent = Math.round(d.value);
          sc.className = 'pv3-p-score' + (d.higher_is_worse ? ' amber' : '');
          if (fill) {
            fill.style.width = Math.max(0, Math.min(100, d.value)) + '%';
            /* 压力值是反向指标，用琥珀色与其余四维区分，与图例
               「反向指标 越低越好」对应。类名必须是 amber ——
               psy_v3.css L232/L248 定义的是 .amber，写 .rev 会
               得到一个存在但无任何规则的类名，即静默的视觉失败。 */
            fill.className = 'pv3-p-fill' + (d.higher_is_worse ? ' amber' : '');
          }
          var tip = [];
          if (d.exploratory) tip.push('探索性指标，不计入综合分');
          if (d.dropped && d.dropped.length) {
            d.dropped.forEach(function (x) { tip.push('已剔除 ' + x.term + '：' + x.why); });
          }
          row.title = tip.join('\n');
        });

        /* 顶部 chip 与图例文案。反映真实状态，不写死。 */
        var chip = root.querySelector('[data-portrait-chip]');
        var fml = root.querySelector('[data-portrait-formula]');
        var comp = pt.composite || {};
        if (chip) {
          if (comp.value !== null && comp.value !== undefined) {
            chip.textContent = '综合 ' + Math.round(comp.value);
            chip.className = 'pv3-chip done';
          } else if (pt.gated) {
            chip.textContent = '待采集';
            chip.className = 'pv3-chip gray';
          } else {
            chip.textContent = '部分可用';
            chip.className = 'pv3-chip gray';
          }
        }
        if (fml) {
          if (comp.value !== null && comp.value !== undefined) {
            fml.textContent = '综合分 = 四维等权（压力取反）· 活力值不计入';
            fml.title = comp.note || '';
          } else if (comp.missing && comp.missing.length) {
            /* 把「为什么还没有综合分」原样显示。之前这里恒写
               「加权公式待定义」，公式定稿后就成了过期信息。 */
            fml.textContent = comp.missing[0];
            fml.title = comp.missing.join('\n');
          }
        }
      }

      /* ---------------- 核心结论栏 ----------------
         接的是后端 narrative（portrait_narrate 生成），前端只渲染，
         不做任何拼装或换算。理由：将来 LLM 版文案与规则版文案会
         走这同一个函数 —— 前端若自己算，两条路就会分叉。

         四个槽位一次填齐（大圆圈数字 / 进度环 / 定性标题 / 标签行）
         + 结论正文。任一槽位单独更新都会造出自相矛盾的界面，
         比如「数字显示 — 但进度环走了 68%」。 */
      var RING_LEN = 263.9;   // 与 shell_panels.js 里的 stroke-dasharray 一致

      function paintConclusion(pt) {
        var numEl  = root.querySelector('[data-score-num]');
        var ringEl = root.querySelector('[data-score-ring]');
        var titEl  = root.querySelector('[data-score-title]');
        var tagsEl = root.querySelector('[data-score-tags]');
        var bodyEl = root.querySelector('[data-conclusion-body]');

        var nar = (pt && pt.narrative) ? pt.narrative : null;
        var comp = (nar && nar.facts) ? nar.facts.composite : null;

        /* 综合分。绝不回落到 0：0 分是一个测量结果，
           而「没算出来」不是。环形进度同步清零。 */
        if (numEl) {
          numEl.textContent = (comp === null || comp === undefined)
            ? '\u2014' : String(comp);
        }
        if (ringEl) {
          var frac = (comp === null || comp === undefined)
            ? 0 : Math.max(0, Math.min(100, comp)) / 100;
          ringEl.setAttribute('stroke-dashoffset',
                              (RING_LEN * (1 - frac)).toFixed(1));
        }

        if (titEl && nar && nar.title) {
          titEl.textContent = nar.title;
          /* 有综合分时摘掉 .pv3-placeholder：该类把文字降级为次要色，
             真实结论不该看起来像占位符。没有综合分时反过来加回去。 */
          titEl.className = (comp === null || comp === undefined)
            ? 'title pv3-placeholder' : 'title';
        }

        if (tagsEl && nar && nar.tags) {
          tagsEl.innerHTML = '';
          nar.tags.forEach(function (t) {
            var sp = document.createElement('span');
            /* kind 只允许 CSS 里真实存在的类（gray/calm/无）。
               后端若给了别的值就退到默认样式，而不是套一个
               不存在的类名 —— 那样不报错但也没有样式。 */
            var kind = (t.kind === 'gray' || t.kind === 'calm')
              ? ' ' + t.kind : '';
            sp.className = 'pv3-tag' + kind;
            sp.textContent = t.text;
            tagsEl.appendChild(sp);
          });
        }

        if (bodyEl && nar && nar.body) {
          bodyEl.textContent = nar.body;
          /* 规则文案是真实的客观描述，不是占位；但它仍然只是
             「指标转述」，所以样式上不做高亮升级，仅摘掉占位色。 */
          bodyEl.className = '';
        }
      }

      function refresh() {
        /* 取 /portrait/portrait 而不是 /portrait/readiness：
           前者已内含 readiness，一次请求同时驱动完成度条与五维，
           省一次往返，也避免两次请求之间状态不一致。 */
        return jget('/portrait/portrait').then(function (pt) {
          var rd = (pt && pt.readiness) ? pt.readiness : pt;
          if (pt && pt.__http === 400) { rd = pt; }
          if (rd.__http === 400) {
            /* 无会话：不是错误，是还没进检测流程。 */
            ckEl.innerHTML = '<div class="pv3-ck-empty">会话未建立，请先进入检测页开启采集</div>';
            if (chipEl) { chipEl.textContent = '会话未建立'; chipEl.className = 'pv3-chip gray'; }
            return null;
          }
          paint(rd);
          paintPortrait(pt);
          paintConclusion(pt);
          return rd;
        }).catch(function (e) {
          if (noteEl) noteEl.textContent = '完成度读取失败：' + (e && e.message ? e.message : e);
          return null;
        });
      }

      /* ---------------- 固化面部数据 ---------------- */
      if (faceBtn) {
        faceBtn.addEventListener('click', function () {
          if (busy) return;
          busy = true;
          if (faceBtn) faceBtn.textContent = '固化中…';
          jpost('/portrait/face', {}).then(function (j) {
            busy = false;
            if (j.status !== 'ok') {
              /* 409 = 摄像头未就绪 / 窗口不足 20s。把后端原话显示出来，
                 它已经写明了具体是哪一项不满足。 */
              if (noteEl) noteEl.textContent = '固化失败：' + (j.message || '未知原因');
              refresh();
              return;
            }
            paint(j.readiness);
            /* 固化成功会让面部三维立刻可算，必须重取一次五维，
               否则要等到下一个 4s 轮询才更新。 */
            refresh();
          }).catch(function (e) {
            busy = false;
            if (noteEl) noteEl.textContent = '固化失败：' + (e && e.message ? e.message : e);
          });
        });
      }

      /* ---------------- 暴露给 shell_panels.js 的钩子 ----------------
         DASS 答满 21 题时由 shell_panels.js 调用。放在这里而不是让
         shell_panels.js 自己 fetch：网络与契约集中在本文件，
         shell_panels.js 只负责它本来的职责（渲染与作答交互）。 */
      window.PortraitView._submitScale = function (answers, scored) {
        return jpost('/portrait/scale', { answers: answers, scored: scored })
          .then(function (j) {
            if (j.status === 'ok') { paint(j.readiness); }
            else if (noteEl) { noteEl.textContent = '量表提交失败：' + (j.message || ''); }
            return j;
          }).catch(function (e) {
            if (noteEl) noteEl.textContent = '量表提交失败：' + (e && e.message ? e.message : e);
            return { status: 'error', message: String(e) };
          });
      };

      window.PortraitView._resetScale = function () {
        return jpost('/portrait/reset', { what: 'scale' }).then(function (j) {
          if (j && j.readiness) paint(j.readiness);
          return j;
        }).catch(function () { return null; });
      };

      window.PortraitView._refresh = refresh;

      /* ---------------- 轮询 ----------------
         面板可被反复打开（外壳每次重建 DOM），旧定时器必须停掉，
         否则会累积多个 interval 同时打同一个接口。 */
      if (window.PortraitView.__timer) clearInterval(window.PortraitView.__timer);
      refresh();
      timer = setInterval(function () {
        if (!document.body.contains(ckEl)) {       // 面板已关闭
          clearInterval(timer);
          window.PortraitView.__timer = null;
          return;
        }
        refresh();
      }, POLL_MS);
      window.PortraitView.__timer = timer;
    }
  };
})();
