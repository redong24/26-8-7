/* =============================================================================
 * 报告单页面（核心）
 *
 * 版式对齐纸质单（长春市儿童医院 过敏原检测报告单）：
 *   内容区为 **4 列 × N 行**，每个格子 = 过敏原名称 + 序号 + 结果标记。
 *   序号**横向排**：第一行是 1 2 3 4，第二行是 5 6 7 8 ——
 *   这一点是拿实物单子逐格核对出来的，不是推测。若按纵向排（1..N/4 在第一列），
 *   打印出来的单子序号顺序与医院现行纸质单不一致，护士按号找位置会错位。
 *
 * 结果录入（本次改造）：
 *   阳性格点击 → 弹出 + / ++ / +++ / ++++ 四选一
 *   阴性格点击 → 自动填「—」（纸质单上阴性就是一道横杠）
 *
 * 默认 20 个位置；项目数不做硬约束，多于 20 项时自动按 4 列铺开。
 * ============================================================================= */
window.PageReport = (() => {

  const POS_CTRL = 101, NEG_CTRL = 102
  /** 默认位置数（纸质版式）；实际项目更多时按实际数量扩展 */
  const DEFAULT_POS_COUNT = 20
  /** 位置数上限：与后端 MAX_POS_COUNT 一致，避免与对照位号 101/102 冲突 */
  const MAX_POS_COUNT = 100
  /** 内容区列数：纸质单固定 4 列（屏幕与 A4 打印都用这个） */
  const GRID_COLS = 4
  /** A5 打印时的列数。A5 只有 469px 可用宽，4 列会把过敏原名称压到 45px
   *  （装不下 4 个汉字）；降到 2 列后名称有 162px，行数翻倍但 A5 高度够。
   *  详见 renderTable 里的推导。 */
  const PRINT_COLS_A5 = 4
  /** 阳性分级档位（纸质单上手写的就是这四档） */
  const POS_GRADES = ['+', '++', '+++', '++++']
  /** 阴性标记：纸质单上是一道横杠。用全角破折号，避免与减号/连字符混淆 */
  const NEG_MARK = '—'

  /* ---------------------- 打印版式（item 2b） ----------------------
   *
   * 两个互相牵制的选项：纸张尺寸(A4/A5) × 是否打印手臂照片。
   *
   * 【为什么"打印照片"时强制 A4，而不是让用户自己选】
   * 使用方的原话是「如果选择打印手臂实验区照片，则默认A4尺寸」。
   * 但这里做成**强制**而非"默认可改"，是因为 A5 纵向 12mm 边距下
   * 可用高度仅 ~124×136mm；表格（20 项 5 行）+ 患者信息 + 页眉页脚
   * 本就把 A5 占满，再插两张 75mm 高的照片必然溢出到第 2 页。
   * 而"报告单第 2 页只有一张照片"这种输出对护士来说就是打印坏了，
   * 她会重打、换设置、来问，最终还是回到 A4。
   * 与其让用户踩一遍，不如直接锁住并把原因写在界面上。
   *
   * 【为什么用 localStorage 记住】
   * 一间诊室的纸张和打印习惯是固定的（要么一直 A5 存档、要么一直 A4 带图），
   * 每次打印都重新勾一遍是纯粹的重复劳动。但**不写进报告单数据**：
   * 版式是这台电脑/这间诊室的输出偏好，不是这份报告单的临床属性；
   * 存进 DB 会让同一张单子在不同诊室打出不同版式，反而更难排查。
   */
  const PRINT_SIZES = { A4: 'A4', A5: 'A5' }
  const PRINT_TEMPLATES = { STANDARD: 'STANDARD', CCCH: 'CCCH', CCCH_V2: 'CCCH_V2' }
  const PRINT_PREF_KEY = 'spt.print.pref.v1'

  function normalizePrintTemplate(v) {
    if (v === PRINT_TEMPLATES.CCCH_V2) return PRINT_TEMPLATES.CCCH_V2
    if (v === PRINT_TEMPLATES.CCCH) return PRINT_TEMPLATES.CCCH
    return PRINT_TEMPLATES.STANDARD
  }

  function printTemplateLabel(v) {
    const t = normalizePrintTemplate(v)
    if (t === PRINT_TEMPLATES.CCCH_V2) return '长春儿医定制模板'
    if (t === PRINT_TEMPLATES.CCCH) return '长春儿医模板（旧）'
    return '标准模板'
  }

  /** A4 纵向可打印高度（297mm - 上下各 12mm ≈ 273mm ≈ 1032px） */
  const A4_BODY_PX = 1032
  /** A5 纵向可打印高度（210mm - 上下各 12mm ≈ 186mm ≈ 703px）
   *  注意这个 703 与 A4 的**宽度** 703px 数值相同纯属巧合（A5高=A4宽），
   *  两者含义完全不同，不要合并成一个常量。 */
  const A5_BODY_PX = 469

  /** 读打印偏好。localStorage 不可用（隐私模式/被禁）时回落默认值，不抛异常 */
  function loadPrintPref() {
    const def = { size: PRINT_SIZES.A4, photos: true, template: PRINT_TEMPLATES.STANDARD }
    try {
      const raw = localStorage.getItem(PRINT_PREF_KEY)
      if (!raw) return def
      const p = JSON.parse(raw)
      return {
        size: p.size === PRINT_SIZES.A5 ? PRINT_SIZES.A5 : PRINT_SIZES.A4,
        photos: p.photos !== false,
        template: normalizePrintTemplate(p.template)
      }
    } catch { return def }
  }

  function savePrintPref(pref) {
    const eff = effectivePrint(pref || {})
    const out = { size: eff.size, photos: eff.photos, template: eff.template }
    try { localStorage.setItem(PRINT_PREF_KEY, JSON.stringify(out)) } catch { /* 存不了就算了，不影响打印 */ }
  }

  /**
   * 把偏好归一成实际生效的版式。
   *
   * 这里是"打印照片 ⇒ 强制 A4"这条规则的**唯一**落地点：
   * 界面禁用 A5 选项只是提示，真正的保证在这里 —— 否则一旦
   * 有人从 localStorage 手改成 {size:'A5',photos:true}，
   * 或日后新增别的入口，就会绕过界面限制打出溢出的单子。
   */
  function effectivePrint(pref) {
    const size = pref?.size === PRINT_SIZES.A5 ? PRINT_SIZES.A5 : PRINT_SIZES.A4
    return {
      size,
      photos: size === PRINT_SIZES.A4,
      template: normalizePrintTemplate(pref?.template),
      forcedA4: false
    }
  }

  let st = null

  /** 当前报告单的普通位置数（排除对照行） */
  const posCount = () => st.rows.filter(r => r.control_type === 'NORMAL').length

  function blank() {
    return {
      id: null,
      status: 'DRAFT',
      patient: null,
      report_date: dayjs().format('YYYY-MM-DD'),
      symptoms: '',
      notes: '',
      template_id: null,
      template_name: null,
      template_deleted: false,
      doctor_id: '',

      /* ---- 纸质单头部字段（0006）：属于「本次开单」而不是「这个人」 ----
       * 同一患者不同次点刺的科室/申请医生/流水号/临床诊断都可能不同，
       * 存到 patient 上会被下一张单覆盖，历史单再打印就是错的。 */
      medical_record_no: '',
      applied_at: '',
      department: '',
      applying_doctor: '',
      serial_no: '',
      clinical_diagnosis: '',
      /* 年龄快照：HIS 屏幕上只有年龄没有出生日期，且报告单印的是**开单当天**的年龄。
       * 若每次打开都用出生日期现算，三年后重打这张单会印出 13 岁而不是当时的 10 岁。 */
      patient_age_snapshot: '',

      /* ---- 纸质单页脚字段 ---- */
      executed_at: '',
      reported_at: '',
      tester_name: '',        // 检验者：默认留空，纸上手签
      reviewer_id: '',
      reviewer_name: '',

      rows: buildEmptyRows(),
      photos: [],       // 已存服务端: {id, photo_url}
      newPhotos: [],    // 待上传: {data, device}
      ocrUnrecognized: [],
      staff: []
    }
  }

  function buildEmptyRows(count = DEFAULT_POS_COUNT) {
    const rows = []
    for (let i = 1; i <= count; i++) rows.push({ position_no: i, allergen_name: '', positive_area: '', negative_area: '', control_type: 'NORMAL' })
    rows.push({ position_no: POS_CTRL, allergen_name: '组胺', positive_area: '', negative_area: '', control_type: 'POSITIVE_CTRL' })
    rows.push({ position_no: NEG_CTRL, allergen_name: '生理盐水', positive_area: '', negative_area: '', control_type: 'NEGATIVE_CTRL' })
    return rows
  }

  const rowAt = (pos) => st.rows.find(r => r.position_no === pos)

  /* ============================ 渲染 ============================ */

  async function render(body, params = {}) {
    st = blank()
    // 先确保院内可编辑候选库已加载，否则首屏下拉会先用内置兜底库
    await ALLERGENS.ensure()
    try { st.staff = (await API.get('/api/reports/meta/staff')).data } catch { st.staff = [] }
    if (App.user.role === 'DOCTOR') st.doctor_id = App.user.id

    body.innerHTML = shell()
    bind(body)
    renderTable()
    renderPatient()
    renderSignoff()
    renderPhotos()

    /* dirty 是模块级变量，上一次进入报告单页留下的「未保存」状态会残留到下一次，
     * 导致刚打开一张干净的报告单就被离开守卫拦住。每次渲染必须归零。 */
    dirty = false

    /* 报告单是长表单，误触返回会丢掉整张录入结果，必须拦一次。
     * 守卫由 App 在真正切页前调用；返回 false 即取消离开。 */
    App.setLeaveGuard(async () => {
      if (!dirty) return true
      return await UI.confirm({
        title: '离开报告单',
        message: '当前报告单有<b>未保存</b>的修改，离开后这些内容将丢失。<br>建议先「保存草稿」再离开。',
        okText: '放弃修改并离开'
      })
    })

    if (params.id) await loadReport(params.id, params.readonly)
    else if (params.patient_id) await pickPatientById(params.patient_id)
  }

  function shell() {
    return `
    <div class="max-w-[1400px] mx-auto space-y-4">

      <!-- ===== 打印页眉（仅打印可见）=====
           白纸整页打印时纸上必须有医院名与报告单标题，否则不像正式报告单 -->
      <div class="print-head" id="print-head"></div>

      <!-- ===== 顶部工具条 ===== -->
      <div class="card p-4 flex flex-wrap items-center gap-3 no-print">
        <div class="flex items-center gap-2">
          <button id="rp-back" class="btn btn-ghost btn-sm" title="返回报告单列表"><i class="fas fa-arrow-left"></i>返回</button>
          <span class="h-9 w-9 rounded-lg bg-brand-50 text-brand-600 grid place-items-center"><i class="fas fa-file-medical"></i></span>
          <div>
            <p class="font-semibold text-ink-800 text-sm" id="rp-title">新建报告单</p>
            <p class="text-xs text-slate-400" id="rp-subtitle">填写患者信息与点刺结果</p>
          </div>
        </div>
        <div id="rp-status-area" class="flex items-center gap-2"></div>
        <div class="flex-1"></div>
        <label class="text-xs text-slate-500">报告日期</label>
        <input id="rp-date" type="date" class="field-input !w-auto !py-1.5 text-sm">
        <label class="text-xs text-slate-500 ml-2">开单医生</label>
        <select id="rp-doctor" class="field-input !w-auto !py-1.5 text-sm"></select>
        <button id="rp-new" class="btn btn-ghost btn-sm"><i class="fas fa-file-circle-plus"></i>新建</button>
      </div>

      <!-- ===== 4.1 头部：患者信息区 ===== -->
      <section class="card p-5" id="patient-section">
        <div class="flex items-center gap-3 mb-4">
          <h3 class="font-semibold text-ink-800 text-sm"><i class="fas fa-id-card text-brand-500 mr-2"></i>患者信息</h3>
          <div class="flex-1"></div>
          <div class="flex gap-2 no-print">
            <button id="btn-ocr" class="btn btn-primary btn-sm" title="把摄像头对准 HIS 申请单窗口拍照识别">
              <i class="fas fa-desktop"></i>识别申请单屏幕
            </button>
            <button id="btn-pick-patient" class="btn btn-ghost btn-sm"><i class="fas fa-magnifying-glass"></i>从患者库选择</button>
            <button id="btn-new-patient" class="btn btn-ghost btn-sm"><i class="fas fa-user-plus"></i>新建患者</button>
          </div>
        </div>
        <div id="patient-fields" class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3"></div>
        <!-- no-print：这是给操作者看的识别反馈（含"识别引擎：rapidocr"等技术信息），
             属于录入过程的交互提示，不是报告单内容，绝不能出现在打印/存档的纸质报告上。 -->
        <p id="ocr-hint" class="hidden no-print mt-3 text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2"></p>
      </section>

      <!-- ===== 4.2 中部：报告单内容区（严格复刻附件 2） ===== -->
      <section class="card overflow-hidden">
        <div class="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-slate-200 bg-slate-50">
          <h3 class="font-semibold text-ink-800 text-sm"><i class="fas fa-table-cells text-brand-500 mr-2"></i>过敏原点刺结果</h3>
          <span id="tpl-badge" class="hidden"></span>
          <div class="flex-1"></div>
          <div class="flex gap-2 no-print">
            <button id="btn-use-template" class="btn btn-primary btn-sm"><i class="fas fa-layer-group"></i>选用模版</button>
            <button id="btn-clear-results" class="btn btn-ghost btn-sm"><i class="fas fa-eraser"></i>清空结果</button>
            <button id="btn-clear-all" class="btn btn-ghost btn-sm"><i class="fas fa-trash-can"></i>清空全部</button>
          </div>
        </div>
        <div id="spt-table-wrap" class="p-5 overflow-x-auto"></div>
        <!-- rp-textareas：供打印样式定位。
             原先是「症状 | 备注」两列并排；2026-08 使用方要求去掉备注，
             现只剩症状一项，故不再需要两列网格，直接单列占满宽度。 -->
        <div class="rp-textareas px-5 pb-5">
          <div>
            <label class="field-label" for="rp-symptoms">症状</label>
            <textarea id="rp-symptoms" rows="2" class="field-input resize-y" placeholder="例：鼻塞、打喷嚏、皮肤瘙痒…"></textarea>
          </div>
        </div>
      </section>

      <!-- ===== 4.2b 报告单底部信息：执行/报告时间、检验者、审核者 =====
           位置在内容区与手臂照片之间，与纸质单一致（签名栏在结果表下方）。
           屏幕上是可编辑表单，纸上由 .print-foot 渲染成签名栏。 -->
      <section class="card p-5 no-print" id="signoff-section">
        <div class="flex items-center gap-3 mb-4">
          <h3 class="font-semibold text-ink-800 text-sm"><i class="fas fa-pen-nib text-brand-500 mr-2"></i>报告签发</h3>
          <div class="flex-1"></div>
          <button id="btn-now" class="btn btn-ghost btn-sm" title="报告时间取当前时间，执行时间自动倒推 20 分钟">
            <i class="fas fa-clock"></i>取当前时间</button>
        </div>
        <div id="signoff-fields" class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3"></div>
      </section>

      <!-- ===== 4.3 底部：手臂实验区照片 ===== -->
      <section class="card p-5" id="photo-section">
        <div class="flex items-center gap-3 mb-4">
          <h3 class="font-semibold text-ink-800 text-sm"><i class="fas fa-hand text-brand-500 mr-2"></i>手臂实验区照片
            <span class="text-xs font-normal text-slate-400 ml-1 no-print">（提交前至少 1 张）</span></h3>
          <div class="flex-1"></div>
          <button id="btn-measure" class="btn btn-primary btn-sm no-print"
                  title="自动检出所有风团并分级（全自动，无需手动操作）">
            <i class="fas fa-wand-magic-sparkles"></i>自动测量分级</button>
          <button id="btn-measure-semi" class="btn btn-ghost btn-sm no-print"
                  title="手动逐点点击风团，系统自动测量并计算等级（半自动）">
            <i class="fas fa-hand-pointer"></i>半自动测量</button>
          <button id="btn-upload-arm" class="btn btn-ghost btn-sm no-print"
                  title="从本机选择已拍好的照片（手机照片、既往留存图）。原始文件直传，不做任何压缩"><i class="fas fa-upload"></i>上传照片</button>
          <input id="arm-upload-input" type="file" accept="image/jpeg,image/png,image/webp" multiple class="hidden">
          <!-- 左右分开两个按钮，而不是一个「拍照」再弹窗问左右：
               少一步选择，且操作者点下去之前就已经确定了拍哪条臂，
               不会出现「先拍完再回想刚才那张是哪只手」的情况。
               颜色与 Capture.SIDES 保持一致（sky=左 / violet=右），
               取景界面与缩略图角标同色，靠颜色也能快速分辨。 -->
          <button id="btn-shoot-left" class="btn btn-sm no-print text-white bg-sky-600 hover:bg-sky-700"
                  title="拍摄患者左手臂的点刺区域"><i class="fas fa-camera"></i>左手臂</button>
          <button id="btn-shoot-right" class="btn btn-sm no-print text-white bg-violet-600 hover:bg-violet-700"
                  title="拍摄患者右手臂的点刺区域"><i class="fas fa-camera"></i>右手臂</button>
        </div>
        <div id="photo-grid" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3"></div>
      </section>

      <!-- ===== 打印页脚（仅打印可见）：签名栏 ===== -->
      <div class="print-foot" id="print-foot"></div>

      <div class="h-16 no-print"></div>
    </div>

    <!-- ===== 4.4 右下浮动操作栏 ===== -->
    <div class="fixed bottom-6 right-6 z-40 flex items-center gap-2 bg-white/95 backdrop-blur border border-slate-200 rounded-xl shadow-2xl px-3 py-2.5 no-print">
      <span id="rp-dirty" class="text-xs text-slate-400 px-1"></span>
      <!-- 打印设置：单独一个齿轮按钮，不做成打印前必弹的对话框 ——
           护士连续打同一批单子时，每次都被拦一次是纯粹的干扰；
           偏好已记住，默认直接打，要改才点齿轮。 -->
      <button id="btn-print-setup" class="btn btn-ghost btn-sm" title="打印版式设置（纸张尺寸 / 是否打印手臂照片）">
        <i class="fas fa-sliders"></i><span id="print-pref-tag" class="ml-1 text-xs"></span></button>
      <button id="btn-print" class="btn btn-ghost btn-sm"><i class="fas fa-print"></i>打印</button>
      <button id="btn-save-draft" class="btn btn-ghost btn-sm"><i class="fas fa-floppy-disk"></i>保存草稿</button>
      <button id="btn-submit" class="btn btn-success btn-sm"><i class="fas fa-paper-plane"></i>提交</button>
    </div>`
  }

  /* ---------------------- 患者信息区 ---------------------- */

  /* ---------------------- 患者信息区（4 列 + 备注） ----------------------
   *
   * 排布：一行 4 列，每格是「标签：值」左右并排（不是标签在上、值在下）。
   *   行1  姓名 | 性别 | 年龄 | 病历号（就诊卡号）
   *   行2  申请时间 | 科室 | 申请医生 | 流水号
   *   行3  临床诊断
   *   行4  备注（跨整行）
   *
   * 左右格式由 CSS 的 #patient-fields > div{display:flex} 实现，HTML 结构不变，
   * 这样屏幕与打印共用同一套结构，不必维护两份 DOM。
   *
   * 两类字段混在同一张表里，但归属不同，保存去向也不同：
   *   患者档案字段（name/gender/age/visit_card_no）→ PUT /api/patients/:id
   *   本次开单字段（病历号/申请时间/科室/申请医生/流水号/临床诊断）→ 存在报告单上
   * 病历号默认取患者就诊卡号，但允许改：这两者在部分医院并不是同一个号。
   */

  function renderPatient() {
    const p = st.patient
    const un = st.ocrUnrecognized
    const cls = (k) => un.includes(k) ? 'field-unrecognized' : (p?.__ocr?.includes(k) ? 'field-ocr-filled' : '')
    const v = (k) => UI.esc(p?.[k] ?? '')
    const f = (k) => UI.esc(st[k] ?? '')
    const dis = p ? '' : 'disabled'

    // 年龄：HIS 屏幕只给年龄不给生日，故年龄是可直接编辑的输入框，
    // 而不是像旧版那样由出生日期只读推算。有真实生日时仍以生日为准回填。
    const ageVal = p
      ? (p.birth_date && p.birth_date !== UI.AGE_ONLY_BIRTH_DATE
          ? String(UI.fmt.patientAge(p)).replace(' 岁', '')
          : (p.age_years ?? ''))
      : ''

    document.getElementById('patient-fields').innerHTML = `
      <div><label class="field-label">姓名</label>
        <input id="pf-name" class="field-input ${cls('name')}" value="${v('name')}" ${dis} placeholder="—"></div>
      <div><label class="field-label">性别</label>
        <select id="pf-gender" class="field-input ${cls('gender')}" ${dis}>
          <option value="UNKNOWN" ${p?.gender === 'UNKNOWN' ? 'selected' : ''}>未知</option>
          <option value="M" ${p?.gender === 'M' ? 'selected' : ''}>男</option>
          <option value="F" ${p?.gender === 'F' ? 'selected' : ''}>女</option>
        </select></div>
      <div><label class="field-label">年龄</label>
        <div class="pf-age-wrap flex items-center gap-1">
          <input id="pf-age" type="number" min="0" max="120" class="field-input ${cls('age_years')}"
                 value="${UI.esc(ageVal)}" ${dis} placeholder="—">
          <span class="text-xs text-slate-500 shrink-0">岁</span>
        </div></div>
      <div><label class="field-label">病历号</label>
        <input id="rf-mrn" class="field-input ${cls('visit_card_no')}" value="${f('medical_record_no')}" placeholder="—"></div>

      <div><label class="field-label">申请时间</label>
        <input id="rf-applied" type="date" class="field-input ${cls('applied_at')}" value="${f('applied_at')}"></div>
      <div><label class="field-label">科室</label>
        <input id="rf-dept" class="field-input ${cls('department')}" value="${f('department')}" placeholder="—"></div>
      <div><label class="field-label">申请医生</label>
        <input id="rf-appdoc" class="field-input ${cls('applying_doctor')}" value="${f('applying_doctor')}" placeholder="—"></div>
      <div><label class="field-label">流水号</label>
        <input id="rf-serial" class="field-input ${cls('serial_no')}" value="${f('serial_no')}" placeholder="—"></div>

      <!-- 临床诊断：跨整行。
           曾经是「临床诊断占两格 + 备注占整行」的上下两行布局，
           2026-08 使用方要求去掉备注字段，诊断随之独占一行 ——
           诊断常是一句话（含中括号的变应性鼻炎等），1/4 宽会被截断。 -->
      <div class="pf-diag col-span-2 md:col-span-4"><label class="field-label">临床诊断</label>
        <input id="rf-diag" class="field-input ${cls('clinical_diagnosis')}" value="${f('clinical_diagnosis')}" placeholder="—"></div>

      <div class="pf-actions col-span-2 md:col-span-4 flex items-center gap-2 no-print">
        ${p ? `<button id="pf-save" class="btn btn-ghost btn-sm"><i class="fas fa-check"></i>保存患者修改</button>
               <span class="text-xs text-slate-400">姓名 / 性别 / 年龄 / 就诊卡号写回患者档案；其余字段随本报告单保存</span>`
             : `<div class="text-xs text-slate-400">请先识别申请单屏幕或选择患者</div>`}
      </div>`

    // ---- 患者档案字段 ----
    if (p) {
      document.getElementById('pf-save').addEventListener('click', savePatientEdits)
      document.getElementById('pf-name').addEventListener('input', (e) => { st.patient.name = e.target.value; markDirty() })
      document.getElementById('pf-gender').addEventListener('change', (e) => { st.patient.gender = e.target.value; markDirty() })
      document.getElementById('pf-age').addEventListener('input', (e) => {
        const n = e.target.value === '' ? null : Number(e.target.value)
        st.patient.age_years = n
        // 手改年龄后，原先由生日推算出来的值就不再是「当前显示值」的来源了。
        // 把生日改成哨兵，否则下次 renderPatient 会用旧生日把手填的年龄覆盖回去。
        if (n !== null) st.patient.birth_date = UI.AGE_ONLY_BIRTH_DATE
        st.patient_age_snapshot = n === null ? '' : n + '岁'
        markDirty()
      })
    }

    // ---- 本次开单字段（不依赖是否已选患者，补录历史单时可先填）----
    const bindRf = (id, key) => {
      document.getElementById(id)?.addEventListener('input', (e) => { st[key] = e.target.value; markDirty() })
    }
    bindRf('rf-mrn', 'medical_record_no')
    bindRf('rf-dept', 'department')
    bindRf('rf-appdoc', 'applying_doctor')
    bindRf('rf-serial', 'serial_no')
    bindRf('rf-diag', 'clinical_diagnosis')
    document.getElementById('rf-applied')?.addEventListener('change', (e) => { st.applied_at = e.target.value; markDirty() })
    // 原先这里还有 #rf-remark（备注）与页面下方 #rp-notes 的双向同步。
    // 2026-08 使用方要求去掉备注字段，两处输入框均已移除，同步逻辑随之删除。
    // DB 的 spt_report.notes 列保留不动：SQLite 删列需重建表，风险大于收益；
    // 历史数据（若有）也不至于因为界面改版而丢失。
  }

  async function savePatientEdits() {
    if (!st.patient?.id) return
    try {
      UI.loading(true, '保存患者信息…')
      await API.put('/api/patients/' + st.patient.id, {
        name: st.patient.name, gender: st.patient.gender, birth_date: st.patient.birth_date,
        // 只有年龄没有生日的患者，年龄靠这个字段承载；后端 resolveAge 会据此写哨兵生日
        age_years: st.patient.age_years,
        visit_card_no: st.patient.visit_card_no, contact_person: st.patient.contact_person,
        contact_phone: st.patient.contact_phone
      })
      UI.toast('患者信息已更新', 'success')
      st.ocrUnrecognized = []
      renderPatient()
    } catch (e) { UI.toast(e.message, 'error') } finally { UI.loading(false) }
  }

  /* ---------------------- 报告单表格 ---------------------- */

  /* ---------------------- 报告单内容区（4 列 × N 行） ----------------------
   * 一个「格子」= 过敏原名称 + 序号 + 结果标记，对应纸质单上的一小格。
   * 与旧版双栏表的区别：旧版一行是 4 个**字段**（序号|名称|阳性|阴性），
   * 新版一行是 4 个**项目**，每个项目内部再分名称/序号/结果三小格。
   */

  /** 自动测量来源角标（保持原有语义，仅位置随新版式调整） */
  function measureTag(r) {
    if (!r.measure_source) return ''
    // 来源六态：AUTO / AUTO_EDITED / MANUAL / MANUAL_EDITED /
    //           ASSUMED_NEGATIVE / AUTO_NEGATIVE。
    // 手工测量不能说成「算法建议」——GPU 不可达时整张单子都是手工的。
    const manual = r.measure_source === 'MANUAL' || r.measure_source === 'MANUAL_EDITED'
    const edited = r.measure_source === 'AUTO_EDITED' || r.measure_source === 'MANUAL_EDITED'
    /* ASSUMED_NEGATIVE：阴性对照被「跳过即视为阴性」记了 0mm，没有任何人测过它。
     *
     * 必须单列一态、且必须排在最前：本函数原本用白名单判 manual，
     * 未知来源会默认落到「算法自动测量」——于是一个凭空假设的 0mm
     * 会在报告角标上呈现为「算法自动测量 平均径 D=0.0mm」，
     * 把假设伪装成实测结果。这比不显示角标危险得多，因为它看起来完全正常。
     * 后端 apply 已如实写入这个来源，此处若不同步，留痕就只到数据库为止。 */
    const assumed = r.measure_source === 'ASSUMED_NEGATIVE'
    /* AUTO_NEGATIVE：算法**确实测过**该位点（多种子共识 9 个种子全部
     * 未检出风团），据此自动判阴记 0mm。
     *
     * 与 ASSUMED_NEGATIVE 必须分开两态，措辞方向相反：
     *   ASSUMED_NEGATIVE「没测」—— 要提醒这是一个未经查看的假设；
     *   AUTO_NEGATIVE   「测了、没有」—— 是一条有依据的阴性结果。
     * 把后者说成前者会无谓贬低可信度，护士以为还得重测一遍，
     * 正好抵消自动判阴想省下的工作量。
     *
     * 必须显式列举：本函数用白名单判断，不认识的来源会默认落到
     * 「算法自动测量」分支 —— 于是 0mm 会被展示成
     * 「算法自动测量 平均径 D=0.0mm」，看起来像量出了 0。 */
    const autoNeg = r.measure_source === 'AUTO_NEGATIVE'
    const parts = [
      assumed
        ? '未实际测量：已跳过该位点，按默认视为阴性（0mm）'
        : autoNeg
          ? '算法自动测量：未检出风团，自动判阴（0mm）'
          : manual ? '手工测量（未使用算法）' : '算法自动测量'
    ]
    /* assumed 时**不列任何测量明细**。
     * 0mm 的三个径、方法 MANUAL、置信度都是为满足数据库一致性校验而填的占位值，
     * 把它们摊在角标上（「最长径 0.0 / 垂直径 0.0 mm」「方法 MANUAL」）
     * 会让人以为真有人拿尺子量到了 0 —— 恰好抹掉本条留痕想说的唯一一件事：
     * 没有人测过它。 */
    if (!assumed && !autoNeg) {
      if (r.d_max_mm != null && r.d_perp_mm != null) {
        parts.push(`最长径 ${Number(r.d_max_mm).toFixed(1)} / 垂直径 ${Number(r.d_perp_mm).toFixed(1)} mm`)
      }
      if (r.d_mean_mm != null) parts.push(`平均径 D=${Number(r.d_mean_mm).toFixed(1)}mm`)
      if (r.grade_ratio != null) parts.push(`比值 ${Number(r.grade_ratio).toFixed(2)}`)
      if (r.grade_suggested) parts.push(`${manual ? '按测量值建议' : '算法建议'} ${r.grade_suggested}`)
      if (edited && r.grade_confirmed && r.grade_confirmed !== r.grade_suggested) {
        parts.push(`已改为 ${r.grade_confirmed}`)
      }
      if (r.segment_method) parts.push(`方法 ${r.segment_method}`)
      if (r.measure_confidence != null) parts.push(`置信 ${Number(r.measure_confidence).toFixed(2)}`)
    }
    /* 图标一眼区分：尺子=测过有读数，人手=改过，问号=没测过按默认算，
     * 空心圈=测过但未检出风团（自动判阴）。
     * 自动判阴不能用问号（那是"没测"）也不能用尺子（那会暗示量到了 0）。 */
    const icon = assumed
      ? 'fa-circle-question'
      : edited
        ? 'fa-user-pen'
        : autoNeg ? 'fa-circle-minus' : 'fa-ruler-combined'
    return `<span class="measure-tag ${edited ? 'edited' : ''}${assumed ? ' assumed' : ''}${autoNeg ? ' auto-neg' : ''} no-print"
      title="${UI.esc(parts.join('\n'))}"><i class="fas ${icon}"></i></span>`
  }

  /** 单元格当前应显示的结果文本：阳性优先（同时有值时阳性才是临床结论） */
  function cellResult(r) {
    if (r.positive_area) return { text: r.positive_area, kind: 'pos' }
    if (r.negative_area) return { text: r.negative_area, kind: 'neg' }
    return { text: '', kind: '' }
  }

  /**
   * 「阳性对照」标签排成 2×2。
   * 曾经只靠 CSS 的 width:2em + word-break 让它自己折 —— 不可靠：
   * 序号列被 padding/边框吃掉一点宽度后，就退化成「一字一行」四行高，
   * 项目多、行高压到 28px 时四行塞不下，字会贴着上下边框。
   * 所以这里显式拆成两行，行数固定为 2，与行高无关。
   */
  function ctrlLabelHtml(label) {
    const a = UI.esc(label.slice(0, 2))
    const b = UI.esc(label.slice(2))
    return `<span class="spt-ctrl-label"><i>${a}</i><i>${b}</i></span>`
  }

  /** 一个项目格。r 为 null 时输出占位空格（用于把末行补满 4 列） */
  function cellHtml(r, opts = {}) {
    const { forPrint = false, ccch2 = false } = opts
    if (!r) {
      return `<div class="spt-cell is-pad" aria-hidden="true">
        <div class="spt-cell-no"></div><div class="spt-cell-name"></div><div class="spt-cell-res"></div>
      </div>`
    }
    const res = cellResult(r)
    const ctrl = r.control_type !== 'NORMAL'
    const label = r.control_type === 'POSITIVE_CTRL' ? '阳性对照'
      : r.control_type === 'NEGATIVE_CTRL' ? '阴性对照' : ''
    /* 对照行的序号 101/102 是内部位号，纸上不该印出来。
     * 这格空着正好用来放「阳性对照/阴性对照」标签 —— 原先标签和名称输入框
     * 挤在同一个名称小列里，1fr 宽度装不下，名称被压成「阳性对」这样的截断。 */
    /* 若护士把名称也填成了「阳性对照 7」这类字样，序号列就别再印一遍标签 ——
     * 同一行左右两格都写着「阳性对照」，纸上看着像填重了。名称列填的是实际
     * 试剂（组胺/生理盐水）时才需要序号列这个标签来标明它是对照行。 */
    const dupLabel = ctrl && label && (r.allergen_name || '').replace(/\s/g, '').startsWith(label)
    const noText = ctrl ? '' : r.position_no
    /* 小列顺序：序号 → 检测项目 → 结果（按用户要求调整）。
     * 注意：手上的纸质样单是「名称 → 序号 → 结果」，此处**有意不同** ——
     * 序号提到最左，是为了让护士按点刺板号顺读顺填，别照着纸质单改回去。 */
    const shownRes = (ccch2 && forPrint && !res.text) ? '—' : res.text
    const noCell = `<div class="spt-cell-no">${ctrl ? (dupLabel ? '' : ctrlLabelHtml(label)) : noText}</div>`
    const printName = (ccch2 && forPrint && ctrl && label)
      ? `${r.allergen_name || ''}${(r.allergen_name || '').trim() ? '（' + label + '）' : label}`
      : r.allergen_name
    const nameCell = `<div class="spt-cell-name">
        <input class="cell-input spt-name-input" data-f="allergen_name" data-p="${r.position_no}"
               value="${UI.esc(printName)}"
               placeholder="${ctrl ? (r.control_type === 'POSITIVE_CTRL' ? '组胺' : '生理盐水') : '过敏原名称'}">
      </div>`
    const resCell = `<div class="spt-cell-res" data-res="${r.position_no}" tabindex="0"
           title="点击选择结果：阳性 +/++/+++/++++，阴性 —">
        <span class="spt-res-text ${res.kind}">${UI.esc(shownRes)}</span>${measureTag(r)}
      </div>`
    return `<div class="spt-cell ${ctrl ? 'is-ctrl' : ''} ${res.kind === 'pos' ? 'is-pos' : res.kind === 'neg' ? 'is-neg' : ''}"
                 data-row="${r.position_no}">
      ${ccch2 && forPrint ? `${nameCell}${resCell}` : `${noCell}${nameCell}${resCell}`}
    </div>`
  }

  /**
   * @param {boolean} forPrint 打印态渲染：丢掉没填名称的空项目。
   *   屏幕上必须保留空项目（护士要往里填），但化验单上不能印 ——
   *   20 项里只填了 14 项时，纸上会多出 6 个空格子白占版面；
   *   更糟的是若某个空项目误点了结果（如只有「+++」没有名称），
   *   纸上就出现一个没有过敏原名称的阳性结果，这在临床上会被误读。
   */
  function renderTable(forPrint) {
    let normals = st.rows.filter(r => r.control_type === 'NORMAL').sort((a, b) => a.position_no - b.position_no)
    if (forPrint) normals = normals.filter(r => (r.allergen_name || '').trim())
    const ctrls = [rowAt(POS_CTRL), rowAt(NEG_CTRL)].filter(Boolean)

    const eff = effectivePrint(loadPrintPref())
    const template = normalizePrintTemplate(eff.template)
    const isCCCH2Print = !!forPrint && template === PRINT_TEMPLATES.CCCH_V2

    /* 实际列数。屏幕永远 4 列（对齐纸质单实物）；只有 A5 打印时降到 2 列。
     *
     * 【为什么 A5 必须降列，而不是靠 CSS 把 4 列压窄】
     * A5 可用宽仅 469px（A4 是 703px）。4 大列时每列 117px，
     * 减去序号 30px 与结果 42px，过敏原名称只剩 45px —— 装不下 4 个汉字。
     * 「点青霉」勉强，「交链孢霉」「屋尘螨提取物」直接被截断。
     * 一张看不出测了什么过敏原的报告单没有临床价值，所以宁可行数翻倍
     * （20 项从 5 行变 10 行，实测 A5 高度足够，行高约 36px）。
     *
     * 【为什么必须重渲而不能纯 CSS 切列数】
     * 三处结构与列数绑死，纯 CSS 改 grid-template-columns 会全部错乱：
     *   ① 表头是 4 组「序号/检测项目/结果」写在 HTML 里 —— 2 列时后两组会换行；
     *   ② 占位补齐按 4 的倍数算 —— 2 列时会多补出半行空格；
     *   ③ 上下键在同列移动用 GRID_COLS 做索引步长。
     * doPrint 本来就有"打印前重渲一次表格"的先例（丢弃空项目），沿用同一条路。 */
    const cols = (forPrint && eff.size === PRINT_SIZES.A5 && !isCCCH2Print)
      ? PRINT_COLS_A5 : GRID_COLS

    /* 序号横向排：顺序铺进网格，CSS grid 自然按行填充。
     *
     * 两处补空格，都是为了不出现「半行」——半行会让表格右侧缺一段边框，
     * 看上去像渲染坏了（实际是格数不满一行）：
     *  1) 普通项目凑满整行后，对照组才另起一行；
     *  2) 对照组只有 2 格，后面补格填满末行（2 列时正好满行，补 0 个）。
     * 空格用 null 占位，cellHtml 遇到 null 输出纯空格子。 */
    const padTo = (n) => (n % cols === 0 ? 0 : cols - (n % cols))
    let cells = [
      ...normals, ...Array(padTo(normals.length)).fill(null),
      ...ctrls, ...Array(padTo(ctrls.length)).fill(null)
    ]
    /* CCCH_V2 打印按医院纸单视觉基线至少占满 4 行（16 位）。
     * 项目不足时补空槽，仅占版不显示内容，避免中下部留白过大。 */
    if (isCCCH2Print && cells.length < 16) {
      cells = cells.concat(Array(16 - cells.length).fill(null))
    }

    /* 列数同时写进 class 与 CSS 变量：
     *   class 给打印 CSS 做选择器（.spt-cols-2 单独一套小列宽）；
     *   --spt-cols 让 grid-template-columns 能按实际列数 repeat()，
     *   避免"CSS 里写死 4、JS 却渲染了 2"这种必然错位的双份真相。 */
    document.getElementById('spt-table-wrap').innerHTML = `
      <div class="spt-grid4 spt-cols-${cols} ${isCCCH2Print ? 'spt-grid-ccch2' : ''}" style="--spt-cols:${cols}">
        <div class="spt-grid4-head">
          ${Array.from({ length: cols }, () => `<div class="spt-h">序号</div><div class="spt-h">检测项目</div><div class="spt-h">结果</div>`).join('')}
        </div>
        <div class="spt-grid4-body">${cells.map(c => cellHtml(c, { forPrint, ccch2: isCCCH2Print })).join('')}</div>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500 no-print">
        <button id="btn-add-pos" class="btn btn-xs btn-ghost" ${normals.length >= MAX_POS_COUNT ? 'disabled' : ''}>
          <i class="fas fa-plus"></i>增加一项</button>
        <button id="btn-del-pos" class="btn btn-xs btn-ghost" ${normals.length <= 1 ? 'disabled' : ''}>
          <i class="fas fa-minus"></i>删除末项</button>
        <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">共 ${normals.length} 项</span>
        <span class="w-px h-4 bg-slate-200 mx-1"></span>
        <i class="fas fa-lightbulb text-amber-400"></i>
        <span>点击「结果」格选择 + / ++ / +++ / ++++ 或阴性「—」；右键序号弹出快捷操作</span>
        <span class="ml-2 px-2 py-0.5 rounded bg-red-50 text-red-600 border border-red-100">阳性红底</span>
        <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">阴性灰底</span>
      </div>`

    // 传入实际列数：A5 是 2 列，用 4 去除会把行数算少一半，行高翻倍溢出纸外
    applyRowHeight(cells.length, cols)
    bindGridEvents(normals)
  }

  /**
   * 按「实际剩余空间」算打印行高，写成 --spt-row-h。
   *
   * 为什么不写死一个值：可用高度固定，行数却不是（20 项 6 行、30 项 8 行、
   * 上限 100 项）。固定 46px 在 30 项时会把末行挤出纸外（实测丢末项）。
   *
   * 为什么也不能用固定预算：上一版写死「留给表格 300px」，那是在**没有照片**
   * 的报告上估的。一旦贴了手臂照片（占 200px 上下），预算就超了，浏览器把行
   * 压扁往一页里塞，对照标签跟着被挤成一团 —— 用户打出来看到的正是这个。
   * 改为真去量：总高减去表格以外所有区块的实际占用，剩多少分多少。
   */
  function measureRowHeight(rows) {
    const wrap = document.querySelector('.spt-grid4')
    if (!wrap) return 40
    const card = wrap.closest('.card') || wrap.parentElement
    const page = card && card.parentElement
    let used = 0
    if (page) {
      /* 累加「表格之外、且纸上真会出现」的区块高度。
       *
       * 两类必须排除，否则会把可用高度算少、白白把行压扁：
       *  1) .no-print / #signoff-section 等只在屏幕上存在的区块（工具栏、
       *     屏幕签发区、底部占位）—— 本函数可能在屏幕媒体下被调用，
       *     那时 getComputedStyle 读不到 @media print 里的 display:none，
       *     光看 display 会把它们当成占版面的（实测多算了 300px 以上）。
       *  2) 打印时被 JS 临时隐藏的空症状/空备注/无照片区（.print-empty）。 */
      for (const el of page.children) {
        if (el === card) continue
        if (el.classList.contains('no-print')) continue
        if (el.classList.contains('print-empty')) continue
        if (el.id === 'signoff-section') continue
        if (getComputedStyle(el).display === 'none') continue
        used += el.getBoundingClientRect().height
      }
    }
    const head = wrap.querySelector('.spt-grid4-head')
    const headH = head ? head.getBoundingClientRect().height : 30
    // 打印页脚 #print-foot 屏幕上是隐藏的，但纸上要占位，按实测高度预留
    const foot = document.getElementById('print-foot')
    const footH = foot ? (foot.scrollHeight || 60) : 60
    // 24px 余量：卡片自身的 padding/圆角与浏览器分页时的取整误差
    /* 纸高按当前生效版式取：A5 只有 A4 的约 68%（703 vs 1032），
     * 继续用 A4 的高度去分行高，会算出一个 A5 装不下的值，
     * 末行被挤到第 2 页 —— 正是 A4 时代那个"丢末项"的老 bug 换张纸重演。 */
    const bodyPx = effectivePrint(loadPrintPref()).size === PRINT_SIZES.A5 ? A5_BODY_PX : A4_BODY_PX
    const avail = bodyPx - used - headH - footH - 24
    return Math.floor(avail / Math.max(1, rows))
  }

  function applyRowHeight(cellCount, cols) {
    const wrap = document.querySelector('.spt-grid4')
    if (!wrap) return
    const rows = Math.max(1, Math.ceil(cellCount / (cols || GRID_COLS)))
    /* 夹在 [下限, 52]：上限避免项目很少时行高夸张到像表格被拉伸变形。
     *
     * 下限随纸张变：A4 是 28px（对照标签两行需要 ~19px 加内边距）。
     * 但 A5 可用高度只有 703px，20 项 5 行时若仍守 28px 下限，
     * 遇到照片/长诊断把其它区块撑高的情况就会突破纸高。
     * A5 放宽到 22px：这是"挤但仍能看清"与"溢出到第二页"之间的取舍，
     * 而 A5 本来就是给不带照片的精简存档用的，字小一点可接受。 */
    const isA5 = effectivePrint(loadPrintPref()).size === PRINT_SIZES.A5
    const h = Math.max(isA5 ? 22 : 28, Math.min(52, measureRowHeight(rows)))
    wrap.style.setProperty('--spt-row-h', h + 'px')
  }

  function bindGridEvents(normals) {
    document.getElementById('btn-add-pos')?.addEventListener('click', () => {
      const n = posCount()
      if (n >= MAX_POS_COUNT) return
      const ctrls = st.rows.filter(r => r.control_type !== 'NORMAL')
      st.rows = [...normals, { position_no: n + 1, allergen_name: '', positive_area: '', negative_area: '', control_type: 'NORMAL' }, ...ctrls]
      renderTable(); markDirty()
    })
    document.getElementById('btn-del-pos')?.addEventListener('click', async () => {
      const n = posCount()
      if (n <= 1) return
      const last = normals[normals.length - 1]
      if (last.allergen_name || last.positive_area || last.negative_area) {
        const ok = await UI.confirm({
          title: '删除末项',
          message: `第 <b>${last.position_no}</b> 项已有数据（${UI.esc(last.allergen_name || '无名称')}），确认删除？`,
          okText: '删除'
        })
        if (!ok) return
      }
      const ctrls = st.rows.filter(r => r.control_type !== 'NORMAL')
      st.rows = [...normals.slice(0, -1), ...ctrls]
      renderTable(); markDirty()
    })

    // 名称输入
    document.querySelectorAll('#spt-table-wrap .spt-name-input').forEach(inp => {
      inp.addEventListener('input', (e) => {
        const r = rowAt(Number(e.target.dataset.p))
        if (r) { r.allergen_name = e.target.value; markDirty() }
      })
      // 上下键在同列移动：4 列网格里「同列」= 索引相差 4
      inp.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
        if (document.querySelector('.cbx-panel')) return   // 候选下拉正在用方向键
        e.preventDefault()
        const all = [...document.querySelectorAll('#spt-table-wrap .spt-name-input')]
        const i = all.indexOf(e.target)
        /* 这里固定用 GRID_COLS 是对的：方向键只在屏幕上有意义，
         * 而屏幕永远是 4 列（A5 的 2 列只存在于打印那一瞬间，
         * 且打印期间没有键盘交互）。不要改成动态列数 —— 那会让
         * 这段依赖一个此刻必然等于 4 的值，反而更难读。 */
        const next = all[e.key === 'ArrowUp' ? i - GRID_COLS : i + GRID_COLS]
        next?.focus()
      })
      const p = Number(inp.dataset.p)
      const groups = p === POS_CTRL ? () => ALLERGENS.CONTROL_POSITIVE
        : p === NEG_CTRL ? () => ALLERGENS.CONTROL_NEGATIVE
        : () => ALLERGENS.GROUPS
      UI.combobox(inp, {
        groups,
        onPick: (val) => { const r = rowAt(p); if (r) { r.allergen_name = val; markDirty() } }
      })
    })

    // 结果格：点击弹出分级选择
    document.querySelectorAll('#spt-table-wrap .spt-cell-res').forEach(el => {
      const open = (e) => {
        e.preventDefault()
        if (el.classList.contains('locked')) return
        const rect = el.getBoundingClientRect()
        resultMenu(Number(el.dataset.res), rect.left, rect.bottom + 4)
      }
      el.addEventListener('click', open)
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') open(e) })
    })

    // 序号格右键 / 点击 → 行快捷操作
    document.querySelectorAll('#spt-table-wrap .spt-cell-no').forEach(el => {
      const pos = Number(el.closest('.spt-cell').dataset.row)
      el.style.cursor = 'context-menu'
      el.addEventListener('contextmenu', (e) => { e.preventDefault(); rowMenu(pos, e.clientX, e.clientY) })
      el.addEventListener('click', () => rowMenuAt(pos, el))
    })
  }

  /**
   * 结果选择浮层：+ / ++ / +++ / ++++ / 阴性「—」/ 清除。
   *
   * 为什么做成浮层而不是下拉 select：纸质单上这一格是手写的，护士的动作是
   * 「看一眼风团、点一下」。select 需要点开→滚动→再点，多一步；而且 select
   * 在打印时会渲染成带箭头的控件，还得额外处理。浮层点完即走，打印时不存在。
   */
  function resultMenu(pos, x, y) {
    document.querySelectorAll('.row-menu,.res-menu').forEach(m => m.remove())
    const r = rowAt(pos)
    if (!r) return
    const menu = document.createElement('div')
    menu.className = 'res-menu fixed z-[9500] bg-white border border-slate-200 rounded-lg shadow-xl p-2 text-sm'
    menu.style.left = Math.min(x, window.innerWidth - 190) + 'px'
    menu.style.top = Math.min(y, window.innerHeight - 150) + 'px'
    menu.innerHTML = `
      <p class="text-[11px] text-slate-400 px-1 pb-1.5">${UI.esc(r.allergen_name || '第 ' + pos + ' 项')} · 选择结果</p>
      <div class="grid grid-cols-4 gap-1 mb-1.5">
        ${POS_GRADES.map(g => `<button data-g="${g}" class="res-btn ${r.positive_area === g ? 'active' : ''}">${g}</button>`).join('')}
      </div>
      <div class="grid grid-cols-2 gap-1">
        <button data-neg class="res-btn neg ${r.negative_area ? 'active' : ''}">阴性 ${NEG_MARK}</button>
        <button data-clr class="res-btn clr">清除</button>
      </div>`
    document.body.appendChild(menu)
    const close = () => menu.remove()
    setTimeout(() => document.addEventListener('click', close, { once: true }), 0)
    menu.addEventListener('click', (e) => e.stopPropagation())

    const apply = (fn) => { fn(); close(); renderTable(); markDirty() }
    menu.querySelectorAll('[data-g]').forEach(b => b.addEventListener('click', () => apply(() => {
      // 阳性与阴性互斥：一格只能有一个结论，留着另一个会让打印出的单子自相矛盾
      r.positive_area = b.dataset.g
      r.negative_area = ''
    })))
    menu.querySelector('[data-neg]').addEventListener('click', () => apply(() => {
      r.negative_area = NEG_MARK
      r.positive_area = ''
    }))
    menu.querySelector('[data-clr]').addEventListener('click', () => apply(() => {
      r.positive_area = ''
      r.negative_area = ''
    }))
  }

  /* 旧版 refreshRowHighlight() 已删除：
   * 它按 `tr[data-row]` + `.ctrl-row` 找行来切 row-positive/row-negative，
   * 而新版 4 列网格里根本没有 <tr>，函数会静默地一行都命中不到。
   * 现在底色由 cellHtml() 直接输出 is-pos / is-neg 类，随 renderTable 一次成型，
   * 不再需要渲染后的二次「补刷」这一步（那一步正是旧版漏刷的来源）。 */

  function rowMenuAt(pos, anchor) {
    const rect = anchor.getBoundingClientRect()
    rowMenu(pos, rect.right + 4, rect.top)
  }

  function rowMenu(pos, x, y) {
    document.querySelectorAll('.row-menu').forEach(m => m.remove())
    const menu = document.createElement('div')
    menu.className = 'row-menu fixed z-[9500] bg-white border border-slate-200 rounded-lg shadow-xl py-1 text-sm w-40'
    menu.style.left = Math.min(x, window.innerWidth - 170) + 'px'
    menu.style.top = Math.min(y, window.innerHeight - 140) + 'px'
    menu.innerHTML = `
      <button data-a="pos" class="w-full text-left px-3 py-1.5 hover:bg-red-50 text-red-600"><i class="fas fa-plus-circle mr-2"></i>本项阳性 +</button>
      <button data-a="neg" class="w-full text-left px-3 py-1.5 hover:bg-slate-50 text-slate-600"><i class="fas fa-minus-circle mr-2"></i>本项阴性 ${NEG_MARK}</button>
      <button data-a="clr" class="w-full text-left px-3 py-1.5 hover:bg-slate-50"><i class="fas fa-eraser mr-2"></i>清除本项</button>`
    document.body.appendChild(menu)
    const close = () => menu.remove()
    setTimeout(() => document.addEventListener('click', close, { once: true }), 0)
    menu.querySelectorAll('[data-a]').forEach(b => b.addEventListener('click', () => {
      const r = rowAt(pos); if (!r) return
      const a = b.dataset.a
      // 快捷键写入的标记必须与弹层选出来的是同一套符号。
      // 旧版这里写 '√'，与纸质单的 +/— 不是一套写法，同一张单上会混出两种记法。
      if (a === 'pos') { r.positive_area = POS_GRADES[0]; r.negative_area = '' }
      if (a === 'neg') { r.negative_area = NEG_MARK; r.positive_area = '' }
      if (a === 'clr') { r.positive_area = ''; r.negative_area = ''; if (r.control_type === 'NORMAL') r.allergen_name = '' }
      close(); renderTable(); markDirty()
    }))
  }

  /* ---------------------- 报告签发区（执行/报告时间、检验者、审核者） ----------------------
   *
   * 时间规则（来自现场作业流程）：
   *   报告时间 = 出报告的当下
   *   执行时间 = 报告时间 − 20 分钟（点刺后需观察 20 分钟才判读）
   * 两者都可手改：补录历史单时，实际间隔未必刚好 20 分钟，
   * 强制锁死会逼着操作者去改系统时间，那比允许手改危险得多。
   *
   * 检验者：**留空**，纸上手签。不预填当前账号——实际执行点刺的人常常
   * 不是登录这台电脑的人，预填等于替别人签了字。
   * 审核者：显示当前登录账号，纸上仍留手签区（电子名 + 手签双轨）。
   */

  /** 'YYYY-MM-DD HH:MM' —— 与后端 normMinute 同一口径（本地时间，不带时区） */
  function nowMinute() { return dayjs().format('YYYY-MM-DD HH:mm') }

  /**
   * 报告时间前推 20 分钟。前端算一遍是为了让操作者当场看见值，
   * 后端 minus20() 仍会兜底（旧页面/脚本调用不会漏填）。
   *
   * 这里不用 dayjs(s, 'YYYY-MM-DD HH:mm')：页面只引了 dayjs 核心库，
   * 没有 customParseFormat 插件，带格式串的第二个参数会被**静默忽略**，
   * 退化成 Date 解析——不报错，只是在某些输入上给出错误结果。
   * 改成 ISO 形式 'YYYY-MM-DDTHH:mm'，核心库与 Date 都按本地时间稳定解析。
   */
  function minus20Local(reported) {
    const s = String(reported || '').replace(' ', 'T').slice(0, 16)
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s)) return ''
    const d = dayjs(s)
    if (!d.isValid()) return ''
    return d.subtract(20, 'minute').format('YYYY-MM-DD HH:mm')
  }

  /** datetime-local 输入框要求 'YYYY-MM-DDTHH:MM'，与存储用的空格分隔互转 */
  const toLocalInput = (s) => (s || '').replace(' ', 'T').slice(0, 16)
  const fromLocalInput = (s) => (s || '').replace('T', ' ').slice(0, 16)

  function renderSignoff() {
    const box = document.getElementById('signoff-fields')
    if (!box) return
    const meName = App.user?.real_name || App.user?.username || ''

    box.innerHTML = `
      <div><label class="field-label">执行时间</label>
        <input id="sf-exec" type="datetime-local" class="field-input" value="${toLocalInput(st.executed_at)}">
        <p class="text-[11px] text-slate-400 mt-1">默认为报告时间前 20 分钟</p></div>
      <div><label class="field-label">报告时间</label>
        <input id="sf-report" type="datetime-local" class="field-input" value="${toLocalInput(st.reported_at)}"></div>
      <div><label class="field-label">检验者</label>
        <input id="sf-tester" class="field-input" value="${UI.esc(st.tester_name ?? '')}" placeholder="留空，纸上手签">
        <p class="text-[11px] text-slate-400 mt-1">默认留空，由执行人在纸上手签</p></div>
      <div><label class="field-label">审核者</label>
        <input class="field-input bg-slate-50" value="${UI.esc(st.reviewer_name || meName)}" disabled>
        <p class="text-[11px] text-slate-400 mt-1">
          ${st.reviewer_name ? '已审核' : '提交时记为当前账号'}：${UI.esc(meName)}，纸上另有手签区</p></div>`

    document.getElementById('sf-report').addEventListener('change', (e) => {
      st.reported_at = fromLocalInput(e.target.value)
      // 报告时间一改，执行时间跟着走 —— 除非操作者已经单独改过执行时间。
      // 用 __execTouched 记住这件事：不记的话，操作者辛苦改好的执行时间
      // 会在下一次动报告时间时被悄悄改回 −20 分钟。
      if (!st.__execTouched) {
        st.executed_at = minus20Local(st.reported_at)
        document.getElementById('sf-exec').value = toLocalInput(st.executed_at)
      }
      markDirty()
    })
    document.getElementById('sf-exec').addEventListener('change', (e) => {
      st.executed_at = fromLocalInput(e.target.value)
      st.__execTouched = true
      markDirty()
    })
    document.getElementById('sf-tester').addEventListener('input', (e) => { st.tester_name = e.target.value; markDirty() })
  }

  /** 「取当前时间」：报告时间 = 现在，执行时间 = 现在 − 20 分钟（并重置手改标记） */
  function fillNow() {
    st.reported_at = nowMinute()
    st.executed_at = minus20Local(st.reported_at)
    st.__execTouched = false
    renderSignoff()
    markDirty()
    UI.toast('已填入报告时间，执行时间自动倒推 20 分钟', 'success')
  }

  /* ---------------------- 打印页眉 / 页脚 ----------------------
   * 只在打印时可见（CSS .print-head/.print-foot 默认 display:none）。
   * 屏幕上这些信息分散在工具条和各区块里，但纸上必须自成一体：
   * 医院名、报告单标题、报告日期/项目数，以及底部签名栏。
   * 每次打印前重新生成——医院名、患者、日期、医生、项目数都可能刚被改过。 */

  function renderPrintParts() {
    const head = document.getElementById('print-head')
    const foot = document.getElementById('print-foot')
    if (!head || !foot) return

    const eff = effectivePrint(loadPrintPref())
    const template = normalizePrintTemplate(eff.template)

    const p = st.patient || {}
    const hospital = App.user?.hospital_name || ''
    const doctorName = st.staff.find(s => s.id === st.doctor_id)?.real_name || ''
    /* 只数「填了名称」的项目：表格打印时会摘掉空项目（见 renderTable(true)），
     * 若这里仍数全部 NORMAL 行，就会出现表头写「20 项」、纸上只印 14 项的自相
     * 矛盾 —— 化验单上的项目数是要给临床看的数字，必须与纸上实际条目一致。 */
    const n = st.rows.filter(r => r.control_type === 'NORMAL'
      && (r.allergen_name || '').trim()).length
    const statusText = st.status === 'DRAFT' ? '草稿（未提交）'
      : st.status === 'SUBMITTED' ? '已提交' : st.status === 'ARCHIVED' ? '已归档' : ''

    if (template === PRINT_TEMPLATES.CCCH_V2) {
      const genderText = p.gender === 'M' ? '男' : p.gender === 'F' ? '女' : ''
      const ageText = (st.patient_age_snapshot || (p.age_years ? `${p.age_years}岁` : '') || '').toString()
      head.innerHTML = `
        <div class="ccch2-head">
          <div class="ccch2-topline">
            <div class="ccch2-outpatient">门诊</div>
            <div class="ccch2-brand">✚</div>
            <div class="ccch2-hospital">
              <div>吉林省儿童医疗中心</div>
              <div>长春市儿童医院</div>
            </div>
            <div class="ccch2-title">过敏原检测报告单（吸入组）</div>
          </div>
          <div class="ccch2-meta">
            <div><span>姓名：</span>${UI.esc(p.name || '')}</div>
            <div><span>病历号：</span>${UI.esc(st.medical_record_no || '')}</div>
            <div><span>申请时间：</span>${UI.esc(st.applied_at || st.report_date || '')}</div>
            <div class="ccch2-remark"><span>备注：</span>${UI.esc(st.remarks || '')}</div>
            <div><span>性别：</span>${UI.esc(genderText)}</div>
            <div><span>科室：</span>${UI.esc(st.department || '')}</div>
            <div><span>申请医生：</span>${UI.esc(st.applying_doctor || doctorName || '')}</div>
            <div><span>年龄：</span>${UI.esc(ageText)}</div>
            <div><span>流水号：</span>${UI.esc(st.serial_no || '')}</div>
            <div class="ccch2-diag"><span>临床诊断：</span>${UI.esc(st.clinical_diagnosis || st.symptoms || '')}</div>
          </div>
        </div>`

      const reviewerText = st.reviewer_name || App.user?.real_name || App.user?.username || ''
      foot.innerHTML = `
        <div class="ccch2-foot">
          <div class="ccch2-foot-line"></div>
          <div class="ccch2-foot-row">
            <span>执行时间：${UI.esc(st.executed_at || '')}</span>
            <span>报告时间：${UI.esc(st.reported_at || '')}</span>
            <span class="ccch2-foot-sig">检验者：<u></u></span>
            <span class="ccch2-foot-sig">审核者：<u>${UI.esc(reviewerText || '')}</u></span>
          </div>
        </div>`
      return
    }

    if (template === PRINT_TEMPLATES.CCCH) {
      const genderText = p.gender === 'M' ? '男' : p.gender === 'F' ? '女' : ''
      const ageText = (st.patient_age_snapshot || (p.age_years ? `${p.age_years}岁` : '') || '').toString()
      head.innerHTML = `
        <div class="ccch-head">
          <div class="ccch-head-top">
            <div class="ccch-outpatient">门诊</div>
            <div class="ccch-brand">✚</div>
            <div class="ccch-hospital">
              <div>吉林省儿童医疗中心</div>
              <div>长春市儿童医院</div>
            </div>
            <div class="ccch-report-title">过敏原检测报告单（食入组）</div>
          </div>
          <div class="ccch-patient-meta">
            <div><span>姓名：</span>${UI.esc(p.name || '')}</div>
            <div><span>病历号：</span>${UI.esc(st.medical_record_no || '')}</div>
            <div><span>申请时间：</span>${UI.esc(st.applied_at || st.report_date || '')}</div>
            <div><span>性别：</span>${UI.esc(genderText)}</div>
            <div><span>科室：</span>${UI.esc(st.department || '')}</div>
            <div><span>申请医生：</span>${UI.esc(st.applying_doctor || doctorName || '')}</div>
            <div><span>年龄：</span>${UI.esc(ageText)}</div>
            <div><span>流水号：</span>${UI.esc(st.serial_no || '')}</div>
            <div class="ccch-diag"><span>临床诊断：</span>${UI.esc(st.clinical_diagnosis || st.symptoms || '')}</div>
          </div>
        </div>`

      foot.innerHTML = `
        <div class="ccch-foot">
          <div class="ccch-foot-line"></div>
          <div class="ccch-foot-row">
            <span>执行时间：${UI.esc(st.executed_at || '')}</span>
            <span>报告时间：${UI.esc(st.reported_at || '')}</span>
            <span>检验者：________________</span>
            <span>审核者：________________</span>
          </div>
        </div>`
      return
    }

    head.innerHTML = `
      <div style="text-align:center;border-bottom:2px solid #000;padding-bottom:6px;">
        ${hospital ? `<div style="font-size:15px;letter-spacing:.08em;">${UI.esc(hospital)}</div>` : ''}
        <div style="font-size:20px;font-weight:700;letter-spacing:.12em;margin-top:2px;">
          过敏原皮肤点刺实验报告单</div>
        <div style="display:flex;justify-content:space-between;font-size:11px;margin-top:6px;">
          <span>报告日期：${UI.esc(st.report_date || '')}</span>
          <span>检测项目：${n} 项</span>
          <span>${statusText ? '状态：' + statusText : ''}</span>
        </div>
      </div>`

    const sigLine = '<span style="display:inline-block;min-width:22mm;border-bottom:1px solid #000;">&nbsp;</span>'
    const cell = (label, value, withSign) => `
      <div style="display:flex;align-items:flex-end;gap:4px;white-space:nowrap;">
        <span>${label}：</span>
        ${value ? `<span>${UI.esc(value)}</span>` : ''}
        ${withSign ? sigLine : (value ? '' : sigLine)}
      </div>`

    foot.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px 10px;
                  border-top:1px solid #000;padding-top:10px;padding-right:8mm;font-size:12px;">
        ${cell('执行时间', st.executed_at || '', false)}
        ${cell('报告时间', st.reported_at || '', false)}
        ${cell('检验者', st.tester_name || '', true)}
        ${cell('审核者', st.reviewer_name || App.user?.real_name || App.user?.username || '', true)}
      </div>
      <div style="font-size:10px;color:#333;margin-top:6px;">
        本报告单仅反映本次皮肤点刺实验结果，请结合临床综合判断。${doctorName ? ` 开单医生：${UI.esc(doctorName)}` : ''}
      </div>`
  }

  /**
   * 打印：白纸整页打印，表格按实际项目数自然延伸、跨页时重复表头。
   *
   * 症状/备注为什么要换成 div 而不是撑高 textarea：
   * textarea 的可视高度受 rows=2 约束，靠 JS 设 scrollHeight 撑高看似可行，但
   * scrollHeight 是在**屏幕媒体**下量的，而打印媒体的字号/行高/padding 都不同
   * （打印样式里字更小、行高 1.5），量出来的高度偏小，最后一行仍会被下边框压掉半个字。
   * 换成一个 div 渲染同样的文本，高度完全由内容决定，任意长度都不会截断。
   */
  /**
   * 动态注入 @page 规则来切换纸张尺寸。
   *
   * 为什么不写在 style.css 里用选择器切：@page 不参与 DOM 级联，
   * `html[data-print-size=A5] @page{}` 这种写法根本不存在；纯 CSS 的
   * 唯一办法是命名页（@page a5{} + html{page:a5}），而 Firefox 不支持
   * page 属性。打错一次就是一张废纸加一次返工，不赌浏览器特性。
   *
   * A4 也显式注入（而不是"A4 就什么都不做、靠 style.css 缺省"）：
   * 那样一旦上一次打印留下了 A5 的注入节点没清掉，A4 就会被 A5 覆盖。
   * 每次都写明当前尺寸，状态是自洽的，不依赖清理逻辑的正确性。
   */
  const PAGE_STYLE_ID = 'spt-page-size-style'
  function applyPageSize(size) {
    let el = document.getElementById(PAGE_STYLE_ID)
    if (!el) {
      el = document.createElement('style')
      el.id = PAGE_STYLE_ID
      document.head.appendChild(el)
    }
    const s = size === PRINT_SIZES.A5 ? 'A5' : 'A4'
    const orient = size === PRINT_SIZES.A5 ? 'landscape' : 'portrait'; el.textContent = `@media print{@page{size:${s} ${orient};margin:12mm}}`
  }

  /** 在打印按钮旁显示当前版式，让人不点开也知道会打成什么样 */
  function renderPrintPrefTag() {
    const el = document.getElementById('print-pref-tag')
    if (!el) return
    const eff = effectivePrint(loadPrintPref())
    el.textContent = `${eff.size}${eff.photos ? '·含照片' : '·无照片'}·${printTemplateLabel(eff.template)}`
  }

  /**
   * 打印版式设置面板。
   *
   * 交互上刻意把「是否打印照片」放在前面：它决定了尺寸是否可选，
   * 先问尺寸再告诉用户"你刚选的不生效"是很差的顺序。
   */
  async function openPrintSetup() {
    const cur = loadPrintPref()
    const wrap = document.createElement('div')
    wrap.className = 'fixed inset-0 z-50 bg-black/40 grid place-items-center p-4'
    wrap.innerHTML = `
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
        <div class="px-5 py-3 border-b border-slate-200 flex items-center gap-2">
          <i class="fas fa-print text-brand-500"></i>
          <p class="font-semibold text-ink-800 text-sm">打印版式设置</p>
        </div>
        <div class="p-5 space-y-4">
          <div>
            <p class="text-xs font-semibold text-slate-500 mb-2">手臂实验区照片</p>
            <div class="flex gap-2">
              <button data-ph="1" class="btn btn-sm flex-1 ${cur.photos ? 'btn-primary' : 'btn-ghost'}">
                <i class="fas fa-image"></i>打印照片</button>
              <button data-ph="0" class="btn btn-sm flex-1 ${cur.photos ? 'btn-ghost' : 'btn-primary'}">
                <i class="fas fa-ban"></i>不打印照片</button>
            </div>
          </div>
          <div>
            <p class="text-xs font-semibold text-slate-500 mb-2">纸张尺寸</p>
            <div class="flex gap-2">
              <button data-sz="A4" class="btn btn-sm flex-1">A4</button>
              <button data-sz="A5" class="btn btn-sm flex-1">A5</button>
            </div>
            <p id="pp-note" class="mt-2 text-xs leading-relaxed"></p>
          </div>
          <div>
            <p class="text-xs font-semibold text-slate-500 mb-2">报告单模板</p>
            <div class="grid grid-cols-3 gap-2">
              <button data-tpl="STANDARD" class="btn btn-sm">标准模板</button>
              <button data-tpl="CCCH_V2" class="btn btn-sm">长春儿医定制模板</button>
              <button data-tpl="CCCH" class="btn btn-sm">长春儿医模板（旧）</button>
            </div>
          </div>
        </div>
        <div class="px-5 py-3 bg-slate-50 flex justify-end gap-2">
          <button data-act="cancel" class="btn btn-ghost btn-sm">取消</button>
          <button data-act="ok" class="btn btn-primary btn-sm">确定</button>
        </div>
      </div>`
    document.body.appendChild(wrap)

    const draft = {
      size: cur.size,
      photos: cur.photos,
      template: normalizePrintTemplate(cur.template)
    }
    const paint = () => {
      wrap.querySelectorAll('[data-ph]').forEach(b => {
        const on = (b.dataset.ph === '1') === (draft.photos === true)
        b.className = 'btn btn-sm flex-1 ' + (on ? 'btn-primary' : 'btn-ghost')
      })
      /* 打印照片时 A5 不可选：按钮置灰 + 明说原因。
       * 只置灰不解释的话，用户会以为是 bug。 */
      wrap.querySelectorAll('[data-sz]').forEach(b => {
        const isA5 = b.dataset.sz === 'A5'
        const disabled = draft.photos === true && isA5
        b.disabled = disabled
        const on = !disabled && draft.size === b.dataset.sz
        b.className = 'btn btn-sm flex-1 ' + (on ? 'btn-primary' : 'btn-ghost')
          + (disabled ? ' opacity-40 cursor-not-allowed' : '')
      })
      wrap.querySelectorAll('[data-tpl]').forEach(b => {
        const on = draft.template === b.dataset.tpl
        b.className = 'btn btn-sm flex-1 ' + (on ? 'btn-primary' : 'btn-ghost')
      })
      const note = wrap.querySelector('#pp-note')
      if (draft.photos) {
        note.className = 'mt-2 text-xs leading-relaxed text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5'
        note.innerHTML = '打印照片时固定 <b>A4</b>：A5 纸装不下表格再加照片，会溢出到第 2 页。'
      } else {
        note.className = 'mt-2 text-xs leading-relaxed text-slate-500'
        note.textContent = '不打印照片时 A4 / A5 均可。A5 更省纸，适合病历存档。'
      }
    }
    paint()

    return new Promise(resolve => {
      const close = (v) => { wrap.remove(); resolve(v) }
      wrap.addEventListener('click', (e) => {
        if (e.target === wrap) return close(false)
        const ph = e.target.closest('[data-ph]')
        if (ph) { draft.photos = ph.dataset.ph === '1'; paint(); return }
        const sz = e.target.closest('[data-sz]')
        if (sz && !sz.disabled) { draft.size = sz.dataset.sz; paint(); return }
        const tpl = e.target.closest('[data-tpl]')
        if (tpl) { draft.template = normalizePrintTemplate(tpl.dataset.tpl); paint(); return }
        const act = e.target.closest('[data-act]')
        if (!act) return
        if (act.dataset.act === 'ok') {
          savePrintPref({ size: draft.size, photos: draft.photos, template: draft.template })
          renderPrintPrefTag()
          const eff = effectivePrint(draft)
          UI.toast(`打印版式：${eff.size}${eff.photos ? '，含手臂照片' : '，不含照片'}，${printTemplateLabel(eff.template)}`, 'success')
          return close(true)
        }
        close(false)
      })
    })
  }

  function doPrint() {
    renderPrintParts()

    /* 应用打印版式（item 2b）。
     * 纸张尺寸走动态注入的 @page（见 applyPageSize），
     * 其余版式差异（字号、列宽、照片显隐）走 <html> 上的 data 属性 ——
     * 这些是普通选择器，属性驱动完全可靠。 */
    const eff = effectivePrint(loadPrintPref())
    applyPageSize(eff.size)
    const root = document.documentElement
    root.setAttribute('data-print-size', eff.size)
    root.setAttribute('data-print-photos', eff.photos ? '1' : '0')
    root.setAttribute('data-print-template', eff.template)

    /* 空项目（没填过敏原名称的格子）只在屏幕上留着给护士填，纸上不印。
     * 这里整表重渲一次：不能用 display:none 藏格子 —— 网格靠 grid 自动
     * 按行填充，藏掉中间几格后面的会整体回流，末行补的占位数就不对了，
     * 又会出现用户报的那种「半行」。重渲还会按新行数重算自适应行高。 */
    const hasEmpty = st.rows.some(r => r.control_type === 'NORMAL' && !(r.allergen_name || '').trim())
    /* A5 要把 4 列重渲成 2 列（见 renderTable 里的列数推导），
     * 所以即使没有空项目也必须重渲。反过来 restore 时也必须无条件渲回，
     * 否则 A5 打印后屏幕会一直停在 2 列 —— 屏幕上 2 列是错的版式
     * （纸质单实物是 4 列，护士按屏幕找位置会对不上）。 */
    const needRerender = hasEmpty || eff.size === PRINT_SIZES.A5
    if (needRerender) renderTable(true)

    /* 备注已于 2026-08 按使用方要求移除，这里只剩症状一项。
     * 仍用数组 + filter(Boolean) 而不写成单个元素：下面整段替身/隐藏逻辑
     * 对一项和多项同样适用，保持数组形式以后再加字段不用改结构。 */
    const areas = ['rp-symptoms'].map(id => document.getElementById(id)).filter(Boolean)

    // 连点两次打印时，上一次的替身可能还没被 restore 清掉，会出现两个重复的症状框，
    // 因此每次先清理残留
    document.querySelectorAll('.print-shadow').forEach(d => d.remove())
    areas.forEach(el => el.classList.remove('print-hide'))

    // 用等价的 div 替身顶替 textarea（打印后移除，textarea 自身只是临时隐藏，不丢数据）
    const shadows = []
    areas.forEach(el => {
      if (!el.value.trim()) return
      const div = document.createElement('div')
      div.className = 'print-shadow'
      div.textContent = el.value
      el.insertAdjacentElement('afterend', div)
      el.classList.add('print-hide')
      shadows.push(div)
    })

    // 没填内容的症状/备注框，在纸上只是白占一大块，整块（含标签）隐藏
    const emptied = areas.filter(el => !el.value.trim())
      .map(el => el.closest('div')).filter(Boolean)

    /* print-solo 已不再需要：它是为「症状|备注两列并排，其中一个为空时
     * 让另一个横跨整行」而存在的。备注移除后只剩症状一项，本就占整行。
     * 仍保留空数组变量：下方 restore() 要遍历它，而 restore 被 afterprint
     * 与 1500ms 兜底两条路径调用，动它的改动面大于收益。 */
    const solo = []
    /* 照片区隐藏有两个独立原因，但走同一条 emptied 路径：
     *   ① 本来就没照片 —— 否则纸上留一个孤零零的「手臂实验区照片」标题
     *   ② 用户选了不打印照片（item 2b）
     *
     * 【为什么必须走 emptied 而不是只靠 CSS 藏】
     * measureRowHeight 会跳过 .print-empty 的区块来算可用高度。
     * 若只用 CSS `[data-print-photos="0"] #photo-section{display:none}` 隐藏，
     * 那是 @media print 里的规则，而 measureRowHeight 在**屏幕媒体**下执行，
     * getComputedStyle 读不到它 —— 照片区的高度会被算进"已占用"，
     * 于是行高被压扁，明明腾出了一整块空间却不用，白挤。 */
    const hidePhotos = !eff.photos || (!st.photos.length && !st.newPhotos.length)
    if (hidePhotos) {
      const ps = document.getElementById('photo-section')
      if (ps) emptied.push(ps)
    }
    emptied.forEach(d => d.classList.add('print-empty'))

    // 空的 date input 在打印时会显示浏览器占位符 mm/dd/yyyy，中文报告单上很不专业。
    // 临时换成 text 类型清空显示，打印后还原（直接改 value 会丢用户数据）
    const dates = Array.from(document.querySelectorAll('#patient-fields input[type=date], #rp-date'))
      .filter(el => !el.value)
    dates.forEach(el => { el.dataset.__t = el.type; el.type = 'text' })

    let restored = false
    const restore = () => {
      if (restored) return   // afterprint 与 1500ms 兜底可能都触发，重渲两次会闪
      restored = true
      shadows.forEach(d => d.remove())
      areas.forEach(el => el.classList.remove('print-hide'))
      emptied.forEach(d => d.classList.remove('print-empty'))
      solo.forEach(d => d.classList.remove('print-solo'))
      dates.forEach(el => { if (el.dataset.__t) { el.type = el.dataset.__t; delete el.dataset.__t } })
      /* 清掉版式标记。不清的话属性会一直留在 <html> 上：
       * 虽然那些 CSS 规则都在 @media print 里、屏幕上无副作用，
       * 但下次若有人加了非打印态的选择器就会莫名生效，
       * 而且残留属性会让"当前是什么状态"变得不可读。 */
      document.documentElement.removeAttribute('data-print-size')
      document.documentElement.removeAttribute('data-print-photos')
      document.documentElement.removeAttribute('data-print-template')
      /* 渲回屏幕版式：把打印时摘掉的空项目还回来（否则护士打完一次就
       * 再也填不了剩余项），并把 A5 的 2 列恢复成屏幕的 4 列。 */
      if (needRerender) renderTable()
      window.removeEventListener('afterprint', restore)
    }
    window.addEventListener('afterprint', restore)

    /* 行高必须最后算：measureRowHeight 是量「表格以外区块的实际占用」，
     * 而上面刚刚才把空症状/空备注/无照片区隐藏、把 textarea 换成替身 div。
     * 在这些调整完成前量，得到的占用偏大，行高会被算小（白白压扁）。 */
    const grid = document.querySelector('.spt-grid4')
    if (grid) {
      const cells = document.querySelectorAll('.spt-cell').length
      /* 列数从 DOM 上的 --spt-cols 读回来，而不是再算一遍 effectivePrint：
       * 表格是上面刚渲染的，它用的列数才是事实。重新推导一遍等于制造
       * 第二个真相源，两边一旦不一致（比如偏好在这几行之间被改了），
       * 行高就会按错误的行数算。 */
      const cols = Number(grid.style.getPropertyValue('--spt-cols')) || GRID_COLS
      applyRowHeight(cells, cols)
    }

    window.print()
    // 部分浏览器不触发 afterprint（如打印预览被直接取消），兜底还原
    setTimeout(restore, 1500)
  }

  /* ---------------------- 照片区 ---------------------- */

  /* 侧别显示配置。三组都要能显示，UNKNOWN 尤其重要：
   * 0007 迁移前入库的照片、以及旧客户端上传的照片都是 UNKNOWN，
   * 不单独列出来的话它们会被误当成某一侧（或干脆看不见），
   * 而它们恰恰是需要人来补标的那批。 */
  const SIDE_GROUPS = [
    { key: 'LEFT',    name: '左手臂', badge: 'bg-sky-600',    hint: '' },
    { key: 'RIGHT',   name: '右手臂', badge: 'bg-violet-600', hint: '' },
    { key: 'UNKNOWN', name: '未标注左右', badge: 'bg-slate-500',
      hint: '这些照片没有左右标记（早期拍摄或上传时未选择），请点标注按钮补上' }
  ]
  const sideOf = (v) => {
    const s = String(v || '').toUpperCase()
    return s === 'LEFT' || s === 'RIGHT' ? s : 'UNKNOWN'
  }

  async function renderPhotos() {
    const grid = document.getElementById('photo-grid')
    const total = st.photos.length + st.newPhotos.length
    if (!total) {
      grid.innerHTML = `<div class="col-span-full">${UI.emptyState(
        'fa-camera-retro', '尚未采集手臂照片',
        '点击右上「左手臂」/「右手臂」按标记分别采集；' +
        '或「上传照片」选择已有图片（原图直传，不压缩）')}</div>`
      return
    }

    /* 按左右分组渲染。
     * 分组不只是排版：位点编号与手臂的对应关系是判读依据本身，
     * 混在一张网格里，操作者要靠记忆判断「第 3 张是哪条臂」，
     * 而这正是本次要消除的操作负担。 */
    const buckets = new Map(SIDE_GROUPS.map(g => [g.key, { saved: [], pending: [] }]))
    for (const p of st.photos) buckets.get(sideOf(p.arm_side)).saved.push(p)
    st.newPhotos.forEach((p, i) => buckets.get(sideOf(p.arm_side)).pending.push({ p, i }))

    grid.innerHTML = ''
    for (const g of SIDE_GROUPS) {
      const b = buckets.get(g.key)
      const n = b.saved.length + b.pending.length
      // 空分组不占版面，但左右两组即使为空也要显示「0 张」——
      // 只做了单侧时，让人一眼看到另一侧确实还没拍，而不是以为忘了渲染
      if (!n && g.key === 'UNKNOWN') continue

      grid.insertAdjacentHTML('beforeend', `
        <div class="col-span-full flex items-center gap-2 ${g.key === 'LEFT' ? '' : 'mt-2'}">
          <span class="px-2 py-0.5 rounded text-white text-xs ${g.badge}">${g.name}</span>
          <span class="text-xs text-slate-400">${n} 张</span>
          ${g.hint && n ? `<span class="text-xs text-amber-600 no-print"><i class="fas fa-triangle-exclamation mr-1"></i>${g.hint}</span>` : ''}
        </div>`)

      if (!n) {
        grid.insertAdjacentHTML('beforeend',
          `<p class="col-span-full text-xs text-slate-400 no-print -mt-1">尚未采集${g.name}照片</p>`)
        continue
      }

      for (const p of b.saved) {
        const url = await API.imageUrl(p.photo_url)
        grid.insertAdjacentHTML('beforeend', `
          <div class="thumb" data-saved="${p.id}">
            <img src="${url}" alt="${g.name}照片">
            <span class="print-only absolute top-0.5 left-0.5 px-1 rounded text-[10px] text-white ${g.badge}">${g.name}</span>
            <div class="thumb-actions no-print">
              <button data-zoom="${url}" class="btn btn-xs btn-ghost" title="放大"><i class="fas fa-magnifying-glass-plus"></i></button>
              <button data-side-saved="${p.id}" class="btn btn-xs btn-ghost" title="标注左右手臂"><i class="fas fa-hand"></i></button>
              <button data-del-saved="${p.id}" class="btn btn-xs btn-danger" title="删除"><i class="fas fa-trash"></i></button>
            </div>
            <p class="text-[10px] text-slate-500 px-1.5 py-1 truncate" title="${UI.esc(p.captured_by_device || '')}">${UI.esc(p.captured_by_device || '')}</p>
          </div>`)
      }
      for (const { p, i } of b.pending) {
        grid.insertAdjacentHTML('beforeend', `
          <div class="thumb ring-2 ring-amber-300">
            <img src="${p.data}" alt="待上传">
            <span class="print-only absolute top-0.5 left-0.5 px-1 rounded text-[10px] text-white ${g.badge}">${g.name}</span>
            <div class="thumb-actions no-print">
              <button data-zoom="${p.data}" class="btn btn-xs btn-ghost"><i class="fas fa-magnifying-glass-plus"></i></button>
              <button data-side-new="${i}" class="btn btn-xs btn-ghost" title="标注左右手臂"><i class="fas fa-hand"></i></button>
              <button data-del-new="${i}" class="btn btn-xs btn-danger"><i class="fas fa-trash"></i></button>
            </div>
            <p class="text-[10px] text-amber-600 px-1.5 py-1 truncate">待保存 · ${UI.esc(p.device || '')}</p>
          </div>`)
      }
    }

    grid.querySelectorAll('[data-zoom]').forEach(b => b.addEventListener('click', () => {
      UI.modal({ title: '照片预览', size: 'lg', body: `<img src="${b.dataset.zoom}" class="w-full rounded-lg" alt="预览">` })
    }))
    grid.querySelectorAll('[data-side-saved]').forEach(b => b.addEventListener('click',
      () => markSide({ photoId: b.dataset.sideSaved })))
    grid.querySelectorAll('[data-side-new]').forEach(b => b.addEventListener('click',
      () => markSide({ newIndex: Number(b.dataset.sideNew) })))
    /* 归档报告单：后端对照片的 PATCH / DELETE 都返 409。
     * 这里必须在**每次重绘后**重新隐藏，而不是只在 loadReport 里隐藏一次 ——
     * renderPhotos 会因删除、标注、重新载入等多条路径被再次调用，
     * 只隐藏一次的话按钮会在下一次重绘时悄悄回来。 */
    if (st.archivedLock) {
      grid.querySelectorAll('[data-side-saved], [data-side-new], [data-del-saved], [data-del-new]')
        .forEach(el => el.classList.add('hidden'))
    }
    grid.querySelectorAll('[data-del-new]').forEach(b => b.addEventListener('click', () => {
      st.newPhotos.splice(Number(b.dataset.delNew), 1); renderPhotos(); markDirty()
    }))
    grid.querySelectorAll('[data-del-saved]').forEach(b => b.addEventListener('click', async () => {
      if (!await UI.confirm({ title: '删除照片', message: '确认删除这张手臂照片？删除后不可恢复。' })) return
      try {
        await API.del(`/api/reports/${st.id}/photos/${b.dataset.delSaved}`)
        st.photos = st.photos.filter(p => p.id !== b.dataset.delSaved)
        UI.toast('照片已删除', 'success'); renderPhotos()
      } catch (e) { UI.toast(e.message, 'error') }
    }))
  }

  /* ---------------------- 事件绑定 ---------------------- */

  function bind(body) {
    const dsel = document.getElementById('rp-doctor')
    dsel.innerHTML = `<option value="">未指定</option>` + st.staff.filter(s => s.role === 'DOCTOR')
      .map(s => `<option value="${s.id}" ${s.id === st.doctor_id ? 'selected' : ''}>${UI.esc(s.real_name)}</option>`).join('')
    dsel.addEventListener('change', e => { st.doctor_id = e.target.value; markDirty() })

    const dateEl = document.getElementById('rp-date')
    dateEl.value = st.report_date
    dateEl.addEventListener('change', e => { st.report_date = e.target.value; markDirty() })

    document.getElementById('rp-symptoms').addEventListener('input', e => { st.symptoms = e.target.value; markDirty() })
    // 备注字段已按使用方要求移除（2026-08），此处原有的 #rp-notes 绑定同时删除。
    // 注意：绝不能只删 DOM 留着 getElementById(...).addEventListener —— 那会抛
    // "Cannot read properties of null"，把整个报告单页面打成白屏。
    document.getElementById('btn-now').addEventListener('click', fillNow)

    document.getElementById('btn-ocr').addEventListener('click', doOcr)
    document.getElementById('btn-pick-patient').addEventListener('click', pickPatient)
    /* 新建患者：一并录入本次申请信息（使用方要求与报告单上的字段一致）。
     * 申请信息不进患者档案（见 PagePatients.openEditor 的注释），
     * 而是由回调写到当前这张报告单的 st 上。
     * 把 st 里已有的值带过去做初值：可能刚做过 OCR 或手工填过一部分，
     * 不带过去会让人在弹窗里看到空白，以为要重填一遍。 */
    document.getElementById('btn-new-patient').addEventListener('click', () => PagePatients.openEditor(
      null,
      async (id, applyInfo) => { applyApplyInfo(applyInfo); await pickPatientById(id) },
      { withApplyInfo: true, applyInfo: currentApplyInfo() }
    ))
    document.getElementById('btn-use-template').addEventListener('click', pickTemplate)
    document.getElementById('btn-clear-results').addEventListener('click', async () => {
      if (!await UI.confirm({ title: '清空结果', message: '将清空全部阳性/阴性面积数据，过敏原名称保留。', okText: '清空结果' })) return
      st.rows.forEach(r => { r.positive_area = ''; r.negative_area = '' }); renderTable(); markDirty()
    })
    document.getElementById('btn-clear-all').addEventListener('click', async () => {
      if (!await UI.confirm({ title: '清空全部', message: `将清空全部 ${posCount()} 项过敏原名称与结果数据（对照组名称保留），并恢复为默认 ${DEFAULT_POS_COUNT} 项。` })) return
      st.rows = buildEmptyRows(); st.template_id = null; st.template_name = null
      renderTable(); renderTplBadge(); markDirty()
    })
    document.getElementById('btn-shoot-left')?.addEventListener('click', () => shootArm('LEFT'))
    document.getElementById('btn-shoot-right')?.addEventListener('click', () => shootArm('RIGHT'))
    document.getElementById('btn-upload-arm')?.addEventListener('click',
      () => document.getElementById('arm-upload-input')?.click())
    document.getElementById('arm-upload-input')?.addEventListener('change', async (e) => {
      /* 必须先 Array.from 取快照，再清空 input。
       *
       * input.files 返回的是**活引用（live FileList）**，不是快照：
       * `e.target.value = ''` 会把这个对象本身清空，于是先前赋值的 files
       * 长度当场变成 0（实测：清空前 1 → 清空后 0，且 files === e.target.files）。
       * 之前写成 `const files = e.target.files; e.target.value = ''` ——
       * uploadArmFiles 收到空列表后被开头的 `if (!list.length) return` 静默丢弃：
       * 没有请求、没有报错、没有 toast，用户只看到「点了上传没反应」，
       * 是最难排查的一类失败。
       *
       * 清空 input 本身是必要的（否则连续选同一个文件不会再触发 change），
       * 所以顺序不能反，也不能省快照。 */
      const files = Array.from(e.target.files || [])
      e.target.value = ''            // 允许再次选择同一文件
      await uploadArmFiles(files)
    })
    document.getElementById('btn-measure').addEventListener('click', () => openMeasure('AUTO'))
    document.getElementById('btn-measure-semi')?.addEventListener('click', () => openMeasure('SEMI'))
    document.getElementById('btn-save-draft').addEventListener('click', () => save('DRAFT'))
    document.getElementById('btn-submit').addEventListener('click', () => save('SUBMITTED'))
    document.getElementById('btn-print').addEventListener('click', doPrint)
    // 用 ?. ：这两个是本次新增的元素，若日后模板被改动而漏掉其中之一，
    // 无保护的 addEventListener 会抛 null 异常把整页打成白屏
    document.getElementById('btn-print-setup')?.addEventListener('click', openPrintSetup)
    renderPrintPrefTag()
    document.getElementById('rp-new').addEventListener('click', () => App.go('report'))
    document.getElementById('rp-back').addEventListener('click', () => App.back())

    if (App.user.role === 'NURSE') {
      const sb = document.getElementById('btn-submit')
      sb.title = '护士仅可保存草稿，提交需由医生完成'
    }
  }

  let dirty = false
  function markDirty() {
    dirty = true
    document.getElementById('rp-dirty').innerHTML = '<i class="fas fa-circle text-amber-400 text-[8px] mr-1"></i>未保存'
  }
  function clearDirty() { dirty = false; document.getElementById('rp-dirty').textContent = '' }

  /* ---------------------- HIS 申请单屏幕识别 ----------------------
   *
   * 现场作业方式变了：不再拍就诊卡，而是把摄像头**对着电脑屏幕**拍 HIS
   * 里的申请单窗口。这带来三个与拍卡片完全不同的问题，界面上必须交代：
   *   1) 摩尔纹 —— 屏幕像素栅格与相机感光元件干涉，「耳鼻咽喉」会被读成
   *      「其晶咽喉」这种形近字。后端 fixMoire 只能纠已知的几组。
   *   2) 反光 —— 屏幕自身发光 + 环境灯，白斑会整块吞掉文字。
   *   3) 屏幕上**没有出生日期**，只有年龄。所以识别结果里是 age_years，
   *      新建患者时走「只填年龄」的路径，不能再要求填生日。
   *
   * 识别结果分两部分落位：患者档案字段 / 本次申请字段（见后端 patient / report）。
   */

  /** 把「本次申请」字段填进报告单状态。空值不覆盖已填内容——
   *  识别是辅助，不该把操作者已经手工填好的字段擦掉。 */
  function applyOcrReport(rep) {
    if (!rep) return
    const set = (k, v) => { if (v) st[k] = v }
    set('applied_at', rep.applied_at)
    set('department', rep.department)
    set('applying_doctor', rep.applying_doctor)
    set('serial_no', rep.serial_no)
    set('clinical_diagnosis', rep.clinical_diagnosis)
    set('medical_record_no', rep.medical_record_no)
  }

  /** 本次申请信息的字段清单。新建患者弹窗与报告单表单共用，避免两处各写一份漂移 */
  const APPLY_KEYS = ['applied_at', 'department', 'applying_doctor', 'serial_no', 'clinical_diagnosis']

  /** 取当前报告单上的申请信息，用作「新建患者」弹窗的初值 */
  function currentApplyInfo() {
    const o = {}
    APPLY_KEYS.forEach(k => { if (st?.[k]) o[k] = st[k] })
    return o
  }

  /**
   * 把新建患者弹窗里录入的申请信息写回报告单。
   * 只覆盖有值的字段：弹窗里留空的项不应把 st 上已有的值清掉
   * （例如先 OCR 识别出了流水号，又在弹窗里没填，不能因此丢掉）。
   */
  function applyApplyInfo(info) {
    if (!info) return
    let changed = false
    APPLY_KEYS.forEach(k => {
      if (info[k]) { st[k] = info[k]; changed = true }
    })
    if (changed) markDirty()
  }

  async function doOcr() {
    const shots = await Capture.shoot('card', { multi: false })
    if (!shots.length) return
    try {
      UI.loading(true, '正在识别申请单屏幕…')
      const r = await API.post('/api/capture/ocr/his-screen', {
        data: shots[0].data, provider: shots[0].provider, device: shots[0].device
      })
      UI.loading(false)

      const hint = document.getElementById('ocr-hint')
      const f = r.fields || {}
      const rep = r.report || {}

      if (r.matched_patient) {
        // 病历号命中患者库 → 患者信息自动带出，申请信息一并回填
        st.patient = Object.assign({}, r.matched_patient, { __ocr: [] })
        st.ocrUnrecognized = []
        applyOcrReport(rep)
        if (f.age_years != null) st.patient_age_snapshot = f.age_years + '岁'
        renderPatient(); markDirty()
        hint.className = 'mt-3 text-xs bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-lg px-3 py-2'
        hint.innerHTML = `<i class="fas fa-circle-check mr-1"></i>已匹配到患者档案「${UI.esc(r.matched_patient.name)}」，患者与申请信息已带出。识别引擎：${UI.esc(r.engine)}`
        hint.classList.remove('hidden')
        UI.toast('已匹配患者：' + r.matched_patient.name, 'success')
        return
      }

      const filled = Object.keys(f).filter(k => f[k] !== '' && f[k] !== null && f[k] !== undefined)
      const total = filled.length

      if (!r.ocr_available) {
        hint.className = 'mt-3 text-xs bg-red-50 border border-red-200 text-red-800 rounded-lg px-3 py-2'
        hint.innerHTML = `<i class="fas fa-circle-exclamation mr-1"></i><b>识别服务不可用</b>：${UI.esc(r.message)}
          ${r.error_detail ? `<br><span class="text-[11px] font-mono text-red-500/80">${UI.esc(r.error_detail)}</span>` : ''}
          <br>屏幕照片已保存，请手动填写患者与申请信息。`
        hint.classList.remove('hidden')
      } else if (total === 0) {
        hint.className = 'mt-3 text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2'
        hint.innerHTML = `<i class="fas fa-triangle-exclamation mr-1"></i><b>未能从屏幕提取到信息</b>：请正对屏幕拍摄、让患者信息区填满取景框、避开屏幕反光后重试。`
        hint.classList.remove('hidden')
      } else {
        hint.className = 'mt-3 text-xs bg-sky-50 border border-sky-200 text-sky-800 rounded-lg px-3 py-2'
        hint.innerHTML = `<i class="fas fa-circle-info mr-1"></i>已识别 ${total} 项，患者库中无此病历号。请核对后新建患者。识别引擎：${UI.esc(r.engine)}${r.elapsed_ms ? `（${r.elapsed_ms}ms）` : ''}`
        hint.classList.remove('hidden')
      }

      const row = (label, val) => `
        <div class="flex items-center justify-between gap-2 ${val ? '' : 'opacity-70'}">
          <span class="text-slate-500">${label}</span>
          <b class="${val ? 'text-ink-800' : 'bg-yellow-100 text-amber-700 px-1.5 rounded'}">${val || '未识别'}</b>
        </div>`

      /* 摩尔纹提示单列出来：这是屏幕拍摄**特有**的失败方式，
       * 操作者拿到「其晶咽喉」这种结果时会以为是软件坏了，
       * 不说明原因就会反复重拍而不是去调角度。 */
      const diagBlock = (!r.ocr_available || total === 0) ? `
        <div class="mt-2 bg-amber-50 border border-amber-200 rounded-lg p-2.5 text-xs text-amber-800">
          <p class="font-medium mb-1"><i class="fas fa-lightbulb mr-1"></i>对着屏幕拍摄的注意事项</p>
          <ul class="list-disc pl-4 space-y-0.5 text-[11px] leading-relaxed">
            <li><b>正对屏幕</b>，镜头与屏幕平行，斜拍会让文字变形</li>
            <li>轻微<b>改变距离或角度</b>可消除条纹状<b>摩尔纹</b>（屏幕像素与镜头干涉，会把字读错）</li>
            <li>避开屏幕<b>反光白斑</b>，必要时关掉头顶灯</li>
            <li>让<b>患者信息区填满取景框</b>，不必拍整个屏幕</li>
            <li>可在「系统设置 → 采集分辨率」调到 <b>1920×1080</b></li>
          </ul>
          ${r.error_detail ? `<p class="mt-2 text-[10px] font-mono text-amber-600 break-all">${UI.esc(r.error_detail)}</p>` : ''}
        </div>` : ''

      UI.modal({
        title: total ? '<i class="fas fa-desktop text-brand-500 mr-2"></i>屏幕识别结果确认'
                     : '<i class="fas fa-triangle-exclamation text-amber-500 mr-2"></i>未识别到屏幕信息',
        size: 'lg',
        body: `
          <div class="grid sm:grid-cols-2 gap-3">
            <div class="space-y-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg p-3">
              <p class="text-[11px] font-semibold text-slate-500 pb-1">患者信息</p>
              ${row('姓名', UI.esc(f.name || ''))}
              ${row('性别', f.gender ? UI.fmt.gender(f.gender) : '')}
              ${row('年龄', f.age_years != null ? f.age_years + ' 岁' : '')}
              ${row('病历号', UI.esc(f.visit_card_no || ''))}
              <p class="text-[11px] font-semibold text-slate-500 pt-2 pb-1 border-t border-slate-200">本次申请</p>
              ${row('申请时间', UI.esc(rep.applied_at || ''))}
              ${row('科室', UI.esc(rep.department || ''))}
              ${row('申请医生', UI.esc(rep.applying_doctor || ''))}
              ${row('流水号', UI.esc(rep.serial_no || ''))}
              ${row('临床诊断', UI.esc(rep.clinical_diagnosis || ''))}
              <p class="pt-1.5 mt-1 border-t border-slate-200 text-[11px] text-slate-400">
                共识别 ${total} 项${(r.unrecognized || []).length ? '，关键项未识别已<span class="bg-yellow-100 px-1 rounded">标黄</span>，请人工补录' : ''}
              </p>
            </div>
            <div>
              <p class="text-[11px] text-slate-400 mb-1">拍摄到的屏幕图像</p>
              <!-- src 留空、由 onMount 里异步填：图片接口要带 Authorization，
                   API.imageUrl 是 async（取 blob → objectURL）。若在模板里直接内插，
                   插进去的是 "[object Promise]"，浏览器当成相对路径请求 → 裂图。 -->
              <img data-shot class="w-full rounded-lg border border-slate-200 bg-slate-50" alt="申请单屏幕">
              <p class="text-[10px] text-slate-400 mt-1">识别有误时请对照原图人工订正，切勿直接采信</p>
            </div>
          </div>
          ${diagBlock}`,
        footer: `
          <button data-cancel class="btn btn-ghost">取消</button>
          <button data-fillonly class="btn btn-ghost" ${total ? '' : 'disabled'}
                  title="只回填本次申请信息，不新建患者"><i class="fas fa-file-import"></i>仅填申请信息</button>
          <button data-retry class="btn btn-ghost"><i class="fas fa-rotate-right"></i>重新拍照识别</button>
          <button data-create class="btn btn-primary"><i class="fas fa-user-plus"></i>${total ? '新建患者档案' : '手动填写新建'}</button>`,
        onMount(root, close) {
          // 异步取原图（带鉴权）。取不到就明说，别留一个空白框让人以为没拍到 ——
          // 这张图是人工订正识别结果的唯一依据，静默失败会让医生误以为「屏幕没拍上」。
          const shot = root.querySelector('[data-shot]')
          if (shot) {
            API.imageUrl(r.screen_image_url).then((u) => {
              if (!root.isConnected) { if (u) URL.revokeObjectURL(u); return }
              if (u) {
                shot.src = u
                /* 弹窗关掉后释放 objectURL，避免反复识别累积占内存。
                 * UI.modal 只是 wrap.remove()，没有关闭事件可监听，
                 * 所以用 MutationObserver 盯 modal-root 的子节点增删来判定脱离。 */
                const host = document.getElementById('modal-root')
                if (host) {
                  const mo = new MutationObserver(() => {
                    if (!root.isConnected) { URL.revokeObjectURL(u); mo.disconnect() }
                  })
                  mo.observe(host, { childList: true })
                }
              } else {
                shot.replaceWith(Object.assign(document.createElement('div'), {
                  className: 'w-full rounded-lg border border-dashed border-slate-300 bg-slate-50 '
                           + 'text-xs text-slate-400 py-6 text-center',
                  textContent: '原图加载失败，请对照 HIS 屏幕核对上述识别结果'
                }))
              }
            })
          }
          root.querySelector('[data-cancel]').addEventListener('click', close)
          root.querySelector('[data-retry]').addEventListener('click', () => { close(); doOcr() })
          root.querySelector('[data-fillonly]').addEventListener('click', () => {
            close(); applyOcrReport(rep); renderPatient(); markDirty()
            UI.toast('申请信息已回填', 'success')
          })
          root.querySelector('[data-create]').addEventListener('click', () => {
            close()
            applyOcrReport(rep)
            PagePatients.openEditor({
              name: f.name || '', gender: f.gender || 'UNKNOWN',
              // 屏幕上没有出生日期，只带年龄过去；患者编辑器允许「只填年龄」
              birth_date: '', age_years: f.age_years ?? null,
              visit_card_no: f.visit_card_no || '',
              contact_person: '', contact_phone: '',
              card_photo_url: r.screen_image_url,
              __unrecognized: r.unrecognized || []
            },
            async (id, applyInfo) => { applyApplyInfo(applyInfo); await pickPatientById(id) },
            // applyOcrReport 上面已把识别值写进 st，故初值直接取 st ——
            // 这样弹窗里能看到并订正 OCR 识别出的科室/诊断，而不是又一个空白表单
            { withApplyInfo: true, applyInfo: currentApplyInfo() })
          })
        }
      })
    } catch (e) {
      UI.loading(false)
      UI.toast('识别失败：' + e.message, 'error')
    }
  }

  /* ---------------------- 患者选择 ---------------------- */

  async function pickPatientById(id) {
    try {
      const r = await API.get('/api/patients/' + id)
      st.patient = Object.assign({}, r.data, { __ocr: [] })
      st.ocrUnrecognized = []
      renderPatient(); markDirty()
    } catch (e) { UI.toast(e.message, 'error') }
  }

  function pickPatient() {
    const m = UI.modal({
      title: '<i class="fas fa-users text-brand-500 mr-2"></i>从患者库选择',
      size: 'lg',
      body: `<div class="space-y-3">
        <div class="flex gap-2">
          <input data-kw class="field-input" placeholder="搜索姓名 / 就诊卡号 / 电话">
          <button data-search class="btn btn-primary"><i class="fas fa-magnifying-glass"></i>搜索</button>
        </div>
        <div data-list class="max-h-[55vh] overflow-auto"></div>
      </div>`,
      onMount(root, close) {
        const list = root.querySelector('[data-list]')
        const kw = root.querySelector('[data-kw]')
        const load = async () => {
          list.innerHTML = '<p class="text-center py-8 text-slate-400 text-sm"><i class="fas fa-circle-notch fa-spin"></i> 加载中…</p>'
          try {
            const r = await API.get('/api/patients', { kw: kw.value, size: 50 })
            if (!r.data.length) { list.innerHTML = UI.emptyState('fa-user-slash', '未找到患者', '可点击「新建患者」创建'); return }
            list.innerHTML = `<table class="data-table"><thead><tr>
                <th>姓名</th><th>性别</th><th>年龄</th><th>就诊卡号</th><th>报告数</th><th></th></tr></thead><tbody>
              ${r.data.map(p => `<tr>
                <td class="font-medium">${UI.esc(p.name)}</td>
                <td>${UI.fmt.genderIcon(p.gender)}</td>
                <td>${UI.fmt.patientAge(p)}</td>
                <td class="font-mono text-xs">${UI.esc(p.visit_card_no)}</td>
                <td>${p.report_count}</td>
                <td class="text-right"><button data-pick="${p.id}" class="btn btn-sm btn-primary">选择</button></td>
              </tr>`).join('')}</tbody></table>`
            list.querySelectorAll('[data-pick]').forEach(b => b.addEventListener('click', async () => {
              close(); await pickPatientById(b.dataset.pick)
            }))
          } catch (e) { list.innerHTML = `<p class="text-red-600 text-sm py-6 text-center">${UI.esc(e.message)}</p>` }
        }
        root.querySelector('[data-search]').addEventListener('click', load)
        kw.addEventListener('keydown', e => { if (e.key === 'Enter') load() })
        load()
      }
    })
  }

  /* ---------------------- 模版选用 ---------------------- */

  function renderTplBadge() {
    const el = document.getElementById('tpl-badge')
    if (!st.template_name) { el.classList.add('hidden'); return }
    /* 必须带 no-print：这是「本单套用了哪个模版」的屏幕操作反馈，
     * 属于软件自身信息，化验单上不该出现（旁边按钮组都有 no-print，
     * 唯独这里因为整体赋值 className 把它冲掉了，结果印到了纸上）。 */
    el.className = 'flex items-center gap-1.5 no-print'
    el.innerHTML = `<span class="badge bg-brand-50 text-brand-700 border border-brand-100">
        <i class="fas fa-layer-group"></i> ${UI.esc(st.template_name)}</span>
      ${st.template_deleted ? `<span class="badge badge-deleted" title="模版已删除，但本报告单数据已快照，完整可读">
        <i class="fas fa-triangle-exclamation"></i> 模版已删除${st.template_deleted_at ? '（' + UI.fmt.datetime(st.template_deleted_at) + '）' : ''}</span>` : ''}`
    el.classList.remove('hidden')
  }

  async function pickTemplate() {
    let tpls = []
    try { tpls = (await API.get('/api/templates')).data } catch (e) { UI.toast(e.message, 'error'); return }
    UI.modal({
      title: '<i class="fas fa-layer-group text-brand-500 mr-2"></i>选用模版',
      size: 'lg',
      body: tpls.length ? `<div class="grid sm:grid-cols-2 gap-3">
          ${tpls.map(t => `
            <button data-t="${t.id}" class="card card-hover p-4 text-left">
              <div class="flex items-start gap-2">
                <span class="h-9 w-9 rounded-lg bg-brand-50 text-brand-600 grid place-items-center shrink-0"><i class="fas fa-layer-group"></i></span>
                <div class="min-w-0 flex-1">
                  <p class="font-semibold text-sm text-ink-800 truncate">${UI.esc(t.name)}</p>
                  <p class="text-xs text-slate-400 truncate">${UI.esc(t.description || '无描述')}</p>
                  <div class="flex flex-wrap gap-1.5 mt-2 text-[11px]">
                    <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">${t.allergen_count} 项过敏原</span>
                    <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">阳性:${UI.esc(t.control_positive_allergen || '—')}</span>
                    <span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">阴性:${UI.esc(t.control_negative_allergen || '—')}</span>
                  </div>
                </div>
              </div>
            </button>`).join('')}
        </div>` : UI.emptyState('fa-layer-group', '暂无模版', '请先到「模版库」创建模版'),
      onMount(root, close) {
        root.querySelectorAll('[data-t]').forEach(b => b.addEventListener('click', async () => {
          const t = tpls.find(x => x.id === b.dataset.t)
          close()
          applyTemplate(t)
        }))
      }
    })
  }

  /**
   * 应用模版：A. 过敏原名称按位置序号填充；B. 阳性/阴性对照行一并填充；
   *          C. 填上名称的过敏原位点默认判为阴性（使用方 2026-08 要求）
   * 应用后模版本身的修改不再影响本报告单（数据已快照）
   *
   * 关于 C 的三条边界（都是刻意这么定的，改动前请先想清楚）：
   *
   * 1) 只给**有名称的普通位点**默认阴性。
   *    空位点保持空白 —— 20 个位置里只用了 12 个时，剩下 8 格印成「—」
   *    会让人以为那 8 项也测了且是阴性，而实际上根本没做。
   *
   * 2) **不覆盖已有结果**。重复套用模版（比如先用错模版再换一个）不能把
   *    医生已经点出来的阳性等级抹成阴性 —— 那是丢判读结果。
   *
   * 3) **对照行不给默认值**，保持空白等人工判读。
   *    阳性对照（组胺）本就应该起风团，预填「阴性」是印一句假话；
   *    阴性对照（生理盐水）更关键 —— 它若为阳性说明皮肤划痕症或试剂污染，
   *    整份实验无效。给它预填阴性会让操作者不再抬眼看这一格，
   *    把「必须确认的一票否决项」变成默认通过。使用方要求的是
   *    「过敏原点刺结果区域」默认阴性，对照区不在其内。
   */
  function applyTemplate(t) {
    if (!t) return
    // 模版项目数可多于当前报告单位置数（项目数无硬约束）→ 先按需扩展位置，避免超出部分被丢弃
    const tplMax = t.rows.reduce((m, r) => Math.max(m, Number(r.position_no) || 0), 0)
    if (tplMax > posCount()) {
      const ctrls = st.rows.filter(r => r.control_type !== 'NORMAL')
      const normals = st.rows.filter(r => r.control_type === 'NORMAL').sort((a, b) => a.position_no - b.position_no)
      for (let i = normals.length + 1; i <= Math.min(tplMax, MAX_POS_COUNT); i++) {
        normals.push({ position_no: i, allergen_name: '', positive_area: '', negative_area: '', control_type: 'NORMAL' })
      }
      st.rows = [...normals, ...ctrls]
    }
    let negFilled = 0
    t.rows.forEach(tr => {
      const r = rowAt(tr.position_no)
      if (!r) return
      r.allergen_name = tr.allergen_name || ''
      // 有名称 + 当前无结论 → 默认阴性（见函数头注释的三条边界）
      if (r.allergen_name && !r.positive_area && !r.negative_area) {
        r.negative_area = NEG_MARK
        negFilled++
      }
    })
    if (t.control_positive_allergen) rowAt(POS_CTRL).allergen_name = t.control_positive_allergen
    if (t.control_negative_allergen) rowAt(NEG_CTRL).allergen_name = t.control_negative_allergen
    st.template_id = t.id
    st.template_name = t.name
    st.template_deleted = false
    renderTable(); renderTplBadge(); markDirty()
    // 必须把「已默认判阴性」说出来：这是软件替人写进单子的结论，
    // 操作者不知道的话，就不会去逐项核对哪些其实是阳性。
    UI.toast(negFilled
      ? `已应用模版「${t.name}」，${negFilled} 项默认为阴性，请按实际情况修改阳性项`
      : `已应用模版「${t.name}」，数据将快照保存`, 'success')
  }

  /* ---------------------- 拍照 ---------------------- */

  /**
   * 拍摄指定手臂。
   * @param {'LEFT'|'RIGHT'} side 必传。不设默认值：默认一侧就意味着
   *   某条调用路径会静默把照片记到错误的手臂上，而结果看起来完全正常。
   */
  async function shootArm(side) {
    const label = side === 'RIGHT' ? '右手臂' : '左手臂'
    const shots = await Capture.shoot('arm', { multi: true, side })
    if (!shots.length) return
    if (st.id) {
      // 已有报告单 → 立即上传
      UI.loading(true, `上传 ${shots.length} 张${label}照片…`)
      try {
        for (const s of shots) {
          const r = await API.post(`/api/reports/${st.id}/photos`,
            { data: s.data, device: s.device, provider: s.provider, arm_side: side })
          if (!r.deduped) {
            st.photos.push({ id: r.id, photo_url: r.photo_url, captured_by_device: s.device, arm_side: side })
          } else {
            /* 去重命中：后端可能已把 UNKNOWN 补成本次的侧别，
             * 本地那条记录要同步，否则界面上它还留在「未标注」组里，
             * 操作者会以为标注没生效而反复重拍。 */
            const ex = st.photos.find(p => p.id === r.id)
            if (ex && r.arm_side) ex.arm_side = r.arm_side
          }
        }
        UI.toast(`已保存 ${shots.length} 张${label}照片`, 'success')
      } catch (e) { UI.toast(e.message, 'error') } finally { UI.loading(false) }
    } else {
      st.newPhotos.push(...shots.map(s => ({ ...s, arm_side: side })))
      markDirty()
      UI.toast(`已采集 ${shots.length} 张${label}照片，保存报告单时一并上传`, 'success')
    }
    renderPhotos()
  }

  /**
   * 补标 / 更正照片的左右手臂。
   *
   * 存在的必要性：存量照片全是 UNKNOWN，没有这个功能只能删掉重拍 ——
   * 照片是临床原始记录，为补一个标签删原始记录是本末倒置。
   * 同时覆盖「现场按错了左右」这类高频失误。
   */
  /**
   * 左右手臂选择器。返回 'LEFT' | 'RIGHT' | 'UNKNOWN' | null（null = 取消）。
   *
   * allowUnknown 只在**批量上传**时开放：一次选中的多张图可能左右混杂，
   * 强迫操作者给整批选一侧，等于让软件把一半照片标错 ——
   * 那比「暂不标注」更糟，因为错的标注看起来是可信的。
   * 单张补标不给这个选项：都点进来改了，就该给出确定答案。
   */
  function pickSide({ message, current = 'UNKNOWN', allowUnknown = false }) {
    return new Promise((resolve) => {
      /* 类名写字面量、不用 `ring-${色}-400` 拼接：
       * Tailwind 的类名生成依赖能在源码/DOM 里看到完整字符串，
       * 拼接出来的类名在多种构建/扫描方式下都可能不生成对应 CSS，
       * 表现为「选中态没有高亮」这种不报错、只是悄悄失效的问题。 */
      const ringL = current === 'LEFT' ? 'ring-2 ring-offset-1 ring-sky-400' : ''
      const ringR = current === 'RIGHT' ? 'ring-2 ring-offset-1 ring-violet-400' : ''
      const m = UI.modal({
        title: '<i class="fas fa-hand text-brand-500 mr-2"></i>标注手臂',
        body: `<p class="text-sm text-slate-600 mb-3">${message}</p>
          <div class="flex gap-2">
            <button data-pick="LEFT" class="btn flex-1 text-white bg-sky-600 hover:bg-sky-700 ${ringL}">左手臂</button>
            <button data-pick="RIGHT" class="btn flex-1 text-white bg-violet-600 hover:bg-violet-700 ${ringR}">右手臂</button>
          </div>
          ${allowUnknown ? `<button data-pick="UNKNOWN" class="btn btn-ghost btn-sm w-full mt-2">这批照片左右混杂，先不标注（之后逐张补）</button>` : ''}
          <p class="text-xs text-slate-400 mt-3">当前：${current === 'UNKNOWN' ? '未标注' : (current === 'LEFT' ? '左手臂' : '右手臂')}</p>`,
        footer: `<button data-cancel class="btn btn-ghost">取消</button>`,
        onMount(root, close) {
          root.querySelectorAll('[data-pick]').forEach(b =>
            b.addEventListener('click', () => { close(); resolve(b.dataset.pick) }))
          root.querySelector('[data-cancel]').addEventListener('click', () => { close(); resolve(null) })
        }
      })
      m.root.addEventListener('click', (e) => { if (e.target === m.root) resolve(null) })
    })
  }

  async function markSide({ photoId, newIndex }) {
    const cur = photoId
      ? sideOf(st.photos.find(p => p.id === photoId)?.arm_side)
      : sideOf(st.newPhotos[newIndex]?.arm_side)

    const pick = await pickSide({ message: '这张照片拍的是患者的哪条手臂？', current: cur })
    if (!pick || pick === cur) return

    if (photoId) {
      try {
        await API.patch(`/api/reports/${st.id}/photos/${photoId}`, { arm_side: pick })
        const p = st.photos.find(x => x.id === photoId)
        if (p) p.arm_side = pick
        UI.toast(`已标注为${pick === 'LEFT' ? '左' : '右'}手臂`, 'success')
      } catch (e) { UI.toast('标注失败：' + (e.message || e), 'error'); return }
    } else {
      // 未上传的照片只改本地，随保存一并提交（不产生一次多余请求）
      st.newPhotos[newIndex].arm_side = pick
      markDirty()
      UI.toast(`已标注为${pick === 'LEFT' ? '左' : '右'}手臂，保存时一并上传`, 'success')
    }
    renderPhotos()
  }

  /* ---------------------- 上传本地照片 ---------------------- */

  /* 与 nginx client_max_body_size 32m 对齐，但要扣掉 base64 的 33% 膨胀：
   * 6.5MB 原图 → 8.7MB 请求体（已实测通过）。按 22MB 原图封顶，
   * 换算后约 29.3MB 请求体，留出余量给 JSON 其余字段。
   * 前端先拦是为了当场说清原因，而不是让用户等半天再吃一个 413。 */
  const ARM_MAX_BYTES = 22 * 1024 * 1024

  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader()
      fr.onload = () => resolve(String(fr.result))
      fr.onerror = () => reject(new Error('文件读取失败'))
      fr.readAsDataURL(file)      // 原始字节的 base64，不经 canvas，不重编码
    })
  }

  async function uploadArmFiles(files) {
    const list = Array.from(files || [])
    /* 取消文件选择框时 change 根本不会触发，所以走到这里却拿到空列表，
     * 说明是代码问题（曾因 live FileList 被 input.value='' 清空而发生过）。
     * 静默 return 会让这类故障完全无声 —— 用户只看到「点了没反应」，
     * 控制台干净，无从下手。这里必须留下痕迹并给用户一句可复述的话。 */
    if (!list.length) {
      console.error('[report] uploadArmFiles 收到空文件列表，上传未执行')
      UI.toast('未读取到所选图片，请重试；若反复出现请联系管理员', 'warn', 6000)
      return
    }

    const shots = []
    for (const f of list) {
      if (!/^image\/(jpeg|png|webp)$/.test(f.type || '')) {
        // HEIC 是 iPhone 默认格式，浏览器解不了，必须点名说清楚怎么办
        const isHeic = /\.(heic|heif)$/i.test(f.name || '')
        UI.toast(isHeic
          ? `${f.name} 是 HEIC 格式，浏览器无法解码。请在 iPhone「设置→相机→格式」选「兼容性最强」，或导出为 JPG`
          : `${f.name} 不是受支持的图片格式（仅 JPG / PNG / WebP）`, 'warn', 8000)
        continue
      }
      if (f.size > ARM_MAX_BYTES) {
        UI.toast(`${f.name} 为 ${(f.size / 1048576).toFixed(1)}MB，超过 ${ARM_MAX_BYTES / 1048576}MB 上限`, 'error', 7000)
        continue
      }
      try {
        shots.push({
          data: await readAsDataUrl(f),
          device: 'upload:' + (f.name || 'local').slice(0, 60),
          provider: 'upload',
          _name: f.name,
          _size: f.size
        })
      } catch (e) {
        UI.toast(`${f.name} 读取失败：${e.message || e}`, 'error')
      }
    }
    if (!shots.length) return

    /* 上传的照片无法从文件本身推断左右（EXIF 里没有这个信息），
     * 所以必须问人。允许「先不标注」：一次选中的多张可能左右混杂，
     * 逼着给整批选一侧会让一半照片带上错的标注。 */
    const side = await pickSide({
      message: `即将上传 ${shots.length} 张照片，这些照片拍的是患者的哪条手臂？`,
      allowUnknown: true
    })
    if (!side) return   // 取消 = 不上传，避免留下一批无标注照片

    // 复用与「拍照」完全相同的入库流程：照片进库后，测量／复看／打印
    // 全都能用，且与现场拍摄的照片没有任何区别。
    if (st.id) {
      UI.loading(true, `上传 ${shots.length} 张照片…`)
      let done = 0, dup = 0
      try {
        for (const s of shots) {
          const r = await API.post(`/api/reports/${st.id}/photos`,
            { data: s.data, device: s.device, provider: s.provider, arm_side: side })
          if (r.deduped) {
            dup++
            const ex = st.photos.find(p => p.id === r.id)
            if (ex && r.arm_side) ex.arm_side = r.arm_side
            continue
          }
          st.photos.push({ id: r.id, photo_url: r.photo_url, captured_by_device: s.device, arm_side: side })
          done++
        }
        UI.toast(`已上传 ${done} 张（原图直传，未压缩）` + (dup ? `，${dup} 张与已有照片重复已跳过` : '') +
          (side === 'UNKNOWN' ? '。这些照片尚未标注左右，请逐张点手掌图标补标' : ''),
          'success', side === 'UNKNOWN' ? 8000 : 5000)
      } catch (e) {
        UI.toast('上传失败：' + (e.message || e), 'error', 7000)
      } finally { UI.loading(false) }
    } else {
      st.newPhotos.push(...shots.map(s => ({ data: s.data, device: s.device, provider: s.provider, arm_side: side })))
      markDirty()
      UI.toast(`已选择 ${shots.length} 张，保存报告单时一并上传` +
        (side === 'UNKNOWN' ? '（尚未标注左右）' : ''), 'success')
    }
    renderPhotos()
  }

  /* ---------------------- 自动测量分级 ---------------------- */

  /**
   * 打开测量向导。
   *
   * 三个前置条件缺一不可，且都要在打开前解决，不能进去再报错：
   *
   * 1) 报告单必须已存库 —— /apply 是 UPDATE spt_report_row_snapshot，
   *    行快照不存在时更新 0 行，接口会成功返回但什么也没写。
   * 2) 本地不能有未保存修改 —— 等级写在服务端的 positive_area 上，
   *    而本页保存时用的是内存里的 st.rows。若带着脏数据去测，测完再保存
   *    就会用旧值把刚写入的等级覆盖掉，且界面上看不出任何异常。
   * 3) 必须有照片，且至少一个位点填了过敏原名称（否则没有可测目标）。
   */
  async function openMeasure(mode) {
    if (!st.patient?.id) { UI.toast('请先识别申请单屏幕或选择患者', 'warn'); return }

    const named = st.rows.filter(r => r.control_type === 'NORMAL' && (r.allergen_name || '').trim()).length
    if (!named) { UI.toast('请先填写过敏原名称（或选用模版），再进行测量', 'warn'); return }

    if (!st.id || dirty || st.newPhotos.length) {
      const ok = await UI.confirm({
        title: '需要先保存草稿', danger: false, okText: '保存并继续',
        message: `测量结果将直接写入服务端的报告单行，因此必须先把当前内容保存为草稿。<br>
          <span class="text-slate-500 text-xs">否则测量写入的等级会在下次保存时被本地旧数据覆盖。</span>`
      })
      if (!ok) return
      await save('DRAFT')
      if (!st.id || dirty) return   // 保存失败则不继续
    }

    if (!st.photos.length) { UI.toast('请先拍摄并保存手臂实验区照片', 'warn'); return }

    // mode 参数来自调用方：'AUTO'=全自动（默认）/ 'SEMI'=半自动
    WhealMeasure.open({
      reportId: st.id,
      rows: st.rows,
      photos: st.photos,
      mode: mode || 'AUTO',
      // 写入后必须重新拉取：等级与尺寸都在服务端行快照里，
      // 本地 st.rows 不会自动知道它们变了
      onApplied: async () => { delete st._measureMode; await loadReport(st.id) }
    })
    delete st._measureMode
  }

  /* ---------------------- 保存 / 提交 ---------------------- */

  async function save(status) {
    if (!st.patient?.id) { UI.toast('请先识别申请单屏幕或选择患者', 'warn'); return }
    if (status === 'SUBMITTED') {
      if (App.user.role === 'NURSE') { UI.toast('护士仅可保存草稿，提交需由医生完成', 'warn'); return }
      if (!st.doctor_id) { UI.toast('提交前请指定开单医生', 'warn'); return }
      if (st.photos.length + st.newPhotos.length < 1) { UI.toast('提交前至少需要 1 张手臂实验区照片', 'warn'); return }
      const hasData = st.rows.some(r => r.allergen_name && (r.positive_area || r.negative_area))
      if (!hasData && !await UI.confirm({
        title: '结果为空', danger: false, okText: '仍然提交',
        message: '当前尚未填写任何点刺结果（阳性/阴性面积），确认提交？'
      })) return

      // 提交即出报告：报告时间没填就是当下，执行时间随之倒推 20 分钟。
      // 不在这里补的话，纸质单底栏会印出两个空白时间——而这两个时间是有法律意义的。
      if (!st.reported_at) {
        st.reported_at = nowMinute()
        if (!st.executed_at) st.executed_at = minus20Local(st.reported_at)
        renderSignoff()
      }
    }

    const payload = {
      patient_id: st.patient.id,
      doctor_id: st.doctor_id || undefined,
      status,
      report_date: st.report_date,
      symptoms: st.symptoms,
      notes: st.notes,
      template_id: st.template_id || undefined,

      /* 纸质单头部/页脚字段。
       * 全部**显式**传出（哪怕是空串）：后端 pickFormFields 只覆盖请求体里出现过的键，
       * 漏传的键会保留旧值——那样操作者在界面上清空一个字段就永远清不掉。 */
      medical_record_no: st.medical_record_no || '',
      applied_at: st.applied_at || '',
      department: st.department || '',
      applying_doctor: st.applying_doctor || '',
      serial_no: st.serial_no || '',
      clinical_diagnosis: st.clinical_diagnosis || '',
      patient_age_snapshot: st.patient_age_snapshot || '',
      executed_at: st.executed_at || '',
      reported_at: st.reported_at || '',
      tester_name: st.tester_name || '',

      rows: st.rows,
      // arm_side 必须一并传：漏传会让待保存照片的左右标注在保存那一刻静默丢失，
      // 界面上标好了、存进去是 UNKNOWN，重新载入后才发现
      photos: st.newPhotos.map(p => ({ data: p.data, device: p.device, arm_side: p.arm_side || 'UNKNOWN' }))
    }
    try {
      UI.loading(true, status === 'SUBMITTED' ? '提交报告单…' : '保存草稿…')
      if (st.id) {
        await API.put('/api/reports/' + st.id, payload)
      } else {
        const r = await API.post('/api/reports', payload)
        st.id = r.id
      }
      st.newPhotos = []
      UI.loading(false)
      clearDirty()
      UI.toast(status === 'SUBMITTED' ? '报告单已提交' : '草稿已保存', 'success')
      await loadReport(st.id)
    } catch (e) {
      UI.loading(false)
      UI.toast(e.message, 'error')
    }
  }

  /* ---------------------- 加载已有报告单 ---------------------- */

  async function loadReport(id, readonly = false) {
    try {
      UI.loading(true, '加载报告单…')
      const r = (await API.get('/api/reports/' + id)).data
      st.id = r.id
      st.status = r.status
      st.report_date = r.report_date
      st.symptoms = r.symptoms || ''
      st.notes = r.notes || ''
      st.doctor_id = r.doctor?.id || ''
      st.patient = Object.assign({}, r.patient, { __ocr: [] })

      /* 纸质单头部/页脚字段。
       * 病历号缺省回落到就诊卡号：0006 之前存的老报告单没有这一列，
       * 直接留空会让历史单打印出来少一个关键号码。 */
      st.medical_record_no = r.medical_record_no || r.patient?.visit_card_no || ''
      st.applied_at = r.applied_at || ''
      st.department = r.department || ''
      st.applying_doctor = r.applying_doctor || ''
      st.serial_no = r.serial_no || ''
      st.clinical_diagnosis = r.clinical_diagnosis || ''
      st.patient_age_snapshot = r.patient_age_snapshot || ''
      st.executed_at = r.executed_at || ''
      st.reported_at = r.reported_at || ''
      st.tester_name = r.tester_name || ''
      st.reviewer_id = r.reviewer_id || ''
      st.reviewer_name = r.reviewer_name || ''
      // 载入的执行时间是库里存的既有值，不能被「改报告时间」联动覆盖掉
      st.__execTouched = !!r.executed_at
      st.template_id = r.template_id
      st.template_name = r.template_status?.name || r.template_name_snapshot
      st.template_deleted = !!r.template_status?.is_deleted
      st.template_deleted_at = r.template_status?.deleted_at
      st.photos = r.photos || []
      st.newPhotos = []

      // 用快照数据渲染（模版删除也完整可读）
      st.rows = buildEmptyRows()
      for (const row of r.rows || []) {
        const t = rowAt(row.position_no)
        if (t) Object.assign(t, {
          allergen_name: row.allergen_name || '',
          positive_area: row.positive_area || '',
          negative_area: row.negative_area || '',
          control_type: row.control_type,
          // 自动测量的溯源信息。不参与保存（保存只回传上面四个字段），
          // 仅用于在表格里标出「这一格是机器量的」并提供明细。
          // 丢掉它们的后果是：等级看得见，但无法判断来源与是否被改过。
          d_mean_mm: row.d_mean_mm,
          d_max_mm: row.d_max_mm,
          d_perp_mm: row.d_perp_mm,
          grade_suggested: row.grade_suggested,
          grade_confirmed: row.grade_confirmed,
          grade_ratio: row.grade_ratio,
          measure_source: row.measure_source,
          segment_method: row.segment_method,
          measure_confidence: row.measure_confidence
        })
      }

      document.getElementById('rp-title').textContent = `报告单 · ${r.patient?.name || ''}`
      document.getElementById('rp-subtitle').textContent =
        `${UI.fmt.date(r.report_date)} · 医生 ${r.doctor?.real_name || '—'} · 操作人 ${r.operator?.real_name || '—'}`
      document.getElementById('rp-status-area').innerHTML = UI.fmt.status(r.status) +
        (r.status === 'SUBMITTED' && App.user.role === 'DOCTOR'
          ? ' <button id="btn-archive" class="btn btn-xs btn-ghost ml-1"><i class="fas fa-box-archive"></i>归档</button>' : '')
      document.getElementById('btn-archive')?.addEventListener('click', async () => {
        if (!await UI.confirm({ title: '归档报告单', danger: false, okText: '归档', message: '归档后报告单将不可再修改。' })) return
        try { await API.post(`/api/reports/${id}/archive`); UI.toast('已归档', 'success'); loadReport(id) }
        catch (e) { UI.toast(e.message, 'error') }
      })
      document.getElementById('rp-date').value = st.report_date
      document.getElementById('rp-symptoms').value = st.symptoms
      // 备注字段已移除（2026-08），不再回填 #rp-notes（该元素已不存在）
      document.getElementById('rp-doctor').value = st.doctor_id

      renderTable(); renderPatient(); renderSignoff(); renderPhotos(); renderTplBadge()
      clearDirty()

      const locked = readonly || r.status === 'ARCHIVED'
      if (locked) {
        document.querySelectorAll('#spt-table-wrap .cell-input, #patient-fields input, #patient-fields select, #signoff-fields input, #rp-symptoms')
          .forEach(el => el.disabled = true)
        // 结果格是 div 不是 input，disabled 对它无效；用 .locked 让点击弹层直接返回
        document.querySelectorAll('#spt-table-wrap .spt-cell-res').forEach(el => el.classList.add('locked'))
        // btn-upload-arm 必须一并隐藏：归档报告后端会 409 拒绝追加照片，
        // 界面上留着入口只会让人点了才知道不行
        document.querySelectorAll('#btn-save-draft, #btn-submit, #btn-use-template, #btn-clear-results, #btn-clear-all, #btn-shoot-left, #btn-shoot-right, #btn-upload-arm, #btn-measure, #btn-measure-semi, #btn-ocr, #btn-now')
          .forEach(el => el?.classList.add('hidden'))
        /* 侧别标注/删除按钮在缩略图里，是 renderPhotos 动态生成的，不在上面那串静态 id 里。
         * 归档后后端 PATCH / DELETE 同样返 409，界面留着入口只会让人点了才知道不行。
         * 置标志位供 renderPhotos 后续重绘时继续隐藏（本次先直接隐藏已渲染的）。 */
        st.archivedLock = true
        document.querySelectorAll('[data-side-saved], [data-side-new], [data-del-saved], [data-del-new]')
          .forEach(el => el.classList.add('hidden'))
      }
    } catch (e) {
      UI.toast(e.message, 'error')
    } finally { UI.loading(false) }
  }

  return { render, loadReport }
})()
