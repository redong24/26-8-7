/* ============================ 模版库 ============================ */
window.PageTemplates = (() => {

  /**
   * 项目数**不做硬约束**：可少于也可多于 20 项，根据实际情况确定。
   * DEFAULT_ROWS：附件 2 纸质版式的默认位置数（少于此数时报告单仍补齐到 20 个位置）。
   * MAX_ROWS：上限 100，与后端 MAX_POS_COUNT 一致，仅为防御异常入参
   *           （取 100 是为了不与对照位号 101/102 冲突）。
   */
  const DEFAULT_ROWS = 20
  const MAX_ROWS = 100

  /*
   * 过敏原候选库来自共享文件 static/allergens.js（现已改为院内可编辑，数据源 /api/allergens）。
   * 注意：**不能**在此层把 ALLERGENS.GROUPS 存成常量——候选库可在本页「过敏原库」模块里
   * 随时修改，必须每次使用时重新读 getter，否则下拉仍是旧数据。
   */
  const allergenGroups = () => ALLERGENS.GROUPS
  const commonList = () => ALLERGENS.COMMON

  /* 页内标签：模版列表 / 过敏原库（后者为候选库维护模块） */
  const TABS = [
    { key: 'list', label: '模版列表', icon: 'fa-layer-group' },
    { key: 'catalog', label: '过敏原库', icon: 'fa-vial' }
  ]
  let tab = 'list'

  async function render(body) {
    // 先拉院内候选库，避免首屏下拉先显示内置兜底库
    await ALLERGENS.ensure()
    body.innerHTML = `
      <div class="space-y-4">
        <div class="seg-group" id="t-tabs">
          ${TABS.map(t => `<button data-tab="${t.key}" class="seg-btn${t.key === tab ? ' seg-active' : ''}">
            <i class="fas ${t.icon} mr-1.5"></i>${t.label}</button>`).join('')}
        </div>
        <div id="t-pane"></div>
      </div>`

    body.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
      if (tab === b.dataset.tab) return
      tab = b.dataset.tab
      body.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('seg-active', x.dataset.tab === tab))
      paintPane()
    }))
    paintPane()
  }

  function paintPane() {
    const pane = document.getElementById('t-pane')
    if (tab === 'catalog') { PageAllergens.render(pane); return }
    pane.innerHTML = `
      <div class="space-y-4">
        <div class="card p-4 flex flex-wrap items-center gap-3">
          <button id="t-new" class="btn btn-primary"><i class="fas fa-plus"></i>新建模版</button>
          <button id="t-trash" class="btn btn-ghost"><i class="fas fa-trash-can-arrow-up"></i>回收站</button>
          <div class="flex-1"></div>
          <div class="relative">
            <i class="fas fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-sm"></i>
            <input id="t-kw" class="field-input !pl-9 w-56" placeholder="搜索模版名称">
          </div>
        </div>
        <div class="flex items-start gap-2 text-xs bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-lg px-3 py-2">
          <i class="fas fa-shield-halved mt-0.5"></i>
          <span><b>删除模版不会影响历史报告单</b>：每份报告单在创建时已完整保存当时的过敏原项目内容，即使之后删除了模版，历史报告单依然可以正常查看与打印。</span>
        </div>
        <div id="t-grid" class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4"></div>
      </div>`

    document.getElementById('t-new').addEventListener('click', () => openEditor(null))
    document.getElementById('t-trash').addEventListener('click', openTrash)
    let t
    document.getElementById('t-kw').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => load(e.target.value), 320) })
    load()
  }

  async function load(kw = '') {
    const grid = document.getElementById('t-grid')
    if (!grid) return   // 当前停留在「过敏原库」标签，模版网格不在 DOM 中
    grid.innerHTML = '<p class="col-span-full text-center py-12 text-slate-400 text-sm"><i class="fas fa-circle-notch fa-spin"></i> 加载中…</p>'
    try {
      const r = await API.get('/api/templates', { kw })
      if (!r.data.length) {
        grid.innerHTML = `<div class="col-span-full">${UI.emptyState('fa-layer-group', '暂无模版', '创建模版可一键填充报告单的过敏原名称（项目数可自定义）',
          '<button class="btn btn-primary" onclick="PageTemplates.openEditor(null)"><i class="fas fa-plus"></i>新建模版</button>')}</div>`
        return
      }
      grid.innerHTML = r.data.map(t => `
        <article class="card card-hover p-4 flex flex-col">
          <div class="flex items-start gap-3">
            <span class="h-10 w-10 rounded-lg bg-brand-50 text-brand-600 grid place-items-center shrink-0"><i class="fas fa-layer-group"></i></span>
            <div class="min-w-0 flex-1">
              <h4 class="font-semibold text-ink-800 text-sm truncate">${UI.esc(t.name)}</h4>
              <p class="text-xs text-slate-400 line-clamp-2 mt-0.5">${UI.esc(t.description || '无描述')}</p>
            </div>
          </div>
          <div class="flex flex-wrap gap-1.5 mt-3 text-[11px]">
            <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">${t.allergen_count} 项</span>
            <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700" title="阳性对照组">阳性：${UI.esc(t.control_positive_allergen || '—')}</span>
            <span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700" title="阴性对照组">阴性：${UI.esc(t.control_negative_allergen || '—')}</span>
            ${t.usage_count ? `<span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700">被 ${t.usage_count} 份报告使用</span>` : ''}
          </div>
          <div class="mt-3 pt-3 border-t border-slate-100 flex items-center gap-1.5 text-xs text-slate-400">
            <i class="fas fa-user text-[10px]"></i>${UI.esc(t.created_by_name)}
            <span class="ml-1">${UI.fmt.date(t.created_at)}</span>
            <div class="flex-1"></div>
            <button data-preview="${t.id}" class="btn btn-xs btn-ghost" title="应用预览"><i class="fas fa-eye"></i></button>
            <button data-edit="${t.id}" class="btn btn-xs btn-ghost" title="编辑"><i class="fas fa-pen"></i></button>
            <button data-dup="${t.id}" class="btn btn-xs btn-ghost" title="复制"><i class="fas fa-copy"></i></button>
            <button data-del="${t.id}" data-name="${UI.esc(t.name)}" class="btn btn-xs btn-ghost text-red-500" title="删除"><i class="fas fa-trash"></i></button>
          </div>
        </article>`).join('')

      const find = (id) => r.data.find(x => x.id === id)
      grid.querySelectorAll('[data-preview]').forEach(b => b.addEventListener('click', () => preview(find(b.dataset.preview))))
      grid.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => openEditor(find(b.dataset.edit))))
      grid.querySelectorAll('[data-dup]').forEach(b => b.addEventListener('click', async () => {
        try { await API.post(`/api/templates/${b.dataset.dup}/duplicate`); UI.toast('已复制模版', 'success'); load() }
        catch (e) { UI.toast(e.message, 'error') }
      }))
      grid.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => doDelete(b.dataset.del, b.dataset.name)))
    } catch (e) {
      grid.innerHTML = `<p class="col-span-full text-red-600 text-sm py-10 text-center">${UI.esc(e.message)}</p>`
    }
  }

  /** 删除：提示 N 份历史报告单正在使用，删除后历史数据不受影响 */
  async function doDelete(id, name) {
    let usage = 0
    try { usage = (await API.get(`/api/templates/${id}/usage`)).usage_count } catch {}
    const ok = await UI.confirm({
      title: '删除模版',
      okText: '确认删除',
      message: `确认删除模版「<b>${UI.esc(name)}</b>」？<br><br>
        ${usage
          ? `<span class="block bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2 text-xs">
               <i class="fas fa-circle-info mr-1"></i><b>${usage}</b> 份历史报告单正在使用此模版，<b>删除后历史数据不受影响</b>（数据已快照）。</span>`
          : `<span class="block bg-slate-50 border border-slate-200 text-slate-600 rounded-lg px-3 py-2 text-xs">
               <i class="fas fa-circle-info mr-1"></i>暂无历史报告单引用此模版。</span>`}
        <span class="block mt-2 text-xs text-slate-500">删除后模版会移入「<b>回收站</b>」，可随时恢复。</span>`
    })
    if (!ok) return
    try {
      const r = await API.del('/api/templates/' + id)
      UI.toast(r.message || '模版已删除', 'success', 4500)
      load()
    } catch (e) { UI.toast(e.message, 'error') }
  }

  /* --------------------- 应用预览 --------------------- */

  function preview(t) {
    if (!t) return
    const rows = t.rows || []
    const at = (p) => rows.find(r => r.position_no === p)?.allergen_name || ''
    const cell = (p) => `<tr><td class="cell-no">${p}</td><td class="px-2 text-xs">${UI.esc(at(p)) || '<span class="text-slate-300">（留空，可现场手工填写）</span>'}</td><td></td><td></td></tr>`
    const filled = rows.filter(r => r.allergen_name).length
    // 位置数按实际项目数推导（不足 20 项仍按 20 个位置的纸质版式预览；超过则扩展）
    const total = Math.max(DEFAULT_ROWS, rows.reduce((m, r) => Math.max(m, Number(r.position_no) || 0), 0))
    const half = Math.ceil(total / 2)
    const leftPos = Array.from({ length: half }, (_, i) => i + 1)
    const rightPos = Array.from({ length: total - half }, (_, i) => half + i + 1)
    UI.modal({
      title: `<i class="fas fa-eye text-brand-500 mr-2"></i>应用预览 · ${UI.esc(t.name)}`,
      size: 'lg',
      body: `<p class="text-xs text-slate-500 mb-3">以下为应用该模版后报告单的样子（左栏 ${leftPos[0]}–${leftPos[leftPos.length - 1]} + 阳性对照 / 右栏 ${rightPos[0]}–${rightPos[rightPos.length - 1]} + 阴性对照）。
        本模版共 <b class="text-ink-800">${filled}</b> 项${filled < total ? `，其余 ${total - filled} 个位置留空` : ''}${total > DEFAULT_ROWS ? `；已超过纸质版式的 ${DEFAULT_ROWS} 项，版式自动扩展至 ${total} 个位置` : ''}。</p>
        <div class="spt-grid min-w-[760px]">
          <div class="spt-col"><table><colgroup><col class="c-no"><col class="c-name"><col class="c-pos"><col class="c-neg"></colgroup>
            <thead><tr><th>位置序号</th><th>过敏原名称</th><th>阳性/面积</th><th>阴性/面积</th></tr></thead><tbody>
            ${leftPos.map(cell).join('')}
            <tr class="ctrl-row"><td colspan="2" class="cell-ctrl-label">阳性对照 · ${UI.esc(t.control_positive_allergen || '组胺')}</td><td></td><td></td></tr>
          </tbody></table></div>
          <div class="spt-divider"></div>
          <div class="spt-col"><table><colgroup><col class="c-no"><col class="c-name"><col class="c-pos"><col class="c-neg"></colgroup>
            <thead><tr><th>位置序号</th><th>过敏原名称</th><th>阳性/面积</th><th>阴性/面积</th></tr></thead><tbody>
            ${rightPos.map(cell).join('')}
            <tr class="ctrl-row neg"><td colspan="2" class="cell-ctrl-label">阴性对照 · ${UI.esc(t.control_negative_allergen || '生理盐水')}</td><td></td><td></td></tr>
          </tbody></table></div>
        </div>`,
      footer: `<button data-close2 class="btn btn-ghost">关闭</button>
               <button data-use class="btn btn-primary"><i class="fas fa-check"></i>在新报告单中使用</button>`,
      onMount(root, close) {
        root.querySelector('[data-close2]').addEventListener('click', close)
        root.querySelector('[data-use]').addEventListener('click', () => { close(); App.go('report', { template_id: t.id }) })
      }
    })
  }

  /* --------------------- 新建/编辑模版 --------------------- */

  function openEditor(t) {
    const isNew = !t?.id

    // 已有模版：只取真正填了名称的行（尾部空行不再占位）
    // 新建模版：默认给 10 行（常见门诊套餐规模），不再强制 20 行
    let values = (t?.rows || [])
      .slice()
      .sort((a, b) => a.position_no - b.position_no)
      .map(r => r.allergen_name || '')
    while (values.length && !values[values.length - 1]) values.pop()
    if (!values.length) values = Array.from({ length: 10 }, () => '')

    UI.modal({
      title: `<i class="fas fa-layer-group text-brand-500 mr-2"></i>${isNew ? '新建模版' : '编辑模版'}`,
      size: 'xl',
      body: `
        <div class="space-y-4">
          <div class="grid md:grid-cols-2 gap-4">
            <div><label class="field-label">模版名称 <span class="text-red-500">*</span></label>
              <input data-name class="field-input" value="${UI.esc(t?.name || '')}" placeholder="例：儿童常见过敏原套餐"></div>
            <div><label class="field-label">描述</label>
              <input data-desc class="field-input" value="${UI.esc(t?.description || '')}" placeholder="适用范围说明"></div>
          </div>
          <div class="grid md:grid-cols-2 gap-4">
            <div><label class="field-label">阳性对照组</label>
              <input data-cpos class="field-input" value="${UI.esc(t?.control_positive_allergen || '组胺')}" placeholder="组胺"></div>
            <div><label class="field-label">阴性对照组</label>
              <input data-cneg class="field-input" value="${UI.esc(t?.control_negative_allergen || '生理盐水')}" placeholder="生理盐水"></div>
          </div>
          <div>
            <div class="flex flex-wrap items-center gap-2 mb-2">
              <label class="field-label !mb-0">过敏原项目</label>
              <span data-count class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600"></span>
              <div class="flex-1"></div>
              <button data-fill class="btn btn-xs btn-ghost" title="按顺序填充常见过敏原到空行"><i class="fas fa-wand-magic-sparkles"></i>填入常见</button>
              <button data-clear class="btn btn-xs btn-ghost"><i class="fas fa-eraser"></i>清空</button>
            </div>
            <!-- 四列：与报告单点刺结果表一致，建模版时所见即所得，
                 序号横向排（1 2 3 4 / 5 6 7 8 …）也与纸质单的行优先编号对齐 -->
            <div data-rows class="grid grid-cols-2 lg:grid-cols-4 gap-x-4 gap-y-1.5"></div>
            <div class="flex items-center gap-2 mt-2.5">
              <button data-add class="btn btn-sm btn-ghost"><i class="fas fa-plus"></i>新增一项</button>
              <span data-hint class="text-xs text-slate-400"></span>
            </div>
          </div>
          <p class="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 flex items-start gap-2">
            <i class="fas fa-circle-info mt-0.5 text-slate-400"></i>
            <span><b>项目数根据实际情况确定，不做限制</b>（可少于也可多于 20 项）。
            不足 20 项时报告单仍保持附件 2 的 20 个位置版式，未覆盖的位置留空供现场手工补填；
            超过 20 项时，报告单双栏与导出列会自动扩展。</span>
          </p>
        </div>`,
      footer: `<button data-cancel class="btn btn-ghost">取消</button>
               <button data-ok class="btn btn-primary"><i class="fas fa-check"></i>${isNew ? '创建模版' : '保存修改'}</button>`,
      onMount(root, close) {
        const wrap = root.querySelector('[data-rows]')
        const countEl = root.querySelector('[data-count]')
        const hintEl = root.querySelector('[data-hint]')
        const addBtn = root.querySelector('[data-add]')

        /** 读取当前所有输入框的值 */
        const readVals = () => [...wrap.querySelectorAll('[data-r]')].map(el => el.value)

        /**
         * 刷新「已填写 / 总数」计数与上限提示。
         * 必须能被「输入时」单独调用 —— 否则只在重绘时更新，
         * 用户打完字计数仍显示 0，与实际不符。
         */
        function syncCount(vals) {
          const n = vals.length
          const filled = vals.filter(v => v.trim()).length
          countEl.textContent = `${filled} / ${n} 项已填写`
          addBtn.disabled = n >= MAX_ROWS
          hintEl.textContent = n >= MAX_ROWS
            ? `已达上限 ${MAX_ROWS} 项`
            : n > DEFAULT_ROWS
              ? `已超过纸质版式的 ${DEFAULT_ROWS} 项，报告单与导出将自动扩展`
              : `项目数不限，可超过 ${DEFAULT_ROWS} 项`
        }

        /** 重绘行列表。序号始终连续，删除中间行后自动重排 */
        function paint(vals, focusIdx) {
          wrap.innerHTML = vals.map((v, i) => `
            <div class="tpl-row">
              <span class="tpl-no">${i + 1}</span>
              <input data-r="${i}" class="field-input !py-1.5 text-sm" value="${UI.esc(v)}" placeholder="过敏原名称（可搜索）">
              <button type="button" class="tpl-del" data-del-row="${i}" title="删除此项"
                ${vals.length <= 1 ? 'disabled' : ''}><i class="fas fa-xmark"></i></button>
            </div>`).join('')

          // 给每个输入框挂上可搜索下拉
          // 传函数（非数组）：候选库在「过敏原库」里改动后，已绑定的输入框也能读到新数据
          wrap.querySelectorAll('[data-r]').forEach(el => UI.combobox(el, { groups: allergenGroups }))

          wrap.querySelectorAll('[data-del-row]').forEach(b => b.addEventListener('click', () => {
            const cur = readVals()
            cur.splice(Number(b.dataset.delRow), 1)
            paint(cur.length ? cur : [''])
          }))

          // 在最后一行按回车 = 快速新增下一项（连续录入更顺手）
          wrap.querySelectorAll('[data-r]').forEach((el, i) => {
            el.addEventListener('keydown', (e) => {
              if (e.key !== 'Enter') return
              // combobox 打开且有高亮项时，回车交给它选中，不在这里加行
              if (document.querySelector('.cbx-panel .cbx-active')) return
              e.preventDefault()
              const cur = readVals()
              if (i === cur.length - 1) { if (cur.length < MAX_ROWS) paint([...cur, ''], cur.length) }
              else wrap.querySelector(`[data-r="${i + 1}"]`)?.focus()
            })
          })

          // 输入时实时更新计数（combobox 选中也会派发 input，一并覆盖）
          wrap.querySelectorAll('[data-r]').forEach(el =>
            el.addEventListener('input', () => syncCount(readVals())))

          syncCount(vals)

          if (focusIdx !== undefined) wrap.querySelector(`[data-r="${focusIdx}"]`)?.focus()
        }

        paint(values)

        // 对照组也给候选（语义不同，用各自的候选组）
        UI.combobox(root.querySelector('[data-cpos]'), { groups: () => ALLERGENS.CONTROL_POSITIVE })
        UI.combobox(root.querySelector('[data-cneg]'), { groups: () => ALLERGENS.CONTROL_NEGATIVE })

        addBtn.addEventListener('click', () => {
          const cur = readVals()
          if (cur.length >= MAX_ROWS) return UI.toast(`已达上限 ${MAX_ROWS} 项`, 'warn')
          paint([...cur, ''], cur.length)
        })

        root.querySelector('[data-fill]').addEventListener('click', () => {
          const COMMON = commonList()   // 每次重读：常用套餐可在「过敏原库」里随时调整
          if (!COMMON.length) return UI.toast('候选库里还没有标为「常用」的项目，可在「过敏原库」中标记', 'warn', 4500)
          const cur = readVals()
          // 先填满现有空行；若行数不足则自动补行到常用套餐长度
          const out = cur.slice()
          let k = 0
          for (let i = 0; i < out.length; i++) if (!out[i].trim()) out[i] = COMMON[k++] || ''
          while (k < COMMON.length && out.length < MAX_ROWS) out.push(COMMON[k++])
          paint(out)
        })

        root.querySelector('[data-clear]').addEventListener('click', () => {
          paint(readVals().map(() => ''))
        })

        root.querySelector('[data-cancel]').addEventListener('click', close)
        root.querySelector('[data-ok]').addEventListener('click', async () => {
          const name = root.querySelector('[data-name]').value.trim()
          if (!name) return UI.toast('请填写模版名称', 'warn')

          // 只提交非空项，并按顺序重新编号为 1..N
          const names = readVals().map(v => v.trim()).filter(Boolean)
          if (!names.length) return UI.toast('请至少填写 1 个过敏原项目', 'warn')

          const dup = names.find((v, i) => names.indexOf(v) !== i)
          if (dup) {
            const ok = await UI.confirm({
              title: '存在重复项',
              danger: false, okText: '仍然保存',
              message: `过敏原「<b>${UI.esc(dup)}</b>」重复出现，确认仍要保存吗？`
            })
            if (!ok) return
          }

          const payload = {
            name,
            description: root.querySelector('[data-desc]').value.trim(),
            control_positive_allergen: root.querySelector('[data-cpos]').value.trim(),
            control_negative_allergen: root.querySelector('[data-cneg]').value.trim(),
            rows: names.map((allergen_name, i) => ({ position_no: i + 1, allergen_name }))
          }
          try {
            UI.loading(true, '保存模版…')
            if (isNew) await API.post('/api/templates', payload)
            else await API.put('/api/templates/' + t.id, payload)
            UI.loading(false); close()
            UI.toast(isNew ? '模版已创建' : '模版已保存', 'success')
            load()
          } catch (e) { UI.loading(false); UI.toast(e.message, 'error') }
        })
      }
    })
  }

  /* --------------------- 回收站 --------------------- */

  function openTrash() {
    UI.modal({
      title: '<i class="fas fa-trash-can-arrow-up text-brand-500 mr-2"></i>模版回收站',
      size: 'lg',
      body: `<p class="text-xs text-slate-500 mb-3">这里的模版可随时恢复；已经开好的报告单不受影响，始终可以查看和打印。</p>
        <div data-list><p class="text-center py-8 text-slate-400 text-sm"><i class="fas fa-circle-notch fa-spin"></i> 加载中…</p></div>`,
      onMount(root, close) {
        const list = root.querySelector('[data-list]')
        const load2 = () => API.get('/api/templates/trash/list').then(r => {
          if (!r.data.length) { list.innerHTML = UI.emptyState('fa-trash', '回收站为空', ''); return }
          list.innerHTML = `<table class="data-table"><thead><tr><th>模版名称</th><th>过敏原数</th><th>删除时间</th><th class="text-right">操作</th></tr></thead>
            <tbody>${r.data.map(t => `<tr>
              <td class="font-medium">${UI.esc(t.name)} <span class="badge badge-deleted ml-1">已删除</span></td>
              <td>${(t.rows || []).filter(x => x.allergen_name).length} 项</td>
              <td class="text-xs text-slate-400">${UI.fmt.datetime(t.deleted_at)}</td>
              <td class="text-right"><button data-restore="${t.id}" class="btn btn-xs btn-success"><i class="fas fa-rotate-left"></i>恢复</button></td>
            </tr>`).join('')}</tbody></table>`
          list.querySelectorAll('[data-restore]').forEach(b => b.addEventListener('click', async () => {
            try { await API.post(`/api/templates/${b.dataset.restore}/restore`); UI.toast('模版已恢复', 'success'); load2(); load() }
            catch (e) { UI.toast(e.message, 'error') }
          }))
        }).catch(e => list.innerHTML = `<p class="text-red-600 text-sm py-6 text-center">${UI.esc(e.message)}</p>`)
        load2()
      }
    })
  }

  return { render, load, openEditor }
})()
