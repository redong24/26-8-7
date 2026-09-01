/* =============================================================================
 * API 客户端：JWT 双 Token 自动续期 + 会话时效管控
 *
 * 会话策略（医疗数据合规：空闲即失效、关浏览器即失效）
 *   - access  10 分钟：过期后由 refresh 静默换新，用户无感
 *   - refresh  2 小时**空闲滑动窗口**：每次续期重新计时；
 *              连续 2 小时无操作 → 服务端 refresh 记录过期 → 必须重新登录
 *
 * 令牌一律只写 **sessionStorage**，不再写 localStorage：
 *   sessionStorage 随标签页/浏览器关闭而销毁，因此「退出浏览器后再打开网址」
 *   一定回到登录页。原先的「记住我（7 天免登录）」已移除 —— 诊室是共用终端，
 *   长期免登录等于把上一位医生的会话留给下一位。
 *
 * 注意：客户端的清理只是体验层；真正的底线在服务端（refresh 记录到期即失效），
 * 即使有人手工把令牌搬进 localStorage 也无法延长会话。
 * ========================================================================== */
window.API = (() => {
  const KEY_A = 'spt_access'
  const KEY_R = 'spt_refresh'
  const KEY_U = 'spt_user'
  const KEY_ACT = 'spt_last_activity'   // 最近一次「用户操作」时间戳（ms）

  /** 空闲上限：2 小时。登录响应里若带 idle_timeout_seconds 则以服务端为准。 */
  let IDLE_LIMIT_MS = 2 * 3600 * 1000

  /* 历史版本可能把令牌持久化在 localStorage（旧「记住我」）。
   * 这里在脚本加载时一次性清掉，避免老用户升级后仍被免登录带进系统。 */
  try { [KEY_A, KEY_R, KEY_U, 'spt_last_hospital'].forEach(k => localStorage.removeItem(k)) } catch {}

  const store = {
    get access() { return sessionStorage.getItem(KEY_A) },
    get refresh() { return sessionStorage.getItem(KEY_R) },
    get user() { try { return JSON.parse(sessionStorage.getItem(KEY_U) || 'null') } catch { return null } },

    /** 空闲上限（毫秒），供前端计时器使用 */
    get idleLimitMs() { return IDLE_LIMIT_MS },
    setIdleLimitSeconds(sec) {
      const n = Number(sec)
      if (Number.isFinite(n) && n >= 60) IDLE_LIMIT_MS = n * 1000
    },

    /** 最近一次用户操作时间（ms）；无会话时返回 0 */
    get lastActivity() { return Number(sessionStorage.getItem(KEY_ACT) || 0) },
    touch() { sessionStorage.setItem(KEY_ACT, String(Date.now())) },
    /** 已空闲毫秒数 */
    get idleMs() {
      const t = store.lastActivity
      return t ? Date.now() - t : 0
    },
    get isIdleExpired() {
      const t = store.lastActivity
      return !!t && Date.now() - t > IDLE_LIMIT_MS
    },

    save(tokens, user) {
      if (tokens?.idle_timeout_seconds) store.setIdleLimitSeconds(tokens.idle_timeout_seconds)
      if (tokens?.access) sessionStorage.setItem(KEY_A, tokens.access)
      if (tokens?.refresh) sessionStorage.setItem(KEY_R, tokens.refresh)
      if (user) sessionStorage.setItem(KEY_U, JSON.stringify(user))
      store.touch()
    },
    updateTokens(tokens) {
      if (tokens?.idle_timeout_seconds) store.setIdleLimitSeconds(tokens.idle_timeout_seconds)
      if (tokens?.access) sessionStorage.setItem(KEY_A, tokens.access)
      if (tokens?.refresh) sessionStorage.setItem(KEY_R, tokens.refresh)
    },
    clear() {
      ;[KEY_A, KEY_R, KEY_U, KEY_ACT].forEach(k => { sessionStorage.removeItem(k); localStorage.removeItem(k) })
    }
  }

  let refreshing = null

  async function doRefresh() {
    if (refreshing) return refreshing
    const rt = store.refresh
    if (!rt) return null
    /* 已经超过空闲上限：不再尝试续期（服务端也会拒），直接判定会话结束。
     * 否则「离开 3 小时后回来点一下」会先被续期成功、再被放进系统。 */
    if (store.isIdleExpired) { store.clear(); return null }
    refreshing = fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: rt })
    }).then(async r => {
      if (!r.ok) { store.clear(); return null }
      const t = await r.json()
      store.updateTokens(t)
      return t.access
    }).catch(() => null).finally(() => { refreshing = null })
    return refreshing
  }

  async function request(method, path, body, opts = {}) {
    const headers = Object.assign({}, opts.headers || {})
    if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
    const token = store.access
    if (token) headers['Authorization'] = 'Bearer ' + token

    const init = { method, headers }
    if (body !== undefined) init.body = body instanceof FormData ? body : JSON.stringify(body)

    let resp = await fetch(path, init)

    if (resp.status === 401 && store.refresh && !opts._retried) {
      const newToken = await doRefresh()
      if (newToken) return request(method, path, body, Object.assign({}, opts, { _retried: true }))
      store.clear()
      if (window.App?.showLogin) window.App.showLogin('会话已超时，请重新登录')
      throw new Error('会话已超时')
    }

    if (opts.raw) return resp

    const ct = resp.headers.get('content-type') || ''
    const data = ct.includes('application/json') ? await resp.json().catch(() => ({})) : await resp.text()
    if (!resp.ok) {
      const msg = (data && data.message) || (data && data.error) || ('请求失败 ' + resp.status)
      const err = new Error(msg)
      err.status = resp.status
      err.payload = data
      throw err
    }
    return data
  }

  const qs = (params) => {
    const u = new URLSearchParams()
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') u.set(k, v)
    })
    const s = u.toString()
    return s ? '?' + s : ''
  }

  return {
    store, qs,
    get:  (p, params, o) => request('GET', p + qs(params), undefined, o),
    post: (p, b, o) => request('POST', p, b ?? {}, o),
    put:  (p, b, o) => request('PUT', p, b ?? {}, o),
    patch: (p, b, o) => request('PATCH', p, b ?? {}, o),
    del:  (p, o) => request('DELETE', p, undefined, o),

    /* --------- 认证 --------- */
    /* 医院不再由登录页选择，改为按账号归属自动匹配（见 src/routes/auth.ts 的 /login 注释），
     * 原 hospitalsPublic()（公开医院列表）已随后端接口一并移除。 */
    login: (payload) => request('POST', '/api/auth/login', payload),
    /** 主动续期：仅在用户有操作时调用，把 2 小时空闲窗口往后推 */
    refreshSession: () => doRefresh(),
    me: () => request('GET', '/api/auth/me'),
    logout: () => request('POST', '/api/auth/logout', {}),
    changePassword: (b) => request('POST', '/api/auth/change-password', b),
    setup: () => request('POST', '/api/setup', {}),

    /** 下载：带 Authorization 头取 blob 后触发保存 */
    async download(path, body, filename) {
      const headers = { 'Authorization': 'Bearer ' + store.access }
      let init = { method: body ? 'POST' : 'GET', headers }
      if (body) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body) }
      let resp = await fetch(path, init)
      if (resp.status === 401) {
        const t = await doRefresh()
        if (t) { headers['Authorization'] = 'Bearer ' + t; resp = await fetch(path, init) }
      }
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}))
        throw new Error(j.message || j.error || '导出失败')
      }
      const ct = resp.headers.get('content-type') || ''
      if (ct.includes('text/html')) {
        const html = await resp.text()
        const w = window.open('', '_blank')
        w.document.write(html); w.document.close()
        return
      }
      const blob = await resp.blob()
      const cd = resp.headers.get('content-disposition') || ''
      const m = /filename="?([^";]+)"?/.exec(cd)
      const name = filename || (m ? m[1] : 'download')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = name; document.body.appendChild(a); a.click()
      setTimeout(() => { URL.revokeObjectURL(url); a.remove() }, 1500)
    },

    /** 受鉴权的图片：取 blob → objectURL */
    async imageUrl(path) {
      try {
        const resp = await fetch(path, { headers: { 'Authorization': 'Bearer ' + store.access } })
        if (!resp.ok) return ''
        return URL.createObjectURL(await resp.blob())
      } catch { return '' }
    }
  }
})()
