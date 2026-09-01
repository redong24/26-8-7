/* =============================================================================
 * ai_view.js —— AI 综合解读面板的前端接入
 * =============================================================================
 * 负责三件事：
 *   1) 探测凭证是否就绪（/portrait/ai_status），决定 CTA 能否点
 *   2) 轮询 /portrait/readiness，三项采集齐备后【自动】生成一次解读
 *   3) 点击 CTA 手动生成 / 重新生成
 *
 * ---------------------------------------------------------------------------
 * 2026-08-21 修订：由 shell_panels.js 的 mount() 驱动，并改为自动生成
 * ---------------------------------------------------------------------------
 * 修掉的 bug：CTA 永远点不动。
 *   外壳 openPopup() 每次打开面板都 remove() 旧 DOM 再 buildPopup() 建全新
 *   DOM。而旧版本这里只在页面 load 时 boot() 一次、轮询 30 秒就放弃，
 *   于是：
 *     * 用户在 30 秒内没打开面板 → 轮询超时退出，此后永不再探，
 *       CTA 停在初始的 .disabled 上，点了毫无反应；
 *     * 即便侥幸绑上了，下次打开面板 DOM 已被换掉，绑定随旧节点一起丢失。
 *   其余四个视图模块（VoiceRecorder / EmotionView / BehaviorView /
 *   PortraitView）都由 mount() 显式驱动，唯独本模块漏接 —— 这才是根因。
 *   现在暴露 window.AiView.mount(root)，与其它模块保持一致。
 *
 * 三条原有设计约束继续保留：
 *   * 失败一律回落到既有占位文案，绝不显示假内容。这块面板的历史问题
 *     恰恰是"硬编码假结论排版成真结果"，不能再犯第二次。
 *   * 不缓存到 localStorage。解读对应的是"本次采集"，跨会话复用等于
 *     把上次的数据当成这次的。
 *   * 纯 ES5 + 原生 DOM，与 portrait_view.js / emotion_view.js 保持一致，
 *     不引入任何构建步骤或框架。
 * ========================================================================== */
(function () {
  'use strict';

  /* 与 portrait_view.js 的完成度轮询同频。
     这个请求很轻（只读内存态、不调模型），4s 一次不构成负担。 */
  var POLL_MS = 4000;

  /* 自动生成的去重键：记录"已为哪一份采集数据生成过"。
     放在模块级而不是 mount 作用域 —— 面板反复开关会重建 DOM，
     但"这次采集已经生成过了"这件事必须跨重建存活，
     否则用户每开一次面板就重新烧一次模型调用的钱。 */
  var autoDoneKey = null;
  /* 自动生成失败过的键：失败不重试同一份数据。
     否则采集齐备后每 4s 重试一次，会把额度打光（模型调用是计费的）。
     用户仍可手动点 CTA 重试 —— 那是明确的人工意图。 */
  var autoFailedKey = null;

  function $(sel, root) { return (root || document).querySelector(sel); }

  /* 统一的 fetch：带超时，避免请求悬挂导致按钮永远停在"生成中"。
     AbortController 在目标浏览器（Chrome/Edge 现代版）均可用。 */
  function req(url, opts, timeoutMs) {
    opts = opts || {};
    var ctl = null;
    if (typeof AbortController !== 'undefined') {
      ctl = new AbortController();
      opts.signal = ctl.signal;
    }
    var timer = setTimeout(function () {
      if (ctl) { try { ctl.abort(); } catch (e) {} }
    }, timeoutMs || 120000);
    return fetch(url, opts).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }, function (e) {
      clearTimeout(timer);
      throw e;
    });
  }

  /* ---------------------------------------------------------------- 渲染 */

  /* 保存初始占位 HTML。失败回落时原样还原 ——
     与其自己再拼一段"暂不可用"，不如复用那段已经审过措辞的文案。
     注意按 DOM 实例保存：面板每次重建都是新节点，
     存成模块级单例会在第二次打开时还原成上一轮被替换后的内容。 */
  function bodyEl(root) { return $('[data-ai-body]', root); }
  function ctaEl(root) { return $('[data-ai-cta]', root); }

  function placeholderOf(b) {
    if (b && b.__aiPlaceholder === undefined) {
      b.__aiPlaceholder = b.innerHTML;
    }
    return b ? b.__aiPlaceholder : '';
  }

  function setCta(root, state, label) {
    var el = ctaEl(root);
    if (!el) return;
    var lb = el.querySelector('.lb');
    if (lb && label) lb.textContent = label;
    if (state === 'disabled') {
      el.classList.add('disabled');
      el.setAttribute('aria-disabled', 'true');
    } else {
      el.classList.remove('disabled');
      el.setAttribute('aria-disabled', 'false');
    }
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function renderLoading(root) {
    var b = bodyEl(root);
    if (!b) return;
    placeholderOf(b);
    b.innerHTML = '<p class="pv3-placeholder">正在生成解读…（首次约需 5-10 秒）</p>';
  }

  /* 失败：还原占位，并把原因作为补充说明附在后面。
     刻意显示具体原因而不是笼统的"生成失败" ——
     "未配置模型 API 凭证"和"采集数据不足"要采取的行动完全不同。 */
  function renderFallback(root, reason) {
    var b = bodyEl(root);
    if (!b) return;
    b.innerHTML = placeholderOf(b) || '';
    if (reason) {
      var p = document.createElement('p');
      p.className = 'pv3-placeholder';
      p.textContent = '（本次未生成：' + reason + '）';
      b.appendChild(p);
    }
  }

  function renderResult(root, d) {
    var b = bodyEl(root);
    if (!b) return;
    placeholderOf(b);
    var html = '';
    if (d.summary) {
      html += '<p>' + esc(d.summary) + '</p>';
    }
    if (d.points && d.points.length) {
      html += '<ul class="pv3-ai-points">';
      for (var i = 0; i < d.points.length; i++) {
        html += '<li>' + esc(d.points[i]) + '</li>';
      }
      html += '</ul>';
    }
    if (d.caveat) {
      /* caveat 用占位样式（次要色）：它是局限说明，
         视觉权重必须低于正文，但又不能省 —— 省了就等于把
         "数据有限"这件事藏起来。 */
      html += '<p class="pv3-placeholder">' + esc(d.caveat) + '</p>';
    }
    /* 署名行：让用户知道这段话是模型生成的，不是系统测出来的。
       这不是装饰，是必要的信息披露。 */
    var meta = [];
    meta.push('由 ' + esc(d.model || '模型') + ' 生成');
    if (d.measured) meta.push('基于 ' + d.measured + ' 项已采集指标');
    if (d.auto) meta.push('采集齐备后自动生成');
    if (d.cached) meta.push('复用缓存');
    else if (d.elapsed_ms) meta.push((d.elapsed_ms / 1000).toFixed(1) + 's');
    html += '<p class="pv3-placeholder pv3-ai-meta">' + meta.join(' · ') + '</p>';
    b.innerHTML = html;
  }

  /* ---------------------------------------------------------------- 交互 */

  /* busy 是模块级的：自动生成与手动点击共用一把闸，
     避免"采集刚齐备触发自动生成"与"用户同时点了按钮"打两次模型。 */
  var busy = false;

  function generate(root, opts) {
    opts = opts || {};
    if (busy) return Promise.resolve(null);
    var el = ctaEl(root);
    /* 手动点击要尊重 disabled（凭证没配时不该发请求）；
       自动触发则跳过这层检查 —— 它的前置条件是 readiness.ready，
       而 CTA 的 disabled 态可能只是还没探测完。 */
    if (!opts.auto && el && el.classList.contains('disabled')) {
      return Promise.resolve(null);
    }
    busy = true;
    setCta(root, 'disabled', '生成中…');
    renderLoading(root);

    /* 统一在这里落地"成功 / 失败"两种终态，并解除 busy。
       刻意不用 throw 串联：调用方（自动触发）只需要知道成不成，
       用 resolve(null) 表示失败比 reject 更好写，也不会漏掉
       unhandled rejection。
       后端超时 90s，前端给 120s 留出网络与排队余量。 */
    return req('/portrait/ai_summary', { method: 'POST' }, 120000)
      .then(function (d) {
        if (d && d.status === 'ok') {
          if (opts.auto) d.auto = true;
          renderResult(root, d);
          setCta(root, 'enabled', '重新生成');
          return d;
        }
        /* status=unavailable：后端把原因写在 reason 里
           （凭证缺失 / 采集不足 / 模型超时），原文透出。 */
        renderFallback(root, (d && d.reason) || '未知原因');
        setCta(root, 'enabled', '重试');
        return null;
      })
      .catch(function (e) {
        var msg = (e && e.name === 'AbortError')
          ? '请求超时' : ('请求失败 ' + (e && e.message ? e.message : ''));
        renderFallback(root, msg);
        setCta(root, 'enabled', '重试');
        return null;
      })
      .then(function (d) { busy = false; return d; });
  }

  /* ------------------------------------------------- 自动生成的闸门判定 */

  /* 用哪些字段做"这份采集是否已变化"的指纹：
     三项完成状态 + 各自的时间戳。时间戳变化意味着重新采集过，
     应当重新生成解读；只看 ready 布尔值会漏掉"重测一次"的情况。 */
  function readyKey(rd) {
    if (!rd || !rd.ready) return null;
    var parts = [];
    var steps = rd.steps || [];
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];
      parts.push(s.id + ':' + (s.captured_at || s.submitted_at ||
                 (s.completed_stages || []).join(',') || '1'));
    }
    return parts.join('|');
  }

  function maybeAuto(root, rd) {
    var key = readyKey(rd);
    if (!key) return;                        // 尚未齐备
    if (key === autoDoneKey) return;         // 这份数据已生成过
    if (key === autoFailedKey) return;       // 这份数据失败过，不刷额度
    if (busy) return;

    autoDoneKey = key;                       // 先占位，防并发重入
    generate(root, { auto: true }).then(function (d) {
      if (d) return;                         // 成功，doneKey 保持
      /* 失败：记进 failed 并释放 doneKey。
         这样不会 4s 一次地重试把额度打光，
         但用户手动点 CTA 仍可重来（那是明确的人工意图）。 */
      autoFailedKey = key;
      autoDoneKey = null;
    });
  }

  /* ---------------------------------------------------------------- 挂载 */

  function mount(root) {
    root = root || document;
    var b = bodyEl(root);
    if (!b) return;                          // 该面板没有 AI 卡片
    placeholderOf(b);                        // 记下本次 DOM 的占位原文

    /* 最近一次 readiness。声明在绑定之前：点击回调要用它更新
       autoDoneKey，避免"手动生成完，轮询又自动生成一次"。 */
    var lastRd = null;

    var el = ctaEl(root);
    if (el && !el.__aiBound) {
      el.__aiBound = true;
      el.addEventListener('click', function () {
        /* 手动点击是明确的人工意图：清掉失败记忆，允许重来。 */
        autoFailedKey = null;
        generate(root, { auto: false }).then(function (d) {
          /* 手动生成成功后，把当前采集指纹记为已生成，
             否则下一次 4s 轮询会认为"还没生成过"而再打一次模型。 */
          if (d) {
            var k = readyKey(lastRd);
            if (k) autoDoneKey = k;
          }
        });
      });
      el.style.cursor = 'pointer';
    }

    /* ---------------- 报告单入口（2026-08-21）----------------
       与「生成解读」共用 mount 生命周期：外壳每次开面板都会重建 DOM，
       所以绑定必须在 mount 里做，且用 __rptBound 防重复绑定
       （同一个 DOM 被 mount 两次会导致点一次开两个标签页）。

       刻意【不做】置灰：按拍板结果，数据不齐备也允许出报告，
       报告页自己会显著标注缺什么。这里灰掉会让用户既看不到报告
       也不知道差什么。 */
    var rb = $('[data-rpt-cta]', root);
    if (rb && !rb.__rptBound) {
      rb.__rptBound = true;
      rb.style.cursor = 'pointer';
      rb.addEventListener('click', function () {
        /* 新标签页打开：报告是长文档，占满当前页会把用户从测评流程里
           踢出去，回来还得重新开面板。noopener 是安全惯例。 */
        var w = window.open('/portrait/report', '_blank', 'noopener');
        if (!w) {
          /* 被拦截时必须告知 —— 静默失败会让用户以为按钮坏了。 */
          alert('报告已生成，但浏览器拦截了新标签页。\n请允许本站弹出窗口，或直接访问 /portrait/report');
        }
      });
    }

    /* 探凭证。只探一次：凭证是部署期配置，不会在用户会话中途变化。
       注意 CTA 的可点性只取决于凭证，不取决于采集是否齐备 ——
       未齐备时点击，后端会回"尚无任何已采集指标"，
       这个反馈比一个灰按钮更能说明问题。 */
    req('/portrait/ai_status', {}, 15000)
      .then(function (d) {
        if (d && d.available) {
          setCta(root, 'enabled', '生成解读');
        } else {
          setCta(root, 'disabled', '生成解读（未配置）');
          renderFallback(root, d && d.reason ? d.reason : null);
        }
      })
      .catch(function () {
        setCta(root, 'disabled', '生成解读（不可用）');
      });

    /* ---------------- 完成度轮询，驱动自动生成 ----------------
       面板可被反复打开（外壳每次重建 DOM），旧定时器必须停掉，
       否则会累积多个 interval 同时打同一个接口。
       与 portrait_view.js 同一范式。 */
    function tick() {
      if (!document.body.contains(b)) {      // 面板已关闭
        if (window.AiView.__timer) {
          clearInterval(window.AiView.__timer);
          window.AiView.__timer = null;
        }
        return;
      }
      req('/portrait/readiness', {}, 10000).then(function (rd) {
        lastRd = rd;
        maybeAuto(root, rd);
      }).catch(function () { /* 无会话/网络抖动：静默，下轮再试 */ });
    }

    if (window.AiView.__timer) clearInterval(window.AiView.__timer);
    tick();
    window.AiView.__timer = setInterval(tick, POLL_MS);
  }

  window.AiView = {
    mount: mount,
    /* 保留旧名，避免外部若有引用而断掉 */
    generate: function () { return generate(document, { auto: false }); }
  };
  /* 兼容旧的全局名（此前对外暴露的是 HikoAiView） */
  window.HikoAiView = window.AiView;
})();
