/* ============================================================
 * UI 工具：toast / modal / confirm / loading / 格式化
 * ============================================================ */
window.UI = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]))

  /** 「只有年龄、没有出生日期」的哨兵生日。必须与后端 lib/db.ts 的常量逐字一致。
   *  HIS 屏幕上不显示出生日期，而 patient.birth_date 是 NOT NULL，
   *  故用一个不可能的真实日期占位，年龄改由 age_years 承载。 */
  const AGE_ONLY_BIRTH_DATE = '0001-01-01'

  /* ------------------------- Toast ------------------------- */
  function toast(message, type = 'info', ms = 3200) {
    const conf = {
      success: ['fa-circle-check', 'bg-emerald-600'],
      error:   ['fa-circle-exclamation', 'bg-red-600'],
      warn:    ['fa-triangle-exclamation', 'bg-amber-500'],
      info:    ['fa-circle-info', 'bg-slate-800']
    }[type] || ['fa-circle-info', 'bg-slate-800']
    const el = document.createElement('div')
    el.className = `${conf[1]} text-white px-4 py-2.5 rounded-lg shadow-2xl flex items-center gap-2.5 text-sm max-w-md animate-[slideIn_.2s_ease-out]`
    el.innerHTML = `<i class="fas ${conf[0]}"></i><span class="flex-1">${esc(message)}</span>`
    document.getElementById('toast-root').appendChild(el)
    setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(20px)'; el.style.transition = 'all .25s'; setTimeout(() => el.remove(), 260) }, ms)
  }

  /* ------------------------- Loading ------------------------- */
  function loading(show, text = '处理中…') {
    const m = document.getElementById('loading-mask')
    document.getElementById('loading-text').textContent = text
    m.classList.toggle('hidden', !show)
    m.classList.toggle('flex', show)
  }

  /* ------------------------- Modal ------------------------- */
  let modalSeq = 0
  /**
   * @param {object} o {title, body(html), size, footer(html), onMount(root, close), closable}
   * @returns {{close: Function, root: HTMLElement}}
   */
  function modal(o) {
    const id = 'modal-' + (++modalSeq)
    const sizes = { sm: 'max-w-md', md: 'max-w-2xl', lg: 'max-w-4xl', xl: 'max-w-6xl', full: 'max-w-[95vw]' }
    const wrap = document.createElement('div')
    wrap.id = id
    wrap.className = 'fixed inset-0 z-[9000] flex items-center justify-center p-4 bg-black/45 backdrop-blur-sm'
    wrap.innerHTML = `
      <div class="bg-white rounded-xl shadow-2xl w-full ${sizes[o.size] || sizes.md} max-h-[92vh] flex flex-col" role="dialog" aria-modal="true">
        <div class="flex items-center gap-3 px-5 py-3.5 border-b border-slate-200 shrink-0">
          <h3 class="font-semibold text-ink-800 flex-1">${o.title || ''}</h3>
          ${o.closable === false ? '' : '<button data-close class="text-slate-400 hover:text-slate-700 px-1" aria-label="关闭"><i class="fas fa-xmark text-lg"></i></button>'}
        </div>
        <div data-body class="flex-1 overflow-auto px-5 py-4">${o.body || ''}</div>
        ${o.footer ? `<div data-footer class="px-5 py-3.5 border-t border-slate-200 flex justify-end gap-2 shrink-0 bg-slate-50 rounded-b-xl">${o.footer}</div>` : ''}
      </div>`
    document.getElementById('modal-root').appendChild(wrap)
    const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey) }
    const onKey = (e) => { if (e.key === 'Escape' && o.closable !== false) close() }
    document.addEventListener('keydown', onKey)
    wrap.querySelector('[data-close]')?.addEventListener('click', close)
    wrap.addEventListener('click', (e) => { if (e.target === wrap && o.closable !== false) close() })
    o.onMount?.(wrap, close)
    return { close, root: wrap }
  }

  /** 强制关闭所有弹窗与遮罩。
   *  用于「会话超时自动退出」：此时 closable:false 的弹窗（如首登强制改密）
   *  也必须被清掉，否则登录页会被一层残留遮罩挡住，用户无法重新登录。 */
  function closeAllModals() {
    const root = document.getElementById('modal-root')
    if (root) root.innerHTML = ''
    loading(false)
  }

  /* ------------------------- Confirm ------------------------- */
  function confirm(o) {
    return new Promise((resolve) => {
      const danger = o.danger !== false
      const m = modal({
        title: o.title || '确认操作',
        size: 'sm',
        body: `<div class="flex gap-3">
            <div class="h-10 w-10 shrink-0 rounded-full grid place-items-center ${danger ? 'bg-red-100 text-red-600' : 'bg-brand-50 text-brand-600'}">
              <i class="fas ${danger ? 'fa-triangle-exclamation' : 'fa-circle-question'}"></i>
            </div>
            <div class="text-sm text-slate-600 leading-relaxed pt-1">${o.message || ''}</div>
          </div>
          ${o.requireText ? `<div class="mt-4"><label class="field-label">请输入 <code class="bg-slate-100 px-1 rounded text-red-600">${esc(o.requireText)}</code> 以确认</label>
            <input data-req class="field-input" placeholder="${esc(o.requireText)}"></div>` : ''}`,
        footer: `<button data-cancel class="btn btn-ghost">取消</button>
                 <button data-ok class="btn ${danger ? 'btn-danger' : 'btn-primary'}" ${o.requireText ? 'disabled' : ''}>${esc(o.okText || '确认')}</button>`,
        onMount(root, close) {
          const ok = root.querySelector('[data-ok]')
          const req = root.querySelector('[data-req]')
          req?.addEventListener('input', () => { ok.disabled = req.value.trim() !== o.requireText })
          root.querySelector('[data-cancel]').addEventListener('click', () => { close(); resolve(false) })
          ok.addEventListener('click', () => { close(); resolve(true) })
        }
      })
      m.root.addEventListener('click', (e) => { if (e.target === m.root) resolve(false) })
    })
  }

  /* ------------------------- 格式化 ------------------------- */
  const fmt = {
    gender: (g) => g === 'M' ? '男' : g === 'F' ? '女' : '未知',
    genderIcon: (g) => g === 'M'
      ? '<span class="text-blue-600" title="男"><i class="fas fa-person"></i> 男</span>'
      : g === 'F' ? '<span class="text-pink-600" title="女"><i class="fas fa-person-dress"></i> 女</span>'
      : '<span class="text-slate-400"><i class="fas fa-question"></i> 未知</span>',
    role: (r) => ({ PLATFORM_ADMIN: '平台管理员', DOCTOR: '医生', NURSE: '护士' }[r] || r),
    status: (s) => {
      const map = { DRAFT: ['badge-draft', '草稿'], SUBMITTED: ['badge-submitted', '已提交'], ARCHIVED: ['badge-archived', '已归档'] }
      const [cls, label] = map[s] || ['badge-draft', s]
      return `<span class="badge ${cls}">${label}</span>`
    },
    date: (d) => d ? String(d).slice(0, 10) : '—',
    datetime: (d) => d ? String(d).replace('T', ' ').slice(0, 19) : '—',
    age: (birth) => {
      if (!birth) return '—'
      // HIS 屏幕上只有年龄没有出生日期，这类患者的生日存的是哨兵值。
      // 直接丢给下面的算式会得出「2025 岁」——不报错，只是安静地印在病历上。
      if (birth === AGE_ONLY_BIRTH_DATE) return '—'
      const b = new Date(birth + 'T00:00:00'); const n = new Date()
      let a = n.getFullYear() - b.getFullYear()
      const m = n.getMonth() - b.getMonth()
      if (m < 0 || (m === 0 && n.getDate() < b.getDate())) a--
      return Math.max(0, a) + ' 岁'
    },
    /** 患者年龄的**唯一**展示入口：有真实生日就现算，只有年龄就读 age_years。
     *  与后端 lib/db.ts 的 patientAge() 一一对应，两边口径必须一致。 */
    patientAge: (p) => {
      if (!p) return '—'
      if (p.birth_date === AGE_ONLY_BIRTH_DATE || !p.birth_date) {
        return (p.age_years === null || p.age_years === undefined || p.age_years === '')
          ? '—' : Number(p.age_years) + ' 岁'
      }
      return fmt.age(p.birth_date)
    },
    empty: (v) => (v === null || v === undefined || v === '') ? '<span class="text-slate-300">—</span>' : esc(v)
  }

  /* ------------------------- 分页器 ------------------------- */
  function pager(total, page, size, onGo) {
    const pages = Math.max(1, Math.ceil(total / size))
    const el = document.createElement('div')
    el.className = 'flex items-center justify-between gap-3 pt-4 text-sm'
    el.innerHTML = `
      <span class="text-slate-500">共 <b class="text-ink-800">${total}</b> 条 · 第 ${page}/${pages} 页</span>
      <div class="flex gap-1">
        <button data-p="1" class="btn btn-sm btn-ghost" ${page <= 1 ? 'disabled' : ''}><i class="fas fa-angles-left"></i></button>
        <button data-p="${page - 1}" class="btn btn-sm btn-ghost" ${page <= 1 ? 'disabled' : ''}><i class="fas fa-angle-left"></i></button>
        <button data-p="${page + 1}" class="btn btn-sm btn-ghost" ${page >= pages ? 'disabled' : ''}><i class="fas fa-angle-right"></i></button>
        <button data-p="${pages}" class="btn btn-sm btn-ghost" ${page >= pages ? 'disabled' : ''}><i class="fas fa-angles-right"></i></button>
      </div>`
    el.querySelectorAll('[data-p]').forEach(b => b.addEventListener('click', () => onGo(Number(b.dataset.p))))
    return el
  }

  function emptyState(icon, title, hint, actionHtml = '') {
    return `<div class="py-16 text-center">
      <i class="fas ${icon} text-5xl text-slate-200 mb-4"></i>
      <p class="text-slate-600 font-medium">${esc(title)}</p>
      ${hint ? `<p class="text-sm text-slate-400 mt-1">${esc(hint)}</p>` : ''}
      ${actionHtml ? `<div class="mt-5">${actionHtml}</div>` : ''}
    </div>`
  }

  /* ==================== 可搜索分类下拉（combobox） ====================
   * 替代原生 <datalist>：原生下拉无法控制高度/分组/样式，选项多时会铺满
   * 整屏并溢出弹窗。本组件：
   *   - 固定最大高度 + 内部滚动（绝不撑破容器）
   *   - 按分类分组、粘性组标题
   *   - 输入即过滤（拼音/别名可通过 keywords 支持）
   *   - 允许自由输入（不限于候选项），支持键盘上下/回车/Esc
   *   - 智能上下翻转，避免贴近屏幕底部时被裁切
   *
   * @param {HTMLInputElement} input 目标输入框
   * @param {object} o {groups:[{label, items:[{value,keywords}|string]}], onPick}
   */
  function combobox(input, o) {
    if (!input || input.dataset.cbxBound === '1') return
    input.dataset.cbxBound = '1'
    input.setAttribute('autocomplete', 'off')
    input.setAttribute('role', 'combobox')

    /**
     * 候选组的读取器。
     * 支持传函数：过敏原候选库现在是**可编辑数据**，用户在「模版库 → 过敏原库」
     * 改完后，已经绑定过的输入框必须能拿到新数据；若在此把数组快照下来，
     * 这些输入框会一直用旧候选，直到整页重绘。
     */
    const norm = (gs) => (gs || []).map(g => ({
      label: g.label || '',
      items: (g.items || []).map(it => (typeof it === 'string' ? { value: it } : it))
    }))
    const getGroups = typeof o.groups === 'function' ? () => norm(o.groups()) : () => norm(o.groups)

    let panel = null
    let flat = []      // 当前可见的候选项 [{value, el}]
    let active = -1
    let suppress = false   // 选中后抑制一次重开（commit 派发的 input 事件会触发 openPanel）

    const closePanel = () => {
      if (!panel) return
      panel.remove(); panel = null; flat = []; active = -1
      input.setAttribute('aria-expanded', 'false')
    }

    const commit = (val) => {
      input.value = val
      closePanel()
      // 选中即结束本次选择：派发事件让业务层同步数据，但不要因此重开面板
      suppress = true
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
      suppress = false
      if (o.onPick) o.onPick(val, input)
    }

    const setActive = (i) => {
      if (!flat.length) return
      if (active >= 0 && flat[active]) flat[active].el.classList.remove('cbx-active')
      active = (i + flat.length) % flat.length
      const cur = flat[active]
      cur.el.classList.add('cbx-active')
      cur.el.scrollIntoView({ block: 'nearest' })
    }

    const place = () => {
      if (!panel) return
      // 输入框已被移出 DOM（如表格重绘），面板不应继续悬浮
      if (!input.isConnected) { closePanel(); return }
      const r = input.getBoundingClientRect()
      // 面板最大高度：优先向下，空间不足则向上翻转
      const below = window.innerHeight - r.bottom - 12
      const above = r.top - 12
      const flip = below < 200 && above > below
      const maxH = Math.max(140, Math.min(288, flip ? above : below))
      /*
       * 宽度：以输入框宽度为**最小值**，内容更宽时允许撑开（候选名称由临床自维护，
       * 可能比输入框长），但不越出视口右边缘。
       */
      panel.style.minWidth = r.width + 'px'
      panel.style.maxWidth = Math.max(r.width, window.innerWidth - r.left - 12) + 'px'
      panel.style.maxHeight = maxH + 'px'
      // 撑宽后可能超出右边界，向左回收
      panel.style.left = r.left + 'px'
      const pw = panel.offsetWidth
      if (r.left + pw > window.innerWidth - 8) {
        panel.style.left = Math.max(8, window.innerWidth - 8 - pw) + 'px'
      }
      if (flip) {
        panel.style.top = ''
        panel.style.bottom = (window.innerHeight - r.top + 4) + 'px'
      } else {
        panel.style.bottom = ''
        panel.style.top = (r.bottom + 4) + 'px'
      }
    }

    /**
     * @param {boolean} showAll 忽略输入框内容，列出全部候选。
     *
     * 为什么需要它：输入框里**已有值**时（如对照组默认「生理盐水」，或编辑既有
     * 模版/报告单），这个值会被当成搜索词，把其它候选全过滤掉 —— 下拉里只剩它
     * 自己。表现出来就是「过敏原库里新增的项，在模版编辑里看不到」，很容易被
     * 误判成缓存没刷新，实际是过滤把它挡住了。
     * 所以：**点击 / 按 ↓ = 我要挑一个，列全部**；**打字 = 我在搜，按词过滤**。
     */
    const openPanel = (showAll) => {
      if (suppress) return
      const kw = showAll ? '' : input.value.trim().toLowerCase()
      const match = (it) =>
        !kw ||
        it.value.toLowerCase().includes(kw) ||
        (it.keywords || '').toLowerCase().includes(kw)

      const vis = getGroups()
        .map(g => ({ label: g.label, items: g.items.filter(match) }))
        .filter(g => g.items.length)

      if (!vis.length) { closePanel(); return }

      if (!panel) {
        // 全局同时只允许一个面板：表格重绘等场景可能残留孤儿面板
        document.querySelectorAll('.cbx-panel').forEach(el => el.remove())
        panel = document.createElement('div')
        panel.className = 'cbx-panel'
        document.body.appendChild(panel)
        input.setAttribute('aria-expanded', 'true')
      }

      panel.innerHTML = vis.map(g => `
        <div class="cbx-group">
          ${g.label ? `<div class="cbx-group-label">${esc(g.label)}</div>` : ''}
          <div class="cbx-items">
            ${g.items.map(it => `<button type="button" class="cbx-item" data-v="${esc(it.value)}">${esc(it.value)}</button>`).join('')}
          </div>
        </div>`).join('')

      flat = [...panel.querySelectorAll('.cbx-item')].map(el => ({ value: el.dataset.v, el }))
      active = -1
      flat.forEach((f, i) => {
        // 用 mousedown 而非 click：避免 input 先失焦导致面板被关掉
        f.el.addEventListener('mousedown', (e) => { e.preventDefault(); commit(f.value) })
        f.el.addEventListener('mouseenter', () => setActive(i))
      })
      place()
    }

    // 只在「用户主动交互」时展开：点击输入框、输入内容、按 ↓。
    // 不监听 focus —— 否则新增行后的自动聚焦、Tab 切换都会弹出面板挡住界面。
    // 点击/↓ 传 true（列全部候选），打字传 false（按输入内容过滤）。
    input.addEventListener('click', () => openPanel(true))
    input.addEventListener('input', () => openPanel(false))
    input.addEventListener('blur', () => setTimeout(closePanel, 120))
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); panel ? setActive(active + 1) : openPanel(true) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); if (panel) setActive(active - 1) }
      else if (e.key === 'Enter') { if (panel && active >= 0) { e.preventDefault(); commit(flat[active].value) } }
      else if (e.key === 'Escape') { if (panel) { e.stopPropagation(); closePanel() } }
      else if (e.key === 'Tab') closePanel()
    })
    window.addEventListener('scroll', () => panel && place(), true)
    window.addEventListener('resize', () => panel && place())
  }

  return { esc, toast, loading, modal, closeAllModals, confirm, fmt, pager, emptyState, combobox, AGE_ONLY_BIRTH_DATE }
})()

/* 动画 keyframes */
const style = document.createElement('style')
style.textContent = '@keyframes slideIn{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}'
document.head.appendChild(style)
