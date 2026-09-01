/* =============================================================================
 * PrintRender —— 报告单自定义版式的共享渲染器
 * -----------------------------------------------------------------------------
 * 配置(config)→ 报告单三段 HTML(head / meta 已并入 head、foot)+ 表格版式参数。
 * 两个消费方：
 *   ① 管理后台「报告单版式」页的实时预览（PageAdmin.printTemplates）
 *   ② 报告单打印（PageReport.renderPrintParts / renderTable）
 * 只有一份渲染实现，预览与实际打印永远一致 —— 这是本文件存在的全部理由。
 * 若在 pages-admin.js 与 pages-report.js 里各写一份，两边必然渐行渐远，
 * 最终出现"预览是对的、打出来不对"这种最难排查的偏差。
 *
 * 配置 schema v1（normalizeConfig 是唯一权威，后端不逐字段校验）：
 * {
 *   header: {
 *     hospital_lines: ['吉林省儿童医疗中心','长春市儿童医院'], // 1-2 行医院名
 *     show_logo: false,          // 是否在表头显示医院 logo（hospital.logo_url）
 *     left_text: '门诊',         // 左侧小标（留空不显示）
 *     show_cross: true,          // 医院名左侧的 ✚ 标记
 *     title_mode: 'template',    // template=跟随项目模板名 | fixed=固定文字
 *     title_fixed: '过敏原检测报告单',
 *     layout: 'inline'           // inline=左标|医院名|标题 一行式(长春儿医)
 *                                // stacked=居中医院名+标题两行(通用)
 *   },
 *   patient: {
 *     fields: ['name','gender','age','medical_record_no','department',
 *              'applying_doctor','applied_at','serial_no','clinical_diagnosis','remarks'],
 *     custom_fields: [{label:'检测方法', value:'皮肤点刺'}], // 自定义条目：
 *                                // value 留空 = 打印一条横线供手填
 *     columns: 4                 // 2-4 列
 *   },
 *   table: {
 *     columns: 4,                // 打印列数 2 或 4
 *     show_no: false,            // 是否显示序号格
 *     result_align: 'right',     // left=紧跟名称 | right=推至列右缘
 *     gap_px: 20,                // 名称与结果的最小间隔
 *     min_rows: 4                // 至少占满几行（不足补空槽，避免中部留白）
 *   },
 *   footer: {
 *     items: ['executed_at','reported_at','tester','reviewer'],
 *     custom_items: [{label:'录入者', value:''}],  // 自定义条目，value 留空 = 签名横线
 *     note: '本报告单仅反映本次皮肤点刺实验结果，请结合临床综合判断。'
 *   },
 *   paper: { size: 'A4' }        // A4 | A5
 * }
 * ============================================================================= */
window.PrintRender = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

  /** 患者信息可选字段清单（配置页勾选与打印取值共用同一份定义） */
  const PATIENT_FIELDS = [
    { key: 'name',               label: '姓名' },
    { key: 'gender',             label: '性别' },
    { key: 'age',                label: '年龄' },
    { key: 'medical_record_no',  label: '病历号' },
    { key: 'department',         label: '科室' },
    { key: 'applying_doctor',    label: '申请医生' },
    { key: 'applied_at',         label: '申请时间' },
    { key: 'serial_no',          label: '流水号' },
    { key: 'clinical_diagnosis', label: '临床诊断' },
    { key: 'remarks',            label: '备注' }
  ]
  const FOOTER_ITEMS = [
    { key: 'executed_at', label: '执行时间' },
    { key: 'reported_at', label: '报告时间' },
    { key: 'tester',      label: '检验者（签名栏）' },
    { key: 'reviewer',    label: '审核者（签名栏）' }
  ]

  /** 兜底默认配置 = 一张中规中矩的通用报告单 */
  function defaults() {
    return {
      header: {
        hospital_lines: [], show_logo: false, left_text: '', show_cross: false,
        title_mode: 'template', title_fixed: '过敏原检测报告单', layout: 'stacked'
      },
      patient: {
        fields: ['name', 'gender', 'age', 'medical_record_no', 'department',
                 'applying_doctor', 'applied_at', 'clinical_diagnosis', 'remarks'],
        custom_fields: [],
        columns: 4
      },
      table: { columns: 4, show_no: false, result_align: 'right', gap_px: 20, min_rows: 4 },
      footer: {
        items: ['executed_at', 'reported_at', 'tester', 'reviewer'],
        custom_items: [],
        note: '本报告单仅反映本次皮肤点刺实验结果，请结合临床综合判断。'
      },
      paper: { size: 'A4' }
    }
  }

  /** 把任意（可能残缺/超范围）的存量配置收敛为合法配置。
   *  所有消费方入口都必须先过这里 —— 配置来自数据库，版本会新旧混存。 */
  function normalizeConfig(raw) {
    const d = defaults()
    const c = (raw && typeof raw === 'object') ? raw : {}
    const h = c.header || {}, p = c.patient || {}, t = c.table || {}, f = c.footer || {}, pa = c.paper || {}
    const validKeys = new Set(PATIENT_FIELDS.map(x => x.key))
    const validFoot = new Set(FOOTER_ITEMS.map(x => x.key))
    /* 自定义条目：label 必填（没名称的条目在纸上没有意义），value 可空
     * （空 = 打印横线手填）。上限防手滑批量粘贴把配置撑爆。 */
    const customList = (arr, max) => (Array.isArray(arr) ? arr : [])
      .map(x => ({
        label: String(x && x.label || '').trim().slice(0, 12),
        value: String(x && x.value || '').trim().slice(0, 40)
      }))
      .filter(x => x.label)
      .slice(0, max)
    return {
      header: {
        hospital_lines: (Array.isArray(h.hospital_lines) ? h.hospital_lines : d.header.hospital_lines)
          .map(x => String(x || '').trim()).filter(Boolean).slice(0, 2),
        show_logo: h.show_logo === true,
        left_text: String(h.left_text ?? d.header.left_text).slice(0, 8),
        show_cross: h.show_cross === true,
        title_mode: h.title_mode === 'fixed' ? 'fixed' : 'template',
        title_fixed: String(h.title_fixed ?? d.header.title_fixed).slice(0, 40),
        layout: h.layout === 'inline' ? 'inline' : 'stacked'
      },
      patient: {
        fields: (Array.isArray(p.fields) ? p.fields : d.patient.fields).filter(k => validKeys.has(k)),
        custom_fields: customList(p.custom_fields, 10),
        columns: [2, 3, 4].includes(p.columns) ? p.columns : d.patient.columns
      },
      table: {
        columns: t.columns === 2 ? 2 : 4,
        show_no: t.show_no === true,
        result_align: t.result_align === 'left' ? 'left' : 'right',
        gap_px: Math.min(60, Math.max(4, Number(t.gap_px) || d.table.gap_px)),
        min_rows: Math.min(10, Math.max(0, Number.isFinite(Number(t.min_rows)) ? Number(t.min_rows) : d.table.min_rows))
      },
      footer: {
        items: (Array.isArray(f.items) ? f.items : d.footer.items).filter(k => validFoot.has(k)),
        custom_items: customList(f.custom_items, 6),
        note: String(f.note ?? d.footer.note).slice(0, 200)
      },
      paper: { size: pa.size === 'A5' ? 'A5' : 'A4' }
    }
  }

  /**
   * 渲染表头（含患者信息区）。
   * @param cfg  normalizeConfig 后的配置
   * @param data { hospital_name, logo_src, template_name, patient:{...字段值}, }
   *   logo_src 由调用方先经 API.imageUrl 换成 blob URL 再传入 ——
   *   渲染器保持纯函数，不做网络请求。
   */
  function renderHead(cfg, data) {
    const h = cfg.header
    const lines = h.hospital_lines.length ? h.hospital_lines : [data.hospital_name || '']
    const title = h.title_mode === 'fixed'
      ? h.title_fixed
      : ((data.template_name || '').trim() || h.title_fixed)
    const logo = (h.show_logo && data.logo_src)
      ? `<img src="${esc(data.logo_src)}" alt="" style="height:34px;width:auto;object-fit:contain;">`
      : ''
    const hospHtml = lines.map(l => `<div>${esc(l)}</div>`).join('')

    let topline
    if (h.layout === 'inline') {
      /* 长春儿医式：左标 | ✚/logo | 医院名 | 标题（右对齐），底部横线 */
      topline = `
        <div class="cpt-topline cpt-inline">
          ${h.left_text ? `<div class="cpt-left">${esc(h.left_text)}</div>` : '<div></div>'}
          <div class="cpt-brand">${logo || (h.show_cross ? '✚' : '')}</div>
          <div class="cpt-hosp">${hospHtml}</div>
          <div class="cpt-title">${esc(title)}</div>
        </div>`
    } else {
      /* 通用式：居中 logo+医院名，标题独立一行居中 */
      topline = `
        <div class="cpt-topline cpt-stacked">
          <div class="cpt-hosp-row">${logo}<div class="cpt-hosp">${hospHtml}</div></div>
          <div class="cpt-title">${esc(title)}</div>
        </div>`
    }

    const pv = data.patient || {}
    const metaCells = cfg.patient.fields.map(k => {
      const def = PATIENT_FIELDS.find(x => x.key === k)
      return `<div><span>${esc(def ? def.label : k)}：</span>${esc(pv[k] ?? '')}</div>`
    }).concat(cfg.patient.custom_fields.map(cf =>
      /* 自定义条目：固定内容直接印，留空印横线（现场手填的场景，如"采样部位"） */
      `<div><span>${esc(cf.label)}：</span>${cf.value ? esc(cf.value) : '<i class="cpt-blank"></i>'}</div>`
    )).join('')
    const meta = (cfg.patient.fields.length || cfg.patient.custom_fields.length)
      ? `<div class="cpt-meta" style="grid-template-columns:repeat(${cfg.patient.columns},minmax(0,1fr));">${metaCells}</div>`
      : ''
    return `<div class="cpt-head">${topline}${meta}</div>`
  }

  /** 渲染页脚 */
  function renderFoot(cfg, data) {
    const v = data || {}
    const sig = '<span style="display:inline-block;min-width:88px;border-bottom:1px solid #000;height:14px;vertical-align:bottom;"></span>'
    const cells = cfg.footer.items.map(k => {
      if (k === 'executed_at') return `<div><span>执行时间：</span>${esc(v.executed_at || '')}</div>`
      if (k === 'reported_at') return `<div><span>报告时间：</span>${esc(v.reported_at || '')}</div>`
      if (k === 'tester') return `<div><span>检验者：</span>${v.tester ? esc(v.tester) : sig}</div>`
      if (k === 'reviewer') return `<div><span>审核者：</span>${v.reviewer ? `<span style="border-bottom:1px solid #000;padding:0 6px;">${esc(v.reviewer)}</span>` : sig}</div>`
      return ''
    }).concat(cfg.footer.custom_items.map(ci =>
      /* 自定义条目：固定内容直接印，留空印签名横线 */
      `<div><span>${esc(ci.label)}：</span>${ci.value ? esc(ci.value) : sig}</div>`
    )).join('')
    const nCells = cfg.footer.items.length + cfg.footer.custom_items.length
    const grid = nCells
      ? `<div class="cpt-foot-row" style="grid-template-columns:repeat(${Math.max(nCells, 2)},minmax(0,1fr));">${cells}</div>`
      : ''
    const note = cfg.footer.note ? `<div class="cpt-foot-note">${esc(cfg.footer.note)}</div>` : ''
    return `${grid}${note}`
  }

  /**
   * 管理后台预览：用假数据渲染一张完整报告单（含示例表格）。
   * 表格部分这里独立实现 —— 预览不依赖 PageReport 的真实 st 状态。
   * 打印端的真实表格仍由 pages-report.js 的 renderTable 渲染（它要处理
   * 空项目丢弃、对照组排序、行高自适应等业务），两边共享的是 CSS 类
   * （.cpt-grid 一套版式变量），版式参数（columns/show_no/对齐/gap）
   * 从同一份 cfg 读取，视觉结果一致。
   */
  function renderPreview(cfg, data) {
    const demoRows = (data && data.rows) || [
      { no: 1, name: '尘螨', res: '++++' }, { no: 2, name: '屋尘螨', res: '+++' },
      { no: 3, name: '花粉', res: '—' }, { no: 4, name: '猫毛', res: '+' },
      { no: 5, name: '狗毛', res: '—' }, { no: 6, name: '霉菌', res: '++' },
      { no: 7, name: '蟑螂', res: '—' }, { no: 8, name: '牛奶', res: '+++' },
      { no: '阳', name: '阳性对照', res: '+++' }, { no: '阴', name: '阴性对照', res: '—' }
    ]
    const t = cfg.table
    const cell = (r) => `
      <div class="cpt-cell" style="gap:${t.gap_px}px;">
        ${t.show_no ? `<span class="cpt-no">${esc(r.no)}</span>` : ''}
        <span class="cpt-name">${esc(r.name)}</span>
        <span class="cpt-res" style="${t.result_align === 'right' ? 'margin-left:auto;' : ''}">${esc(r.res)}</span>
      </div>`
    let cells = demoRows.map(cell)
    const per = t.columns
    while (cells.length % per !== 0 || cells.length < t.min_rows * per) cells.push('<div class="cpt-cell"></div>')
    const table = `<div class="cpt-grid" style="grid-template-columns:repeat(${per},minmax(0,1fr));">${cells.join('')}</div>`
    return `
      <div class="cpt-page cpt-${cfg.paper.size.toLowerCase()}">
        ${renderHead(cfg, data || {})}
        ${table}
        <div class="cpt-foot">${renderFoot(cfg, (data && data.footer) || {
          executed_at: '2026-09-01 16:26', reported_at: '2026-09-01 16:46', reviewer: '示例医生'
        })}</div>
      </div>`
  }

  return { defaults, normalizeConfig, renderHead, renderFoot, renderPreview, PATIENT_FIELDS, FOOTER_ITEMS }
})()
