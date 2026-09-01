/* =============================================================================
 * SPA 引导 / 路由 / 登录 / 侧边栏
 * 依赖：API, UI, Capture, PageAdmin, PagePatients, PageReport, PageReports,
 *      PageTemplates, PageAllergens, PageSettings, PageExports
 * ============================================================================= */
window.App = (() => {

  const state = { user: null, route: null, params: {}, mustChange: false, stack: [] }

  /* 离开当前页前的确认钩子（页面自行注册，例如报告单有未保存修改时） */
  let leaveGuard = null

  /* --------------------------- 路由表 --------------------------- */
  const ROUTES = {
    /* 临床角色 */
    'reports':          { title: '报告单列表', icon: 'fa-file-medical', roles: ['DOCTOR', 'NURSE'], nav: true, render: (b, p) => PageReports.render(b, p) },
    'report':           { title: '报告单', icon: 'fa-file-pen', roles: ['DOCTOR', 'NURSE'], nav: false, render: (b, p) => PageReport.render(b, p) },
    'patients':         { title: '患者库', icon: 'fa-hospital-user', roles: ['DOCTOR', 'NURSE'], nav: true, render: (b, p) => PagePatients.render(b, p) },
    'templates':        { title: '模版库', icon: 'fa-layer-group', roles: ['DOCTOR', 'NURSE'], nav: true, render: (b, p) => PageTemplates.render(b, p) },
    'exports':          { title: '数据导出', icon: 'fa-file-export', roles: ['DOCTOR', 'NURSE', 'PLATFORM_ADMIN'], nav: true, render: (b, p) => PageExports.render(b, p) },
    'settings':         { title: '系统设置', icon: 'fa-gear', roles: ['DOCTOR', 'NURSE'], nav: true, render: (b, p) => PageSettings.render(b, p) },
    /* 平台管理员 */
    'admin-overview':   { title: '平台总览', icon: 'fa-gauge-high', roles: ['PLATFORM_ADMIN'], nav: true, render: (b) => PageAdmin.overview(b) },
    'admin-hospitals':  { title: '医院管理', icon: 'fa-hospital', roles: ['PLATFORM_ADMIN'], nav: true, render: (b) => PageAdmin.hospitals(b) },
    'admin-accounts':   { title: '账号管理', icon: 'fa-users-gear', roles: ['PLATFORM_ADMIN'], nav: true, render: (b, p) => PageAdmin.accounts(b, p) },
    'admin-audit':      { title: '审计日志', icon: 'fa-clipboard-list', roles: ['PLATFORM_ADMIN'], nav: true, render: (b) => PageAdmin.auditLog(b) },
    'admin-print-templates': { title: '报告单版式', icon: 'fa-file-invoice', roles: ['PLATFORM_ADMIN'], nav: true, render: (b) => PageAdmin.printTemplates(b) }
  }

  /* 子页面（nav:false）→ 所属父页面。
   * 侧栏里没有入口的页面都是「下钻」进来的，必须能退回去；
   * 这张表既用于返回按钮的兜底目标，也用于侧栏高亮。 */
  const PARENT_OF = {
    'report': 'reports'
  }

  const NAV_GROUPS = [
    { label: '临床工作', keys: ['reports', 'patients', 'templates'] },
    { label: '数据与设置', keys: ['exports', 'settings'] },
    { label: '平台管理', keys: ['admin-overview', 'admin-hospitals', 'admin-accounts', 'admin-print-templates', 'admin-audit'] },
    { label: '数据中心', keys: [] } // 预留
  ]

  const homeOf = (role) => role === 'PLATFORM_ADMIN' ? 'admin-overview' : 'reports'

  /* --------------------------- 启动 --------------------------- */
  async function boot() {
    bindGlobal()
    const cached = API.store.user
    /* 令牌只存 sessionStorage：关闭浏览器后这里必然取不到 → 回登录页。
     * 另外即使同一标签页刷新，也要先确认「上次操作距今未超过 2 小时」。 */
    if (API.store.access && cached && !API.store.isIdleExpired) {
      try {
        const me = await API.me()
        state.user = me.user
        state.mustChange = !!me.must_change_password
        enterApp()
        return
      } catch { /* token 失效 → 登录页 */ }
    }
    if (API.store.access && API.store.isIdleExpired) {
      API.store.clear()
      showLogin('已超过 2 小时无操作，请重新登录')
      return
    }
    showLogin()
  }

  /* --------------------------- 登录页 --------------------------- */
  async function showLogin(msg) {
    state.user = null
    API.store.clear()
    stopIdleWatch()
    document.getElementById('app-view').classList.add('hidden')
    const v = document.getElementById('login-view')
    v.classList.remove('hidden')
    v.classList.add('flex')
    if (msg) showLoginError(msg)
    /* 首次部署引导：平台里还没有任何管理员账号时静默初始化（幂等）。
     * 过去这一步依赖公开的医院列表接口，接口移除后改用 /api/setup 的返回值判断。
     * 注意：不在界面上显示初始账号或口令 —— 登录页是公网可见的。 */
    try {
      const s = await API.setup()
      if (s && s.created) UI.toast('系统已完成初始化，请使用管理员账号登录', 'success', 6000)
    } catch { /* 已初始化或暂时不可用，都不影响正常登录 */ }
    document.getElementById('login-username').focus()
  }

  function showLoginError(msg) {
    const el = document.getElementById('login-error')
    el.textContent = msg
    el.classList.remove('hidden')
  }
  function clearLoginError() {
    document.getElementById('login-error').classList.add('hidden')
  }

  /* =========================================================================
   * 空闲自动退出
   *
   * 规则：连续 2 小时无用户操作 → 自动退出到登录页。
   * 实现要点（每一条都是踩过的坑）：
   *   1. 「操作」以真实交互事件为准（点击/按键/滚动/触摸），
   *      **不能**把 fetch 或定时任务算作操作，否则后台轮询会让会话永不过期。
   *   2. 计时基准存在 sessionStorage（API.store.lastActivity），
   *      因此多标签页共享同一会话时各自独立计时，且刷新页面不会把计时清零。
   *   3. 不用一次性 setTimeout(2h)：设备休眠/标签页被冻结时定时器不准。
   *      改为每 30 秒轮询比较时间戳，休眠唤醒后立即能判定已超时。
   *   4. 续期只在「有操作且 access 快到期」时触发，避免每次点击都打一次
   *      /api/auth/refresh；同时保证服务端的 2 小时滑动窗口被推后。
   * ====================================================================== */
  const IDLE_CHECK_MS = 30 * 1000      // 超时检查间隔
  const IDLE_WARN_MS = 5 * 60 * 1000   // 剩余不足 5 分钟时提醒一次
  const RENEW_AFTER_MS = 8 * 60 * 1000 // 距上次续期超过 8 分钟且有操作 → 续期（access 寿命 10 分钟）

  let idleTimer = null
  let lastRenewAt = 0
  let warned = false
  const ACTIVITY_EVENTS = ['click', 'keydown', 'mousedown', 'touchstart', 'wheel', 'scroll']

  function onUserActivity() {
    if (!state.user) return
    API.store.touch()
    warned = false
    /* 顺带把服务端的空闲窗口往后推：仅在距上次续期足够久时才发请求 */
    if (Date.now() - lastRenewAt > RENEW_AFTER_MS) {
      lastRenewAt = Date.now()
      API.refreshSession().catch(() => {})
    }
  }

  async function checkIdle() {
    if (!state.user) return
    const limit = API.store.idleLimitMs
    const idle = API.store.idleMs
    if (idle > limit) {
      stopIdleWatch()
      try { await API.logout() } catch {}
      /* 弹窗/未保存提示都不应拦住安全退出，这里绕过 leaveGuard 直接回登录页 */
      leaveGuard = null
      UI.closeAllModals?.()
      showLogin('已超过 2 小时无操作，为保护患者数据已自动退出，请重新登录')
      return
    }
    const left = limit - idle
    if (left <= IDLE_WARN_MS && !warned) {
      warned = true
      const mins = Math.max(1, Math.ceil(left / 60000))
      UI.toast(`您已长时间未操作，${mins} 分钟后将自动退出登录`, 'warn', 8000)
    }
  }

  function startIdleWatch() {
    stopIdleWatch()
    API.store.touch()
    warned = false
    lastRenewAt = Date.now()
    ACTIVITY_EVENTS.forEach(e => window.addEventListener(e, onUserActivity, { passive: true, capture: true }))
    /* 标签页从后台切回来时立刻判一次，不等下一个 30 秒周期 */
    document.addEventListener('visibilitychange', onVisible)
    idleTimer = setInterval(checkIdle, IDLE_CHECK_MS)
  }

  function stopIdleWatch() {
    if (idleTimer) { clearInterval(idleTimer); idleTimer = null }
    ACTIVITY_EVENTS.forEach(e => window.removeEventListener(e, onUserActivity, { capture: true }))
    document.removeEventListener('visibilitychange', onVisible)
  }

  function onVisible() {
    if (document.visibilityState === 'visible') checkIdle()
  }

  function bindGlobal() {
    /* 密码显示切换 */
    document.getElementById('toggle-pwd').addEventListener('click', () => {
      const inp = document.getElementById('login-password')
      inp.type = inp.type === 'password' ? 'text' : 'password'
      document.querySelector('#toggle-pwd i').className = inp.type === 'password' ? 'fas fa-eye' : 'fas fa-eye-slash'
    })

    /* 登录提交 */
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault()
      clearLoginError()
      const btn = document.getElementById('login-submit')
      const username = document.getElementById('login-username').value.trim()
      const password = document.getElementById('login-password').value
      btn.disabled = true
      btn.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-2"></i>登录中…'
      try {
        /* 不再提交 hospital_id：所属医院由服务端按账号归属自动匹配 */
        const r = await API.login({ username, password })
        API.store.save(
          { access: r.access, refresh: r.refresh, idle_timeout_seconds: r.idle_timeout_seconds },
          r.user
        )
        state.user = r.user
        state.mustChange = !!r.must_change_password
        document.getElementById('login-password').value = ''
        enterApp()
      } catch (err) {
        showLoginError(err.message || '登录失败')
      } finally {
        btn.disabled = false
        btn.innerHTML = '<i class="fas fa-sign-in-alt mr-2"></i>登录'
      }
    })

    /* 忘记密码 */
    document.getElementById('forgot-link').addEventListener('click', () => {
      const u = document.getElementById('login-username').value.trim()
      UI.modal({
        title: '忘记密码',
        size: 'sm',
        body: `<p class="text-sm text-slate-600 leading-relaxed">
            出于医疗数据安全要求，本系统不支持自助重置密码。<br>
            提交申请后，请联系<b>平台管理员</b>为您重置（管理员可在「账号管理」中操作）。
          </p>
          <div class="mt-4"><label class="field-label">您的用户名</label>
          <input id="fp-user" class="field-input" value="${UI.esc(u)}" placeholder="请输入用户名"></div>`,
        footer: `<button data-c class="btn btn-ghost">取消</button><button data-ok class="btn btn-primary">提交申请</button>`,
        onMount(root, close) {
          root.querySelector('[data-c]').addEventListener('click', close)
          root.querySelector('[data-ok]').addEventListener('click', async () => {
            const un = root.querySelector('#fp-user').value.trim()
            if (!un) { UI.toast('请输入用户名', 'warn'); return }
            try {
              const r = await API.post('/api/auth/forgot-password', { username: un })
              close(); UI.toast(r.message || '已提交申请', 'success', 5000)
            } catch (e) { UI.toast(e.message, 'error') }
          })
        }
      })
    })

    /* 退出 */
    document.getElementById('logout-btn').addEventListener('click', async () => {
      if (!(await UI.confirm({ title: '退出登录', message: '确认退出当前账号？', okText: '退出', danger: false }))) return
      try { await API.logout() } catch {}
      showLogin()
      UI.toast('已安全退出', 'success')
    })

    /* 改密 */
    document.getElementById('change-pwd-btn').addEventListener('click', () => changePassword(false))

    /* 返回按钮 */
    document.getElementById('page-back').addEventListener('click', () => back())

    /* 浏览器前后退。
     * 注意：writeHash 用的是 history.pushState，而 pushState **不会**触发 hashchange，
     * 所以只监听 hashchange 时，「点侧栏进详情 → 按浏览器后退」这条链路可能收不到通知。
     * popstate 才是 pushState 的配对事件，两个都监听、由 syncFromHash 去重。 */
    window.addEventListener('hashchange', syncFromHash)
    window.addEventListener('popstate', syncFromHash)
  }

  /* --------------------------- 进入应用 --------------------------- */
  async function enterApp() {
    document.getElementById('login-view').classList.add('hidden')
    document.getElementById('login-view').classList.remove('flex')
    document.getElementById('app-view').classList.remove('hidden')

    state.stack = []
    leaveGuard = null

    /* 会话计时从进入应用起算，并开始监听「无操作」 */
    startIdleWatch()

    renderUserBox()
    renderNav()

    /* 首次登录强制改密 */
    if (state.mustChange) {
      await changePassword(true)
    }

    const { route, params } = parseHash()
    go(ROUTES[route] && allowed(route) ? route : homeOf(state.user.role), params, true)

    refreshCaptureStatus()
  }

  function renderUserBox() {
    const u = state.user
    document.getElementById('user-avatar').textContent = (u.real_name || u.username || '?').slice(0, 1)
    document.getElementById('user-name').textContent = u.real_name || u.username
    document.getElementById('user-role').textContent = UI.fmt.role(u.role)
    const badge = document.getElementById('hospital-badge')
    if (u.role === 'PLATFORM_ADMIN') {
      badge.classList.add('hidden')
    } else {
      badge.classList.remove('hidden')
      badge.innerHTML = `<i class="fas fa-hospital"></i> ${UI.esc(u.hospital_name || '')}`
    }
  }

  const allowed = (key) => ROUTES[key] && ROUTES[key].roles.includes(state.user.role)

  function renderNav() {
    const nav = document.getElementById('main-nav')
    let html = ''
    for (const g of NAV_GROUPS) {
      const keys = g.keys.filter(k => ROUTES[k] && ROUTES[k].nav && allowed(k))
      if (!keys.length) continue
      // 分组标题原为 text-[10px] + slate-500 + uppercase：10px 在深底上又小又灰，
      // 临床反馈看不清。改 12px、提亮到 slate-400；uppercase 对中文无效果，
      // tracking-wider 反而把中文字距拉散，一并去掉。
      html += `<p class="px-3 pt-3.5 pb-1.5 text-xs font-semibold text-slate-400">${g.label}</p>`
      html += keys.map(k => `
        <a href="#/${k}" data-route="${k}" class="nav-item">
          <i class="fas ${ROUTES[k].icon} w-4 text-center"></i>
          <span>${ROUTES[k].title}</span>
        </a>`).join('')
    }
    nav.innerHTML = html
    nav.querySelectorAll('[data-route]').forEach(a => a.addEventListener('click', (e) => {
      e.preventDefault()
      go(a.dataset.route)
    }))
  }

  /* --------------------------- 返回导航 --------------------------- */

  /* 地址栏与当前状态不一致时（浏览器前后退、手改 hash）同步过来 */
  function syncFromHash() {
    if (!state.user) return
    const { route, params } = parseHash()
    if (route !== state.route || JSON.stringify(params) !== JSON.stringify(state.params)) {
      go(route, params, true)
    }
  }

  /* 返回目标：优先「实际来的那一页」（导航栈栈顶），
   * 栈空时（刷新页面、直接粘贴链接进来）退到父页面，保证按钮永远有去处。 */
  function backTarget() {
    for (let i = state.stack.length - 1; i >= 0; i--) {
      const e = state.stack[i]
      if (e && ROUTES[e.route] && allowed(e.route)) return e
    }
    const p = PARENT_OF[state.route]
    return { route: p && allowed(p) ? p : homeOf(state.user.role), params: {} }
  }

  function back() {
    if (!state.user) return
    const def = ROUTES[state.route]
    if (!def || def.nav) return          // 顶层页面没有「上一层」
    const t = backTarget()
    return go(t.route, t.params, false, { back: true })
  }

  /* 顶部返回按钮：只在下钻页面出现，并把目标页名字写在按钮上，
   * 让人点之前就知道会回到哪里，而不是一个含义模糊的箭头。 */
  function renderBack() {
    const btn = document.getElementById('page-back')
    const label = document.getElementById('page-back-label')
    if (!btn) return
    const def = ROUTES[state.route]
    if (!def || def.nav) {
      btn.classList.add('hidden')
      btn.classList.remove('inline-flex')
      return
    }
    const t = backTarget()
    label.textContent = '返回' + ((ROUTES[t.route] && ROUTES[t.route].title) || '')
    btn.classList.remove('hidden')
    btn.classList.add('inline-flex')
  }

  /* 维护导航栈：
   * - 回到栈顶那一页（点返回或浏览器后退）→ 出栈，避免 A→B→A→B 无限堆积
   * - 其余前进动作 → 把离开的页面入栈 */
  function trackStack(prev, route, params, isBack) {
    const same = (a, b) => a && b && a.route === b.route &&
      JSON.stringify(a.params || {}) === JSON.stringify(b.params || {})
    const top = state.stack[state.stack.length - 1]
    if (isBack || same(top, { route, params })) { state.stack.pop(); return }
    if (!prev || !prev.route || same(prev, { route, params })) return
    state.stack.push(prev)
    if (state.stack.length > 20) state.stack.shift()
  }

  /* --------------------------- 路由跳转 --------------------------- */
  function parseHash() {
    const h = location.hash.replace(/^#\/?/, '')
    if (!h) return { route: '', params: {} }
    const [path, qs] = h.split('?')
    const params = {}
    new URLSearchParams(qs || '').forEach((v, k) => { params[k] = v === 'true' ? true : v })
    return { route: path, params }
  }

  function writeHash(route, params) {
    const u = new URLSearchParams()
    Object.entries(params || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.set(k, v) })
    const s = u.toString()
    const target = `#/${route}${s ? '?' + s : ''}`
    if (location.hash !== target) {
      history.pushState(null, '', target)
    }
  }

  async function go(route, params = {}, fromHash = false, opts = {}) {
    if (!state.user) return
    if (!ROUTES[route]) route = homeOf(state.user.role)
    if (!allowed(route)) {
      UI.toast('您没有访问该功能的权限', 'error')
      route = homeOf(state.user.role)
      params = {}
    }

    const prev = state.route ? { route: state.route, params: state.params } : null
    const changing = !prev || prev.route !== route ||
      JSON.stringify(prev.params || {}) !== JSON.stringify(params || {})

    /* 离开确认：报告单等页面有未保存内容时不能被一次点击悄悄丢掉 */
    if (changing && leaveGuard) {
      const ok = await leaveGuard()
      if (!ok) {
        /* 浏览器后退触发时地址栏已经变了，要把它写回去，否则 URL 与页面不一致 */
        if (fromHash) writeHash(prev.route, prev.params)
        return
      }
    }
    leaveGuard = null

    if (changing) trackStack(prev, route, params, !!opts.back)

    state.route = route
    state.params = params
    if (!fromHash) writeHash(route, params)

    /* 高亮侧边栏（子页面高亮其父项） */
    const activeKey = PARENT_OF[route] || route
    document.querySelectorAll('#main-nav [data-route]').forEach(a =>
      a.classList.toggle('active', a.dataset.route === activeKey))

    const def = ROUTES[route]
    document.getElementById('page-title').textContent = def.title
    renderBack()
    const body = document.getElementById('page-body')
    body.innerHTML = `<div class="py-20 text-center text-slate-400"><i class="fas fa-circle-notch fa-spin text-2xl"></i></div>`
    try {
      await def.render(body, params)
    } catch (e) {
      console.error(e)
      body.innerHTML = `<div class="card p-8 text-center">
        <i class="fas fa-triangle-exclamation text-4xl text-amber-400 mb-3"></i>
        <p class="font-medium text-ink-800">页面加载失败</p>
        <p class="text-sm text-slate-500 mt-1">${UI.esc(e.message || String(e))}</p>
        <button class="btn btn-ghost btn-sm mt-4" onclick="App.go('${route}')"><i class="fas fa-rotate"></i>重试</button>
      </div>`
    }
  }

  /* --------------------------- 改密弹窗 --------------------------- */
  function changePassword(forced) {
    return new Promise((resolve) => {
      const m = UI.modal({
        title: forced ? '首次登录，请修改初始密码' : '修改密码',
        size: 'sm',
        closable: !forced,
        body: `
          ${forced ? `<p class="text-sm bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2 mb-4">
            <i class="fas fa-shield-halved"></i> 为保障医疗数据安全，首次登录必须修改初始密码后方可使用系统。
          </p>` : ''}
          <div class="space-y-3">
            <div><label class="field-label">原密码</label>
              <input id="cp-old" type="password" class="field-input" autocomplete="current-password" placeholder="${forced ? '请输入初始密码' : '请输入原密码'}"></div>
            <div><label class="field-label">新密码（至少 8 位）</label>
              <input id="cp-new" type="password" class="field-input" autocomplete="new-password" placeholder="建议包含大小写字母、数字与符号"></div>
            <div><label class="field-label">确认新密码</label>
              <input id="cp-new2" type="password" class="field-input" autocomplete="new-password"></div>
            <p id="cp-err" class="hidden text-sm text-red-600"></p>
          </div>`,
        footer: `${forced ? '' : '<button data-c class="btn btn-ghost">取消</button>'}
                 <button data-ok class="btn btn-primary"><i class="fas fa-key"></i>确认修改</button>`,
        onMount(root, close) {
          const err = root.querySelector('#cp-err')
          root.querySelector('[data-c]')?.addEventListener('click', () => { close(); resolve(false) })
          root.querySelector('[data-ok]').addEventListener('click', async () => {
            err.classList.add('hidden')
            const o = root.querySelector('#cp-old').value
            const n = root.querySelector('#cp-new').value
            const n2 = root.querySelector('#cp-new2').value
            if (n.length < 8) { err.textContent = '新密码至少 8 位'; err.classList.remove('hidden'); return }
            if (n !== n2) { err.textContent = '两次输入的新密码不一致'; err.classList.remove('hidden'); return }
            if (n === o) { err.textContent = '新密码不能与原密码相同'; err.classList.remove('hidden'); return }
            try {
              await API.changePassword({ old_password: o, new_password: n })
              state.mustChange = false
              close()
              UI.toast('密码修改成功', 'success')
              resolve(true)
            } catch (e) {
              err.textContent = e.message || '修改失败'
              err.classList.remove('hidden')
            }
          })
          setTimeout(() => root.querySelector('#cp-old').focus(), 50)
        }
      })
      if (forced) m.root.dataset.forced = '1'
    })
  }

  /* --------------------------- 采集状态 --------------------------- */
  async function refreshCaptureStatus() {
    const el = document.getElementById('capture-status')
    if (!el) return
    if (state.user && state.user.role === 'PLATFORM_ADMIN') { el.innerHTML = ''; return }
    try { await Capture.renderStatus(el) } catch { el.innerHTML = '' }
  }

  /* --------------------------- 对外 API --------------------------- */
  const api = {
    get user() { return state.user },
    get route() { return state.route },
    go,
    back,
    setLeaveGuard: (fn) => { leaveGuard = typeof fn === 'function' ? fn : null },
    showLogin,
    refreshCaptureStatus,
    changePassword,
    ROUTES
  }

  document.addEventListener('DOMContentLoaded', boot)
  return api
})()
