import { Hono } from "hono";
import { cors } from "hono/cors";
import { logger } from "hono/logger";
//#region \0rolldown/runtime.js
var __defProp = Object.defineProperty;
var __exportAll = (all, no_symbols) => {
	let target = {};
	for (var name in all) __defProp(target, name, {
		get: all[name],
		enumerable: true
	});
	if (!no_symbols) __defProp(target, Symbol.toStringTag, { value: "Module" });
	return target;
};
//#endregion
//#region src/lib/types.ts
var PLATFORM_TENANT = "PLATFORM";
/** 普通位置号必须落在 1..MAX_POS_COUNT（天然不与对照位号 101/102 冲突） */
function isNormalPos(pos) {
	return Number.isInteger(pos) && pos >= 1 && pos <= 100;
}
/**
* 由输入行推导实际位置数：取最大有效位置号，且不低于默认 20。
* 用于模版补齐、报告单快照构造、导出列数生成三处保持一致。
*/
function resolvePosCount(input) {
	let max = 0;
	if (Array.isArray(input)) for (const r of input) {
		const pos = Number(r?.position_no);
		if (isNormalPos(pos) && pos > max) max = pos;
	}
	return Math.max(20, max);
}
//#endregion
//#region node_modules/hono/dist/helper/factory/index.js
var createMiddleware = (middleware) => middleware;
//#endregion
//#region src/lib/crypto.ts
/**
* 密码哈希与 JWT —— 全部基于 WebCrypto，兼容 Cloudflare Workers 运行时。
* 说明: 边缘运行时无法使用 bcrypt/argon2 原生模块，改用 PBKDF2-SHA256 (100k 迭代)，
*       属于 OWASP 推荐的可接受方案之一。
*/
var enc = new TextEncoder();
var dec = new TextDecoder();
var PBKDF2_ITERATIONS = 1e5;
function bufToB64(buf) {
	const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
	let s = "";
	for (const b of bytes) s += String.fromCharCode(b);
	return btoa(s);
}
function b64ToBuf(b64) {
	const s = atob(b64);
	const out = new Uint8Array(s.length);
	for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
	return out;
}
/**
* base64url 编码。
* 注意：字符串先经 UTF-8 编码为字节，再 base64——否则中文姓名/医院名会触发
* btoa() Latin1 范围错误（Workers 与浏览器一致）。
*/
function b64url(input) {
	return bufToB64(typeof input === "string" ? enc.encode(input) : input).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
/** base64url 解码回 UTF-8 字符串 */
function b64urlDecodeToString(input) {
	const b64 = input.replace(/-/g, "+").replace(/_/g, "/");
	const pad = b64.length % 4 ? "=".repeat(4 - b64.length % 4) : "";
	return dec.decode(b64ToBuf(b64 + pad));
}
async function hashPassword(password) {
	const salt = crypto.getRandomValues(/* @__PURE__ */ new Uint8Array(16));
	const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
	const bits = await crypto.subtle.deriveBits({
		name: "PBKDF2",
		salt,
		iterations: PBKDF2_ITERATIONS,
		hash: "SHA-256"
	}, key, 256);
	return `pbkdf2$${PBKDF2_ITERATIONS}$${bufToB64(salt)}$${bufToB64(bits)}`;
}
async function verifyPassword(password, stored) {
	try {
		const [scheme, iterStr, saltB64, hashB64] = stored.split("$");
		if (scheme !== "pbkdf2") return false;
		const salt = b64ToBuf(saltB64);
		const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
		const bits = await crypto.subtle.deriveBits({
			name: "PBKDF2",
			salt,
			iterations: Number(iterStr),
			hash: "SHA-256"
		}, key, 256);
		const a = new Uint8Array(bits);
		const b = b64ToBuf(hashB64);
		if (a.length !== b.length) return false;
		let diff = 0;
		for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
		return diff === 0;
	} catch {
		return false;
	}
}
async function hmacKey(secret) {
	return crypto.subtle.importKey("raw", enc.encode(secret), {
		name: "HMAC",
		hash: "SHA-256"
	}, false, ["sign", "verify"]);
}
async function signJwt(payload, secret) {
	const data = `${b64url(JSON.stringify({
		alg: "HS256",
		typ: "JWT"
	}))}.${b64url(JSON.stringify({
		...payload,
		iat: Math.floor(Date.now() / 1e3)
	}))}`;
	const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(data));
	return `${data}.${b64url(new Uint8Array(sig))}`;
}
async function verifyJwt(token, secret) {
	try {
		const parts = token.split(".");
		if (parts.length !== 3) return null;
		const data = `${parts[0]}.${parts[1]}`;
		const sigBytes = b64ToBuf(parts[2].replace(/-/g, "+").replace(/_/g, "/"));
		if (!await crypto.subtle.verify("HMAC", await hmacKey(secret), sigBytes, enc.encode(data))) return null;
		const payload = JSON.parse(b64urlDecodeToString(parts[1]));
		if (payload.exp && payload.exp < Math.floor(Date.now() / 1e3)) return null;
		return payload;
	} catch {
		return null;
	}
}
function uuid() {
	return crypto.randomUUID();
}
async function sha256Hex(data) {
	const buf = typeof data === "string" ? enc.encode(data) : data;
	const digest = await crypto.subtle.digest("SHA-256", buf);
	return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function randomPairCode() {
	const n = crypto.getRandomValues(/* @__PURE__ */ new Uint32Array(1))[0] % 1e6;
	return String(n).padStart(6, "0");
}
//#endregion
//#region src/lib/middleware.ts
function jwtSecret(env) {
	return env.JWT_SECRET || "dev-insecure-secret-change-me-in-production";
}
/**
* authGuard —— 校验 access token，注入 c.var.user
*/
var authGuard = createMiddleware(async (c, next) => {
	const header = c.req.header("Authorization") || "";
	const token = header.startsWith("Bearer ") ? header.slice(7) : "";
	if (!token) return c.json({
		error: "UNAUTHORIZED",
		message: "未登录"
	}, 401);
	const payload = await verifyJwt(token, jwtSecret(c.env));
	if (!payload || payload.typ !== "access") return c.json({
		error: "UNAUTHORIZED",
		message: "登录已过期，请重新登录"
	}, 401);
	c.set("user", {
		id: payload.sub,
		username: payload.username,
		real_name: payload.real_name,
		role: payload.role,
		hospital_id: payload.hospital_id
	});
	await next();
});
/**
* tenantGuard —— 医院级严格隔离底线。
*
* 1. 作用域 **只** 从 JWT 取，绝不接受请求体/查询串中的 hospital_id
* 2. 任何非平台管理员的请求，若在 body/query 中携带 hospital_id 直接 400 拒绝
* 3. 平台管理员可通过 ?hospital_id= 显式切换作用域（仅平台级 API）
*/
var tenantGuard = createMiddleware(async (c, next) => {
	const user = c.var.user;
	if (!(user.role === "PLATFORM_ADMIN")) {
		if (c.req.query("hospital_id")) return c.json({
			error: "FORBIDDEN_TENANT_PARAM",
			message: "禁止指定 hospital_id 参数，作用域由登录身份决定"
		}, 400);
		if ([
			"POST",
			"PUT",
			"PATCH"
		].includes(c.req.method)) {
			if ((c.req.header("content-type") || "").includes("application/json")) try {
				const raw = await c.req.raw.clone().text();
				if (raw && /"hospital_id"\s*:/.test(raw)) return c.json({
					error: "FORBIDDEN_TENANT_PARAM",
					message: "禁止在请求体中携带 hospital_id"
				}, 400);
			} catch {}
		}
		if (!user.hospital_id || user.hospital_id === "PLATFORM") return c.json({
			error: "NO_TENANT",
			message: "账号未归属任何医院"
		}, 403);
		c.set("tenant", user.hospital_id);
	} else c.set("tenant", c.req.query("hospital_id") || "PLATFORM");
	await next();
});
/** 角色白名单 */
function requireRole(...roles) {
	return createMiddleware(async (c, next) => {
		if (!roles.includes(c.var.user.role)) return c.json({
			error: "FORBIDDEN",
			message: "当前角色无此权限"
		}, 403);
		await next();
	});
}
/** 医疗侧（医生 + 护士），排除平台管理员 —— admin 不得触碰临床数据 */
var clinicalOnly = requireRole("DOCTOR", "NURSE");
var platformOnly = requireRole("PLATFORM_ADMIN");
/**
* 审计日志写入
*/
async function audit(c, action, opts = {}) {
	try {
		const user = c.var?.user;
		const actorId = opts.actor?.id ?? user?.id ?? null;
		const actorName = opts.actor?.real_name ?? user?.real_name ?? null;
		const actorRole = opts.actor?.role ?? user?.role ?? null;
		const hid = opts.hospital_id ?? c.var?.tenant ?? user?.hospital_id ?? null;
		await c.env.DB.prepare(`INSERT INTO audit_log
        (actor_id, actor_name, actor_role, hospital_id, action, resource_type, resource_id,
         before_json, after_json, ip, user_agent)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)`).bind(actorId, actorName, actorRole, hid, action, opts.resource_type ?? null, opts.resource_id ?? null, opts.before ? JSON.stringify(opts.before) : null, opts.after ? JSON.stringify(opts.after) : null, c.req.header("cf-connecting-ip") || c.req.header("x-forwarded-for") || null, c.req.header("user-agent") || null).run();
	} catch (e) {
		console.error("audit failed", e);
	}
}
/**
* assertSameTenant —— 跨表 JOIN 后的二次校验（硬约束 6.2.4）
*/
function assertSameTenant(tenant, ...rows) {
	for (const r of rows) {
		if (!r) return false;
		if (r.hospital_id !== tenant) return false;
	}
	return true;
}
//#endregion
//#region src/lib/storage.ts
var storage_exports = /* @__PURE__ */ __exportAll({
	decodeImagePayload: () => decodeImagePayload,
	deleteFile: () => deleteFile,
	getFile: () => getFile,
	hospitalOfKey: () => hospitalOfKey,
	putFile: () => putFile
});
function extOf(contentType) {
	if (contentType.includes("png")) return "png";
	if (contentType.includes("webp")) return "webp";
	if (contentType.includes("pdf")) return "pdf";
	if (contentType.includes("spreadsheet") || contentType.includes("excel")) return "xlsx";
	if (contentType.includes("csv")) return "csv";
	if (contentType.includes("zip")) return "zip";
	return "jpg";
}
/** 解析 dataURL / 纯 base64 → 字节 */
function decodeImagePayload(input) {
	let contentType = "image/jpeg";
	let b64 = input.trim();
	const m = /^data:([^;]+);base64,(.*)$/s.exec(b64);
	if (m) {
		contentType = m[1];
		b64 = m[2];
	}
	b64 = b64.replace(/\s/g, "");
	const bin = atob(b64);
	const bytes = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
	return {
		bytes,
		contentType
	};
}
async function putFile(env, prefix, hospitalId, bytes, contentType) {
	const hash = await sha256Hex(bytes);
	const key = `${prefix}/${hospitalId}/${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}/${uuid()}.${extOf(contentType)}`;
	if (env.R2) await env.R2.put(key, bytes, { httpMetadata: { contentType } });
	else {
		let s = "";
		const CH = 8192;
		for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode(...bytes.subarray(i, i + CH));
		await env.DB.prepare(`INSERT INTO file_blob (key, hospital_id, content_type, size, data_b64) VALUES (?,?,?,?,?)`).bind(key, hospitalId, contentType, bytes.length, btoa(s)).run();
	}
	return {
		key,
		size: bytes.length,
		hash,
		content_type: contentType
	};
}
async function getFile(env, key) {
	if (env.R2) {
		const obj = await env.R2.get(key);
		if (!obj) return null;
		return {
			body: obj.body,
			contentType: obj.httpMetadata?.contentType || "application/octet-stream"
		};
	}
	const row = await env.DB.prepare(`SELECT content_type, data_b64 FROM file_blob WHERE key = ?`).bind(key).first();
	if (!row) return null;
	const bin = atob(row.data_b64);
	const bytes = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
	return {
		body: bytes.buffer,
		contentType: row.content_type
	};
}
async function deleteFile(env, key) {
	if (env.R2) await env.R2.delete(key);
	else await env.DB.prepare(`DELETE FROM file_blob WHERE key = ?`).bind(key).run();
}
/** 从 key 中提取 hospital_id 段，用于下载鉴权 */
function hospitalOfKey(key) {
	const parts = key.split("/");
	return parts.length >= 2 ? parts[1] : null;
}
//#endregion
//#region src/routes/auth.ts
var auth = new Hono();
var ACCESS_TTL = 600;
var IDLE_TTL = 7200;
var MAX_FAILED$1 = 5;
var LOCK_MINUTES$1 = 10;
async function issueTokens(c, u) {
	const jti = uuid();
	const now = Math.floor(Date.now() / 1e3);
	const base = {
		sub: u.id,
		username: u.username,
		real_name: u.real_name,
		role: u.role,
		hospital_id: u.hospital_id
	};
	const access = await signJwt({
		...base,
		typ: "access",
		exp: now + ACCESS_TTL
	}, jwtSecret(c.env));
	const refresh = await signJwt({
		...base,
		typ: "refresh",
		jti,
		exp: now + IDLE_TTL
	}, jwtSecret(c.env));
	await c.env.DB.prepare(`INSERT INTO refresh_token (jti, user_id, hospital_id, expires_at)
     VALUES (?,?,?,datetime('now','+${IDLE_TTL} seconds'))`).bind(jti, u.id, u.hospital_id).run();
	return {
		access,
		refresh,
		idle_timeout_seconds: IDLE_TTL,
		access_expires_in: ACCESS_TTL
	};
}
/** 登录
*
* 医院不再由用户在登录页选择，而是**由账号自身归属自动确定**（user_account.hospital_id）。
* 理由：
*   1. 少一个必填项 —— 临床反馈选错医院导致的登录失败占失败量的一大半；
*   2. 更安全 —— 公网登录页不再枚举全部医院列表（原 /api/auth/hospitals 已删除），
*      也不再接受客户端传入的 hospital_id，杜绝以入参试探他院的可能。
* 兼容：老前端仍可能带 hospital_id 字段，服务端一律**忽略**，不再做匹配校验。
*/
auth.post("/login", async (c) => {
	const body = await c.req.json();
	const username = (body.username || "").trim();
	const password = body.password || "";
	if (!username || !password) return c.json({
		error: "BAD_REQUEST",
		message: "请输入用户名与密码"
	}, 400);
	const u = await c.env.DB.prepare(`SELECT * FROM user_account WHERE username = ?`).bind(username).first();
	if (!u) {
		await audit(c, "LOGIN_FAILED", {
			resource_type: "user_account",
			after: {
				username,
				reason: "NOT_FOUND"
			}
		});
		return c.json({
			error: "INVALID_CREDENTIALS",
			message: "用户名或密码错误"
		}, 401);
	}
	if (!u.is_active) return c.json({
		error: "DISABLED",
		message: "账号已停用"
	}, 403);
	if (u.locked_until) {
		const lockRow = await c.env.DB.prepare(`SELECT CASE WHEN locked_until > datetime('now') THEN 1 ELSE 0 END AS locked,
              CAST((julianday(locked_until) - julianday('now')) * 1440 AS INT) AS mins
       FROM user_account WHERE id = ?`).bind(u.id).first();
		if (lockRow?.locked) return c.json({
			error: "LOCKED",
			message: `账号已锁定，请在 ${Math.max(1, lockRow.mins ?? 1)} 分钟后重试`
		}, 423);
	}
	if (!await verifyPassword(password, u.password_hash)) {
		const attempts = (u.failed_attempts ?? 0) + 1;
		if (attempts >= MAX_FAILED$1) {
			await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = 0, locked_until = datetime('now', '+${LOCK_MINUTES$1} minutes') WHERE id = ?`).bind(u.id).run();
			await audit(c, "ACCOUNT_LOCKED", {
				resource_type: "user_account",
				resource_id: u.id,
				hospital_id: u.hospital_id
			});
			return c.json({
				error: "LOCKED",
				message: `连续 ${MAX_FAILED$1} 次失败，账号锁定 ${LOCK_MINUTES$1} 分钟`
			}, 423);
		}
		await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = ? WHERE id = ?`).bind(attempts, u.id).run();
		await audit(c, "LOGIN_FAILED", {
			resource_type: "user_account",
			resource_id: u.id,
			hospital_id: u.hospital_id,
			after: { attempts }
		});
		return c.json({
			error: "INVALID_CREDENTIALS",
			message: `用户名或密码错误（剩余 ${MAX_FAILED$1 - attempts} 次机会）`
		}, 401);
	}
	if (u.role !== "PLATFORM_ADMIN") {
		const h = await c.env.DB.prepare(`SELECT is_active FROM hospital WHERE id = ?`).bind(u.hospital_id).first();
		if (!h) {
			await audit(c, "LOGIN_FAILED", {
				resource_type: "user_account",
				resource_id: u.id,
				hospital_id: u.hospital_id,
				after: { reason: "HOSPITAL_NOT_FOUND" }
			});
			return c.json({
				error: "HOSPITAL_INVALID",
				message: "账号所属医院不存在，请联系平台管理员"
			}, 403);
		}
		if (!h.is_active) {
			await audit(c, "LOGIN_FAILED", {
				resource_type: "user_account",
				resource_id: u.id,
				hospital_id: u.hospital_id,
				after: { reason: "HOSPITAL_DISABLED" }
			});
			return c.json({
				error: "HOSPITAL_DISABLED",
				message: "所属医院已停用"
			}, 403);
		}
	}
	await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = 0, locked_until = NULL, last_login_at = datetime('now') WHERE id = ?`).bind(u.id).run();
	const tokens = await issueTokens(c, u);
	await audit(c, "LOGIN_SUCCESS", {
		resource_type: "user_account",
		resource_id: u.id,
		hospital_id: u.hospital_id,
		actor: {
			id: u.id,
			real_name: u.real_name,
			role: u.role
		}
	});
	let hospitalName = "平台";
	if (u.hospital_id !== "PLATFORM") hospitalName = (await c.env.DB.prepare(`SELECT name FROM hospital WHERE id = ?`).bind(u.hospital_id).first())?.name ?? "";
	return c.json({
		...tokens,
		must_change_password: !!u.must_change_password,
		user: {
			id: u.id,
			username: u.username,
			real_name: u.real_name,
			role: u.role,
			hospital_id: u.hospital_id,
			hospital_name: hospitalName
		}
	});
});
/** 刷新 token —— 同时是「空闲计时器复位」的唯一入口
*
* 旧 refresh 立即吊销并签发新的一对，新 refresh 的到期时间重新拨到 2 小时后（滑动窗口）。
* 因此只要用户在操作（前端仅在有操作时续期），会话就一直有效；
* 一旦连续 2 小时无操作，数据库里的 refresh 记录过期，此接口返回 SESSION_EXPIRED。
*/
auth.post("/refresh", async (c) => {
	const { refresh } = await c.req.json();
	if (!refresh) return c.json({ error: "BAD_REQUEST" }, 400);
	const payload = await verifyJwt(refresh, jwtSecret(c.env));
	if (!payload || payload.typ !== "refresh" || !payload.jti) return c.json({
		error: "UNAUTHORIZED",
		message: "刷新令牌无效"
	}, 401);
	if (!await c.env.DB.prepare(`SELECT * FROM refresh_token WHERE jti = ? AND revoked = 0 AND expires_at > datetime('now')`).bind(payload.jti).first()) return c.json({
		error: "SESSION_EXPIRED",
		message: "会话已超时（超过 2 小时无操作），请重新登录"
	}, 401);
	const u = await c.env.DB.prepare(`SELECT * FROM user_account WHERE id = ? AND is_active = 1`).bind(payload.sub).first();
	if (!u) return c.json({ error: "UNAUTHORIZED" }, 401);
	if (u.role !== "PLATFORM_ADMIN") {
		if (!(await c.env.DB.prepare(`SELECT is_active FROM hospital WHERE id = ?`).bind(u.hospital_id).first())?.is_active) {
			await c.env.DB.prepare(`UPDATE refresh_token SET revoked = 1 WHERE user_id = ?`).bind(u.id).run();
			return c.json({
				error: "HOSPITAL_DISABLED",
				message: "所属医院已停用"
			}, 403);
		}
	}
	await c.env.DB.prepare(`UPDATE refresh_token SET revoked = 1 WHERE jti = ?`).bind(payload.jti).run();
	const tokens = await issueTokens(c, u);
	return c.json(tokens);
});
/** 当前用户 */
auth.get("/me", authGuard, async (c) => {
	const u = c.var.user;
	let hospitalName = "平台";
	if (u.hospital_id !== "PLATFORM") hospitalName = (await c.env.DB.prepare(`SELECT name FROM hospital WHERE id = ?`).bind(u.hospital_id).first())?.name ?? "";
	const row = await c.env.DB.prepare(`SELECT must_change_password FROM user_account WHERE id = ?`).bind(u.id).first();
	return c.json({
		user: {
			...u,
			hospital_name: hospitalName
		},
		must_change_password: !!row?.must_change_password
	});
});
/** 改密（含首次强制改密） */
auth.post("/change-password", authGuard, async (c) => {
	const { old_password, new_password } = await c.req.json();
	if (!new_password || new_password.length < 8) return c.json({
		error: "WEAK_PASSWORD",
		message: "新密码至少 8 位"
	}, 400);
	const u = await c.env.DB.prepare(`SELECT * FROM user_account WHERE id = ?`).bind(c.var.user.id).first();
	if (!u || !await verifyPassword(old_password || "", u.password_hash)) return c.json({
		error: "INVALID_CREDENTIALS",
		message: "原密码错误"
	}, 401);
	await c.env.DB.prepare(`UPDATE user_account SET password_hash = ?, must_change_password = 0, updated_at = datetime('now') WHERE id = ?`).bind(await hashPassword(new_password), u.id).run();
	await audit(c, "CHANGE_PASSWORD", {
		resource_type: "user_account",
		resource_id: u.id,
		hospital_id: u.hospital_id
	});
	return c.json({ ok: true });
});
/** 登出：吊销全部 refresh token */
auth.post("/logout", authGuard, async (c) => {
	await c.env.DB.prepare(`UPDATE refresh_token SET revoked = 1 WHERE user_id = ?`).bind(c.var.user.id).run();
	await audit(c, "LOGOUT", {
		resource_type: "user_account",
		resource_id: c.var.user.id
	});
	return c.json({ ok: true });
});
/** 忘记密码：提交找回申请（平台管理员在审计日志中处理） */
auth.post("/forgot-password", async (c) => {
	const { username } = await c.req.json();
	await audit(c, "PASSWORD_RESET_REQUEST", {
		resource_type: "user_account",
		after: { username }
	});
	return c.json({
		ok: true,
		message: "已提交重置申请，请联系平台管理员为您重置密码"
	});
});
//#endregion
//#region src/routes/admin.ts
var admin = new Hono();
admin.use("*", authGuard, platformOnly);
admin.get("/hospitals", async (c) => {
	const kw = (c.req.query("kw") || "").trim();
	const sql = kw ? `SELECT * FROM hospital WHERE name LIKE ? OR code LIKE ? ORDER BY created_at DESC` : `SELECT * FROM hospital ORDER BY created_at DESC`;
	const list = (await (kw ? c.env.DB.prepare(sql).bind(`%${kw}%`, `%${kw}%`) : c.env.DB.prepare(sql)).all()).results ?? [];
	for (const h of list) {
		const s = await c.env.DB.prepare(`SELECT
         (SELECT COUNT(*) FROM user_account WHERE hospital_id = ? AND role='DOCTOR') AS doctors,
         (SELECT COUNT(*) FROM user_account WHERE hospital_id = ? AND role='NURSE') AS nurses,
         (SELECT COUNT(*) FROM patient WHERE hospital_id = ?) AS patients,
         (SELECT COUNT(*) FROM spt_report WHERE hospital_id = ?) AS reports`).bind(h.id, h.id, h.id, h.id).first();
		Object.assign(h, s);
	}
	return c.json({ data: list });
});
admin.post("/hospitals", async (c) => {
	const b = await c.req.json();
	if (!b.name || !b.code) return c.json({
		error: "BAD_REQUEST",
		message: "医院名称与编码必填"
	}, 400);
	if (await c.env.DB.prepare(`SELECT id FROM hospital WHERE code = ?`).bind(b.code).first()) return c.json({
		error: "DUPLICATE",
		message: "医院编码已存在"
	}, 409);
	const id = uuid();
	await c.env.DB.prepare(`INSERT INTO hospital (id, name, code, address, phone, logo_url, is_active) VALUES (?,?,?,?,?,?,?)`).bind(id, b.name, b.code, b.address ?? null, b.phone ?? null, b.logo_url ?? null, b.is_active === false ? 0 : 1).run();
	await audit(c, "CREATE_HOSPITAL", {
		resource_type: "hospital",
		resource_id: id,
		after: b,
		hospital_id: id
	});
	return c.json({ id });
});
admin.put("/hospitals/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const before = await c.env.DB.prepare(`SELECT * FROM hospital WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	await c.env.DB.prepare(`UPDATE hospital SET name=?, code=?, address=?, phone=?, logo_url=?, is_active=?, updated_at=datetime('now') WHERE id=?`).bind(b.name ?? before.name, b.code ?? before.code, b.address ?? before.address, b.phone ?? before.phone, b.logo_url ?? before.logo_url, b.is_active === void 0 ? before.is_active : b.is_active ? 1 : 0, id).run();
	await audit(c, "UPDATE_HOSPITAL", {
		resource_type: "hospital",
		resource_id: id,
		before,
		after: b,
		hospital_id: id
	});
	return c.json({ ok: true });
});
admin.delete("/hospitals/:id", async (c) => {
	const id = c.req.param("id");
	if (((await c.env.DB.prepare(`SELECT (SELECT COUNT(*) FROM patient WHERE hospital_id=?) + (SELECT COUNT(*) FROM spt_report WHERE hospital_id=?) AS c`).bind(id, id).first())?.c ?? 0) > 0) return c.json({
		error: "IN_USE",
		message: "该医院下已有患者或报告单，仅可停用不可删除"
	}, 409);
	const before = await c.env.DB.prepare(`SELECT * FROM hospital WHERE id = ?`).bind(id).first();
	await c.env.DB.prepare(`DELETE FROM user_account WHERE hospital_id = ?`).bind(id).run();
	await c.env.DB.prepare(`DELETE FROM hospital WHERE id = ?`).bind(id).run();
	await audit(c, "DELETE_HOSPITAL", {
		resource_type: "hospital",
		resource_id: id,
		before,
		hospital_id: id
	});
	return c.json({ ok: true });
});
/* ==================== 报告单自定义打印版式（print_template） ====================
 * 平台管理员为各医院配置报告单的打印版式：表头（医院名/logo/标题）、
 * 患者信息字段、表格版式、页脚、纸张。临床端打印时按 hospital_id 拉取。
 * config_json 由前端 PrintRender.normalizeConfig 定义与兜底，后端只做
 * JSON 合法性与大小校验，不逐字段校验 —— 版式字段会持续增补，
 * 后端逐字段白名单只会造成"前端已支持、后端拒收"的锁死。 */
admin.get("/print-templates", async (c) => {
	const hid = c.req.query("hospital_id");
	const sql = hid ? `SELECT p.*, h.name AS hospital_name FROM print_template p JOIN hospital h ON h.id = p.hospital_id WHERE p.hospital_id = ? ORDER BY p.created_at DESC` : `SELECT p.*, h.name AS hospital_name FROM print_template p JOIN hospital h ON h.id = p.hospital_id ORDER BY h.name, p.created_at DESC`;
	const r = await (hid ? c.env.DB.prepare(sql).bind(hid) : c.env.DB.prepare(sql)).all();
	return c.json({ data: r.results ?? [] });
});
admin.post("/print-templates", async (c) => {
	const b = await c.req.json();
	if (!b.hospital_id || !b.name || !b.config_json) return c.json({
		error: "BAD_REQUEST",
		message: "医院、模板名称与版式配置必填"
	}, 400);
	if (typeof b.config_json !== "string" || b.config_json.length > 65536) return c.json({
		error: "BAD_REQUEST",
		message: "版式配置必须是 JSON 字符串且不超过 64KB"
	}, 400);
	try {
		JSON.parse(b.config_json);
	} catch {
		return c.json({
			error: "BAD_REQUEST",
			message: "版式配置不是合法 JSON"
		}, 400);
	}
	if (!await c.env.DB.prepare(`SELECT id FROM hospital WHERE id = ?`).bind(b.hospital_id).first()) return c.json({
		error: "NOT_FOUND",
		message: "医院不存在"
	}, 404);
	const id = uuid();
	if (b.is_default) await c.env.DB.prepare(`UPDATE print_template SET is_default = 0 WHERE hospital_id = ?`).bind(b.hospital_id).run();
	await c.env.DB.prepare(`INSERT INTO print_template (id, hospital_id, name, config_json, is_default, is_active, created_by) VALUES (?,?,?,?,?,?,?)`).bind(id, b.hospital_id, b.name, b.config_json, b.is_default ? 1 : 0, b.is_active === false ? 0 : 1, c.var.user.id).run();
	await audit(c, "CREATE_PRINT_TEMPLATE", {
		resource_type: "print_template",
		resource_id: id,
		after: { name: b.name, hospital_id: b.hospital_id },
		hospital_id: b.hospital_id
	});
	return c.json({ id });
});
admin.put("/print-templates/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const before = await c.env.DB.prepare(`SELECT * FROM print_template WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	if (b.config_json !== void 0) {
		if (typeof b.config_json !== "string" || b.config_json.length > 65536) return c.json({
			error: "BAD_REQUEST",
			message: "版式配置必须是 JSON 字符串且不超过 64KB"
		}, 400);
		try {
			JSON.parse(b.config_json);
		} catch {
			return c.json({
				error: "BAD_REQUEST",
				message: "版式配置不是合法 JSON"
			}, 400);
		}
	}
	if (b.is_default) await c.env.DB.prepare(`UPDATE print_template SET is_default = 0 WHERE hospital_id = ? AND id != ?`).bind(before.hospital_id, id).run();
	await c.env.DB.prepare(`UPDATE print_template SET name=?, config_json=?, is_default=?, is_active=?, updated_at=datetime('now') WHERE id=?`).bind(b.name ?? before.name, b.config_json ?? before.config_json, b.is_default === void 0 ? before.is_default : b.is_default ? 1 : 0, b.is_active === void 0 ? before.is_active : b.is_active ? 1 : 0, id).run();
	await audit(c, "UPDATE_PRINT_TEMPLATE", {
		resource_type: "print_template",
		resource_id: id,
		before: { name: before.name },
		after: { name: b.name ?? before.name },
		hospital_id: before.hospital_id
	});
	return c.json({ ok: true });
});
admin.delete("/print-templates/:id", async (c) => {
	const id = c.req.param("id");
	const before = await c.env.DB.prepare(`SELECT * FROM print_template WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	await c.env.DB.prepare(`DELETE FROM print_template WHERE id = ?`).bind(id).run();
	await audit(c, "DELETE_PRINT_TEMPLATE", {
		resource_type: "print_template",
		resource_id: id,
		before: { name: before.name },
		hospital_id: before.hospital_id
	});
	return c.json({ ok: true });
});
/* 医院 logo 上传：存 file_blob/R2（与手臂照片同一套 putFile），
 * key 回写 hospital.logo_url。前端经 /api/files/<key> 鉴权下载。 */
admin.post("/hospitals/:id/logo", async (c) => {
	const id = c.req.param("id");
	const h = await c.env.DB.prepare(`SELECT id, logo_url FROM hospital WHERE id = ?`).bind(id).first();
	if (!h) return c.json({ error: "NOT_FOUND" }, 404);
	const b = await c.req.json();
	if (!b.data) return c.json({
		error: "BAD_REQUEST",
		message: "缺少图像数据"
	}, 400);
	const { bytes, contentType } = decodeImagePayload(b.data);
	if (bytes.length > 512 * 1024) return c.json({
		error: "TOO_LARGE",
		message: "logo 不能超过 512KB（建议 PNG 透明底，200×200 左右）"
	}, 400);
	const f = await putFile(c.env, "logo", id, bytes, contentType);
	if (h.logo_url) try {
		await deleteFile(c.env, h.logo_url);
	} catch {}
	await c.env.DB.prepare(`UPDATE hospital SET logo_url = ?, updated_at = datetime('now') WHERE id = ?`).bind(f.key, id).run();
	await audit(c, "UPDATE_HOSPITAL_LOGO", {
		resource_type: "hospital",
		resource_id: id,
		hospital_id: id
	});
	return c.json({ key: f.key });
});
admin.delete("/hospitals/:id/logo", async (c) => {
	const id = c.req.param("id");
	const h = await c.env.DB.prepare(`SELECT id, logo_url FROM hospital WHERE id = ?`).bind(id).first();
	if (!h) return c.json({ error: "NOT_FOUND" }, 404);
	if (h.logo_url) {
		try {
			await deleteFile(c.env, h.logo_url);
		} catch {}
		await c.env.DB.prepare(`UPDATE hospital SET logo_url = NULL, updated_at = datetime('now') WHERE id = ?`).bind(id).run();
	}
	return c.json({ ok: true });
});
admin.get("/accounts", async (c) => {
	const hid = c.req.query("hospital_id");
	if (!hid) return c.json({
		error: "BAD_REQUEST",
		message: "请先选择医院"
	}, 400);
	const r = await c.env.DB.prepare(`SELECT u.id, u.username, u.real_name, u.role, u.phone, u.is_active, u.must_change_password,
            u.last_login_at, u.locked_until, u.created_at, h.name AS hospital_name
     FROM user_account u LEFT JOIN hospital h ON h.id = u.hospital_id
     WHERE u.hospital_id = ? ORDER BY u.role, u.created_at DESC`).bind(hid).all();
	return c.json({ data: r.results ?? [] });
});
admin.post("/accounts", async (c) => {
	const b = await c.req.json();
	if (!b.hospital_id || !b.username || !b.real_name || !b.role || !b.password) return c.json({
		error: "BAD_REQUEST",
		message: "医院、用户名、姓名、角色、初始密码必填"
	}, 400);
	if (!["DOCTOR", "NURSE"].includes(b.role)) return c.json({
		error: "BAD_REQUEST",
		message: "仅可创建医生或护士账号"
	}, 400);
	if (!await c.env.DB.prepare(`SELECT id FROM hospital WHERE id = ?`).bind(b.hospital_id).first()) return c.json({
		error: "NOT_FOUND",
		message: "医院不存在"
	}, 404);
	if (await c.env.DB.prepare(`SELECT id FROM user_account WHERE username = ?`).bind(b.username).first()) return c.json({
		error: "DUPLICATE",
		message: "用户名已被占用"
	}, 409);
	const id = uuid();
	await c.env.DB.prepare(`INSERT INTO user_account (id, hospital_id, username, password_hash, real_name, role, phone, is_active, must_change_password)
     VALUES (?,?,?,?,?,?,?,1,?)`).bind(id, b.hospital_id, b.username, await hashPassword(b.password), b.real_name, b.role, b.phone ?? null, b.must_change_password === false ? 0 : 1).run();
	await audit(c, "CREATE_ACCOUNT", {
		resource_type: "user_account",
		resource_id: id,
		hospital_id: b.hospital_id,
		after: {
			username: b.username,
			role: b.role,
			real_name: b.real_name
		}
	});
	return c.json({ id });
});
admin.put("/accounts/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const before = await c.env.DB.prepare(`SELECT * FROM user_account WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	if (before.role === "PLATFORM_ADMIN" && b.role && b.role !== "PLATFORM_ADMIN") return c.json({
		error: "FORBIDDEN",
		message: "不可降级平台管理员"
	}, 403);
	await c.env.DB.prepare(`UPDATE user_account SET real_name=?, role=?, phone=?, is_active=?, updated_at=datetime('now') WHERE id=?`).bind(b.real_name ?? before.real_name, b.role ?? before.role, b.phone ?? before.phone, b.is_active === void 0 ? before.is_active : b.is_active ? 1 : 0, id).run();
	await audit(c, "UPDATE_ACCOUNT", {
		resource_type: "user_account",
		resource_id: id,
		hospital_id: before.hospital_id,
		before: {
			real_name: before.real_name,
			role: before.role,
			is_active: before.is_active
		},
		after: b
	});
	return c.json({ ok: true });
});
/** 重置密码（强制首登改密） */
admin.post("/accounts/:id/reset-password", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	if (!b.password || b.password.length < 8) return c.json({
		error: "WEAK_PASSWORD",
		message: "密码至少 8 位"
	}, 400);
	const before = await c.env.DB.prepare(`SELECT hospital_id FROM user_account WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	await c.env.DB.prepare(`UPDATE user_account SET password_hash=?, must_change_password=1, failed_attempts=0, locked_until=NULL, updated_at=datetime('now') WHERE id=?`).bind(await hashPassword(b.password), id).run();
	await c.env.DB.prepare(`UPDATE refresh_token SET revoked = 1 WHERE user_id = ?`).bind(id).run();
	await audit(c, "RESET_PASSWORD", {
		resource_type: "user_account",
		resource_id: id,
		hospital_id: before.hospital_id
	});
	return c.json({ ok: true });
});
admin.post("/accounts/:id/unlock", async (c) => {
	const id = c.req.param("id");
	await c.env.DB.prepare(`UPDATE user_account SET failed_attempts=0, locked_until=NULL WHERE id=?`).bind(id).run();
	await audit(c, "UNLOCK_ACCOUNT", {
		resource_type: "user_account",
		resource_id: id
	});
	return c.json({ ok: true });
});
admin.delete("/accounts/:id", async (c) => {
	const id = c.req.param("id");
	const before = await c.env.DB.prepare(`SELECT * FROM user_account WHERE id = ?`).bind(id).first();
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	if (before.role === "PLATFORM_ADMIN") return c.json({
		error: "FORBIDDEN",
		message: "不可删除平台管理员"
	}, 403);
	if (((await c.env.DB.prepare(`SELECT (SELECT COUNT(*) FROM spt_report WHERE doctor_id=? OR operator_id=?) AS c`).bind(id, id).first())?.c ?? 0) > 0) return c.json({
		error: "IN_USE",
		message: "该账号已关联报告单，请改为停用"
	}, 409);
	await c.env.DB.prepare(`DELETE FROM user_account WHERE id = ?`).bind(id).run();
	await audit(c, "DELETE_ACCOUNT", {
		resource_type: "user_account",
		resource_id: id,
		hospital_id: before.hospital_id,
		before: { username: before.username }
	});
	return c.json({ ok: true });
});
admin.get("/audit", async (c) => {
	const q = c.req.query();
	const page = Math.max(1, Number(q.page || 1));
	const size = Math.min(200, Number(q.size || 20));
	const conds = ["1=1"];
	const binds = [];
	if (q.hospital_id) {
		conds.push("hospital_id = ?");
		binds.push(q.hospital_id);
	}
	if (q.action) {
		conds.push("action LIKE ?");
		binds.push(`%${q.action}%`);
	}
	if (q.actor) {
		conds.push("(actor_name LIKE ? OR actor_id = ?)");
		binds.push(`%${q.actor}%`, q.actor);
	}
	if (q.from) {
		conds.push("created_at >= ?");
		binds.push(q.from);
	}
	if (q.to) {
		conds.push("created_at <= ?");
		binds.push(q.to + " 23:59:59");
	}
	const where = conds.join(" AND ");
	const total = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM audit_log WHERE ${where}`).bind(...binds).first();
	const r = await c.env.DB.prepare(`SELECT a.*, h.name AS hospital_name FROM audit_log a LEFT JOIN hospital h ON h.id = a.hospital_id
     WHERE ${where} ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?`).bind(...binds, size, (page - 1) * size).all();
	return c.json({
		data: r.results ?? [],
		total: total?.c ?? 0,
		page,
		size
	});
});
/** 平台总览统计 */
admin.get("/stats", async (c) => {
	const s = await c.env.DB.prepare(`SELECT
      (SELECT COUNT(*) FROM hospital WHERE is_active=1) AS hospitals,
      (SELECT COUNT(*) FROM user_account WHERE role='DOCTOR') AS doctors,
      (SELECT COUNT(*) FROM user_account WHERE role='NURSE') AS nurses,
      (SELECT COUNT(*) FROM patient) AS patients,
      (SELECT COUNT(*) FROM spt_report) AS reports,
      (SELECT COUNT(*) FROM spt_report WHERE status='SUBMITTED') AS submitted,
      (SELECT COUNT(*) FROM spt_template WHERE is_deleted=0) AS templates,
      (SELECT COUNT(*) FROM device_bind WHERE status='ONLINE') AS devices_online`).first();
	return c.json({ data: s });
});
//#endregion
//#region src/lib/db.ts
/**
* 租户安全的 D1 访问层。
*
* 设计要点（对应硬约束 6.2）:
*  - 所有业务表访问必须经过 tenantDb(db, tenant)，SQL 由本层拼装并**强制**注入
*    `WHERE hospital_id = ?`，调用方无法绕过。
*  - INSERT 由本层强制写入 hospital_id，调用方传入的同名字段会被覆盖。
*  - 每次 UPDATE / DELETE 都追加 hospital_id 条件，防止跨院改写。
*/
var TENANT_TABLES = /* @__PURE__ */ new Set([
	"patient",
	"spt_report",
	"spt_template",
	"device_bind",
	"user_account",
	"spt_report_row_snapshot",
	"spt_photo",
	"allergen_catalog"
]);
function tenantDb(db, tenant) {
	if (!tenant) throw new Error("tenantDb: 缺少 tenant 作用域");
	function guard(table) {
		if (!TENANT_TABLES.has(table)) throw new Error(`tenantDb: 表 ${table} 未登记为租户表`);
	}
	function build(table, opts = {}, selectOverride) {
		guard(table);
		let sql = `SELECT ${selectOverride ?? opts.select ?? "*"} FROM ${table} WHERE hospital_id = ?`;
		const binds = [tenant];
		if (opts.where) {
			sql += ` AND (${opts.where})`;
			binds.push(...opts.binds ?? []);
		}
		if (opts.orderBy) sql += ` ORDER BY ${opts.orderBy}`;
		if (opts.limit != null) sql += ` LIMIT ${Number(opts.limit)}`;
		if (opts.offset != null) sql += ` OFFSET ${Number(opts.offset)}`;
		return {
			sql,
			binds
		};
	}
	return {
		tenant,
		async all(table, opts = {}) {
			const { sql, binds } = build(table, opts);
			return (await db.prepare(sql).bind(...binds).all()).results ?? [];
		},
		async first(table, opts = {}) {
			const { sql, binds } = build(table, {
				...opts,
				limit: 1
			});
			return await db.prepare(sql).bind(...binds).first();
		},
		async count(table, opts = {}) {
			const { sql, binds } = build(table, {
				...opts,
				limit: void 0,
				offset: void 0,
				orderBy: void 0
			}, "COUNT(*) AS c");
			return (await db.prepare(sql).bind(...binds).first())?.c ?? 0;
		},
		async insert(table, data) {
			guard(table);
			const payload = {
				...data,
				hospital_id: tenant
			};
			const keys = Object.keys(payload);
			const sql = `INSERT INTO ${table} (${keys.join(",")}) VALUES (${keys.map(() => "?").join(",")})`;
			await db.prepare(sql).bind(...keys.map((k) => payload[k])).run();
		},
		async update(table, id, data) {
			guard(table);
			const payload = { ...data };
			delete payload.hospital_id;
			delete payload.id;
			const keys = Object.keys(payload);
			if (!keys.length) return 0;
			const sql = `UPDATE ${table} SET ${keys.map((k) => `${k}=?`).join(",")}, updated_at=datetime('now')
                   WHERE id = ? AND hospital_id = ?`;
			return (await db.prepare(sql).bind(...keys.map((k) => payload[k]), id, tenant).run()).meta?.changes ?? 0;
		},
		async raw(sql, ...binds) {
			if (!/hospital_id\s*=\s*\?/.test(sql)) throw new Error("tenantDb.raw: SQL 必须显式包含 hospital_id = ? 条件");
			return (await db.prepare(sql).bind(...binds).all()).results ?? [];
		},
		async rawRun(sql, ...binds) {
			if (!/hospital_id\s*=\s*\?/.test(sql)) throw new Error("tenantDb.rawRun: SQL 必须显式包含 hospital_id = ? 条件");
			return db.prepare(sql).bind(...binds).run();
		}
	};
}
/**
* 「只知道年龄、不知道生日」的哨兵生日。
* HIS 屏幕上只有「年龄 10岁」没有出生日期，而 patient.birth_date 是 NOT NULL。
* 详细取舍见 migrations/0006_report_form_fields.sql 与 src/routes/patients.ts。
*/
var AGE_ONLY_BIRTH_DATE = "0001-01-01";
/** 计算年龄（按当前日期） */
function calcAge(birthDate) {
	const b = /* @__PURE__ */ new Date(birthDate + "T00:00:00Z");
	if (isNaN(b.getTime())) return 0;
	const now = /* @__PURE__ */ new Date();
	let age = now.getUTCFullYear() - b.getUTCFullYear();
	const m = now.getUTCMonth() - b.getUTCMonth();
	if (m < 0 || m === 0 && now.getUTCDate() < b.getUTCDate()) age--;
	return Math.max(0, age);
}
/**
* 患者展示年龄的**唯一**入口。
*
* 为什么不让各处继续直接调 calcAge(birth_date)：哨兵生日 0001-01-01 交给
* calcAge 会算出 2025 岁。年龄计算原本散落在 patients / reports / exports /
* capture 四个文件共 10 余处，只要漏改一处就会在某个页面或某份导出里冒出
* 荒谬数字，而这种错误不会报错、只会安静地印在病历上。收敛到一个函数，
* 以后再改年龄规则也只有一个地方要动。
*
* @param p 至少包含 birth_date，可选 age_years
*/
function patientAge(p) {
	if (!p) return 0;
	if (p.birth_date === "0001-01-01") return Number(p.age_years ?? 0);
	return calcAge(p.birth_date ?? "");
}
//#endregion
//#region src/routes/danger.ts
/**
* 危险操作双重密码授权 —— /api/danger
*
* 为什么需要这条独立通路：
*   患者档案和报告单原本是「有关联就不让删」（patients.ts 的 IN_USE 409、
*   reports.ts 的非草稿禁删）。那道护栏防的是误删，但也让测试期的脏数据
*   清不掉。直接拆护栏等于把误删风险全额放开，所以改成「护栏默认在，
*   拿到双人授权令牌才放行」。
*
* 为什么第二道密码必须是**另一个账号**：
*   同一个人连输两次自己的密码，挡不住任何误操作——他既然决定要删，
*   输两遍只是多敲几下键盘。双重验证的全部价值在于「第二个人也同意」，
*   所以 approver 必须 ≠ 当前登录账号，这条在下面是硬校验。
*
* 为什么审批人限本院医生：
*   系统的底线是医院级隔离（tenantGuard），平台管理员被 clinicalOnly
*   明确排除在临床数据之外。若让 PLATFORM_ADMIN 来批准删病历，等于
*   给隔离模型开了个后门，且全平台只有一个 admin，两家医院都找他签字
*   既不合理也不可审计。所以审批人 = 同院、在职、DOCTOR。
*
* 令牌设计：
*   - 60 秒有效：够点完确认弹窗，不够留着复用
*   - 绑定 action + resource_id：批「删患者 A」的令牌删不了患者 B
*   - 绑定 operator + hospital：换个人、换个院都用不了
*   - 一次性：用掉即失效（jti 落库，见 consumeDangerToken）
*/
var danger = new Hono();
danger.use("*", authGuard, tenantGuard, clinicalOnly);
/** 令牌有效期（秒）。短到无法囤积，长到够走完确认弹窗。 */
var TOKEN_TTL_SEC = 60;
/** 允许被授权的操作白名单。不在表里的 action 一律拒签，避免将来
*  有人拿这套令牌去授权别的（比如批量清库）而不经过评审。 */
var DANGER_ACTIONS = ["DELETE_PATIENT", "DELETE_REPORT"];
/** 密码错误累计上限。与登录一致，防止把这里变成绕过登录锁定的爆破口。 */
var MAX_FAILED = 5;
var LOCK_MINUTES = 10;
/**
* 记一次密码失败。复用 user_account.failed_attempts —— 必须和登录共用
* 同一个计数器，否则攻击者可以在这里无限试，试中了再去登录。
*/
async function noteFailure(c, userId, hospitalId, who) {
	const attempts = ((await c.env.DB.prepare(`SELECT failed_attempts FROM user_account WHERE id = ?`).bind(userId).first())?.failed_attempts ?? 0) + 1;
	if (attempts >= MAX_FAILED) {
		await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = 0, locked_until = datetime('now', '+${LOCK_MINUTES} minutes') WHERE id = ?`).bind(userId).run();
		await audit(c, "ACCOUNT_LOCKED", {
			resource_type: "user_account",
			resource_id: userId,
			hospital_id: hospitalId,
			after: {
				reason: "DANGER_AUTH_FAILED",
				who
			}
		});
		return {
			locked: true,
			remain: 0
		};
	}
	await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = ? WHERE id = ?`).bind(attempts, userId).run();
	return {
		locked: false,
		remain: MAX_FAILED - attempts
	};
}
async function clearFailures(c, userId) {
	await c.env.DB.prepare(`UPDATE user_account SET failed_attempts = 0 WHERE id = ?`).bind(userId).run();
}
/** 账号是否处于锁定期 */
function isLocked(u) {
	if (!u?.locked_until) return false;
	return (/* @__PURE__ */ new Date(u.locked_until.replace(" ", "T") + "Z")).getTime() > Date.now();
}
/** 院管理员用户名约定：admin-<两位院内编号>。仅用于排序与标注，
*  不参与任何权限判定——权限判定一律看 role。 */
var HOSPITAL_ADMIN_RE = /^admin-\d+$/i;
danger.get("/approvers", async (c) => {
	const me = c.var.user;
	const items = ((await c.env.DB.prepare(`SELECT id, username, real_name FROM user_account
      WHERE hospital_id = ? AND role = 'DOCTOR' AND is_active = 1 AND id <> ?
      ORDER BY real_name`).bind(c.var.tenant, me.id).all()).results ?? []).map((x) => ({
		...x,
		is_hospital_admin: HOSPITAL_ADMIN_RE.test(String(x.username || ""))
	}));
	items.sort((a, b) => Number(b.is_hospital_admin) - Number(a.is_hospital_admin));
	return c.json({ items });
});
danger.post("/authorize", async (c) => {
	const body = await c.req.json();
	const action = String(body.action || "");
	const resourceId = String(body.resource_id || "");
	const selfPwd = body.self_password || "";
	const apprUser = String(body.approver_username || "").trim();
	const apprPwd = body.approver_password || "";
	if (!DANGER_ACTIONS.includes(action)) return c.json({
		error: "BAD_ACTION",
		message: "不支持的操作类型"
	}, 400);
	if (!resourceId) return c.json({
		error: "BAD_REQUEST",
		message: "缺少目标 ID"
	}, 400);
	if (!selfPwd || !apprUser || !apprPwd) return c.json({
		error: "BAD_REQUEST",
		message: "两道密码均为必填"
	}, 400);
	const me = c.var.user;
	const selfRow = await c.env.DB.prepare(`SELECT id, password_hash, failed_attempts, locked_until FROM user_account WHERE id = ?`).bind(me.id).first();
	if (!selfRow) return c.json({ error: "UNAUTHORIZED" }, 401);
	if (isLocked(selfRow)) return c.json({
		error: "LOCKED",
		message: "账号已锁定，请稍后再试"
	}, 423);
	if (!await verifyPassword(selfPwd, selfRow.password_hash)) {
		const r = await noteFailure(c, me.id, c.var.tenant, "SELF");
		await audit(c, "DANGER_AUTH_FAILED", {
			resource_type: action === "DELETE_PATIENT" ? "patient" : "spt_report",
			resource_id: resourceId,
			after: {
				stage: "SELF",
				action
			}
		});
		if (r.locked) return c.json({
			error: "LOCKED",
			message: `连续失败过多，账号锁定 ${LOCK_MINUTES} 分钟`
		}, 423);
		return c.json({
			error: "BAD_SELF_PASSWORD",
			message: `本人密码错误（剩余 ${r.remain} 次）`
		}, 401);
	}
	await clearFailures(c, me.id);
	const appr = await c.env.DB.prepare(`SELECT id, username, real_name, password_hash, failed_attempts, locked_until
       FROM user_account
      WHERE username = ? AND hospital_id = ? AND role = 'DOCTOR' AND is_active = 1`).bind(apprUser, c.var.tenant).first();
	if (!appr) {
		await audit(c, "DANGER_AUTH_FAILED", {
			resource_type: action === "DELETE_PATIENT" ? "patient" : "spt_report",
			resource_id: resourceId,
			after: {
				stage: "APPROVER_NOT_FOUND",
				action,
				tried: apprUser
			}
		});
		return c.json({
			error: "BAD_APPROVER",
			message: "审批人不存在，或不是本院在职医生"
		}, 401);
	}
	if (appr.id === me.id) return c.json({
		error: "SELF_APPROVAL",
		message: "审批人不能是本人，需由另一位本院医生复核"
	}, 400);
	if (isLocked(appr)) return c.json({
		error: "APPROVER_LOCKED",
		message: "审批人账号已锁定，请稍后再试"
	}, 423);
	if (!await verifyPassword(apprPwd, appr.password_hash)) {
		const r = await noteFailure(c, appr.id, c.var.tenant, "APPROVER");
		await audit(c, "DANGER_AUTH_FAILED", {
			resource_type: action === "DELETE_PATIENT" ? "patient" : "spt_report",
			resource_id: resourceId,
			after: {
				stage: "APPROVER",
				action,
				approver: appr.username
			}
		});
		if (r.locked) return c.json({
			error: "APPROVER_LOCKED",
			message: `审批人连续失败过多，账号锁定 ${LOCK_MINUTES} 分钟`
		}, 423);
		return c.json({
			error: "BAD_APPROVER_PASSWORD",
			message: `审批人密码错误（剩余 ${r.remain} 次）`
		}, 401);
	}
	await clearFailures(c, appr.id);
	const jti = uuid();
	const now = Math.floor(Date.now() / 1e3);
	const token = await signJwt({
		sub: me.id,
		username: me.username,
		real_name: me.real_name,
		role: me.role,
		hospital_id: c.var.tenant,
		typ: "danger",
		jti,
		exp: now + TOKEN_TTL_SEC,
		act: action,
		rid: resourceId,
		approver: appr.id,
		approver_name: appr.real_name
	}, jwtSecret(c.env));
	await audit(c, "DANGER_AUTHORIZED", {
		resource_type: action === "DELETE_PATIENT" ? "patient" : "spt_report",
		resource_id: resourceId,
		after: {
			action,
			approver_id: appr.id,
			approver_name: appr.real_name,
			approver_username: appr.username,
			jti,
			expires_in: TOKEN_TTL_SEC
		}
	});
	return c.json({
		token,
		expires_in: TOKEN_TTL_SEC,
		approver: {
			username: appr.username,
			real_name: appr.real_name
		}
	});
});
/**
* 校验并**消费**删除令牌。
*
* 一次性是靠审计表实现的：签发时写 DANGER_AUTHORIZED，消费时用
* jti 查有没有已经用过的 DANGER_CONSUMED。没有额外建表——审计本来
* 就要记这两条，复用它比多一张表少一处不一致。
*
* @returns null 表示校验不通过（调用方应拒绝执行删除）
*/
async function consumeDangerToken(c, expectAction, expectResourceId) {
	const raw = c.req.header("X-Danger-Token") || "";
	if (!raw) return null;
	const p = await verifyJwt(raw, jwtSecret(c.env));
	if (!p) return null;
	if (p.typ !== "danger") return null;
	if (p.act !== expectAction) return null;
	if (p.rid !== expectResourceId) return null;
	if (p.sub !== c.var.user.id) return null;
	if (p.hospital_id !== c.var.tenant) return null;
	if (await c.env.DB.prepare(`SELECT 1 AS x FROM audit_log WHERE action = 'DANGER_CONSUMED' AND after_json LIKE ? LIMIT 1`).bind(`%"jti":"${p.jti}"%`).first()) return null;
	await audit(c, "DANGER_CONSUMED", {
		resource_type: expectAction === "DELETE_PATIENT" ? "patient" : "spt_report",
		resource_id: expectResourceId,
		after: {
			action: expectAction,
			jti: p.jti,
			approver_id: p.approver,
			approver_name: p.approver_name
		}
	});
	return {
		approver_id: p.approver,
		approver_name: p.approver_name,
		jti: p.jti
	};
}
//#endregion
//#region src/routes/patients.ts
var patients = new Hono();
patients.use("*", authGuard, clinicalOnly, tenantGuard);
/** 列表（分页 20/页） */
patients.get("/", async (c) => {
	const db = tenantDb(c.env.DB, c.var.tenant);
	const q = c.req.query();
	const page = Math.max(1, Number(q.page || 1));
	const size = Math.min(100, Number(q.size || 20));
	const conds = [];
	const binds = [];
	if (q.kw) {
		conds.push("(name LIKE ? OR visit_card_no LIKE ? OR contact_phone LIKE ?)");
		binds.push(`%${q.kw}%`, `%${q.kw}%`, `%${q.kw}%`);
	}
	if (q.gender) {
		conds.push("gender = ?");
		binds.push(q.gender);
	}
	const where = conds.length ? conds.join(" AND ") : void 0;
	const total = await db.count("patient", {
		where,
		binds
	});
	const rows = await db.all("patient", {
		where,
		binds,
		orderBy: "created_at DESC",
		limit: size,
		offset: (page - 1) * size
	});
	for (const p of rows) {
		p.age = patientAge(p);
		p.report_count = (await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report WHERE patient_id = ? AND hospital_id = ?`).bind(p.id, c.var.tenant).first())?.c ?? 0;
		if (p.created_by) p.created_by_name = (await c.env.DB.prepare(`SELECT real_name FROM user_account WHERE id = ? AND hospital_id = ?`).bind(p.created_by, c.var.tenant).first())?.real_name ?? "—";
	}
	return c.json({
		data: rows,
		total,
		page,
		size
	});
});
/** 按就诊卡号精确查（识别流程用） */
patients.get("/by-card/:cardNo", async (c) => {
	const p = await tenantDb(c.env.DB, c.var.tenant).first("patient", {
		where: "visit_card_no = ?",
		binds: [c.req.param("cardNo")]
	});
	if (!p) return c.json({ data: null });
	p.age = patientAge(p);
	return c.json({ data: p });
});
patients.get("/:id", async (c) => {
	const p = await tenantDb(c.env.DB, c.var.tenant).first("patient", {
		where: "id = ?",
		binds: [c.req.param("id")]
	});
	if (!p) return c.json({
		error: "NOT_FOUND",
		message: "患者不存在或不属于本医院"
	}, 404);
	p.age = patientAge(p);
	return c.json({ data: p });
});
/** 该患者的历史报告单 */
patients.get("/:id/reports", async (c) => {
	const tenant = c.var.tenant;
	const rows = await c.env.DB.prepare(`SELECT r.id, r.report_date, r.status, r.created_at, r.submitted_at, r.template_name_snapshot,
            d.real_name AS doctor_name, o.real_name AS operator_name,
            (SELECT COUNT(*) FROM spt_photo WHERE report_id = r.id) AS photo_count
     FROM spt_report r
     LEFT JOIN user_account d ON d.id = r.doctor_id AND d.hospital_id = r.hospital_id
     LEFT JOIN user_account o ON o.id = r.operator_id AND o.hospital_id = r.hospital_id
     WHERE r.patient_id = ? AND r.hospital_id = ?
     ORDER BY r.report_date DESC, r.created_at DESC`).bind(c.req.param("id"), tenant).all();
	return c.json({ data: rows.results ?? [] });
});
function validatePatient(b) {
	if (!b.name?.trim()) return "姓名必填";
	if (![
		"M",
		"F",
		"UNKNOWN"
	].includes(b.gender)) return "性别不合法";
	if (!b.visit_card_no?.trim()) return "就诊卡号必填";
	const hasBirth = /^\d{4}-\d{2}-\d{2}$/.test(b.birth_date || "") && b.birth_date !== "0001-01-01";
	const ageRaw = b.age_years;
	const hasAge = ageRaw !== null && ageRaw !== void 0 && ageRaw !== "" && Number.isFinite(Number(ageRaw));
	if (!hasBirth && !hasAge) return "出生日期与年龄至少填写一项（出生日期格式 YYYY-MM-DD）";
	if (!hasBirth && b.birth_date && b.birth_date !== "0001-01-01") return "出生日期格式应为 YYYY-MM-DD";
	if (hasAge) {
		const n = Number(ageRaw);
		if (n < 0 || n > 120 || !Number.isInteger(n)) return "年龄应为 0–120 的整数";
	}
	return null;
}
/** 依据入参决定实际落库的 birth_date / age_years / age_display_cache */
function resolveAge(b) {
	const hasBirth = /^\d{4}-\d{2}-\d{2}$/.test(b.birth_date || "") && b.birth_date !== "0001-01-01";
	const ageRaw = b.age_years;
	const hasAge = ageRaw !== null && ageRaw !== void 0 && ageRaw !== "" && Number.isFinite(Number(ageRaw));
	if (hasBirth) return {
		birth_date: b.birth_date,
		age_years: hasAge ? Number(ageRaw) : calcAge(b.birth_date),
		age_display_cache: calcAge(b.birth_date)
	};
	const n = Number(ageRaw);
	return {
		birth_date: AGE_ONLY_BIRTH_DATE,
		age_years: n,
		age_display_cache: n
	};
}
patients.post("/", async (c) => {
	const b = await c.req.json();
	const err = validatePatient(b);
	if (err) return c.json({
		error: "BAD_REQUEST",
		message: err
	}, 400);
	const db = tenantDb(c.env.DB, c.var.tenant);
	if (await db.first("patient", {
		where: "visit_card_no = ?",
		binds: [b.visit_card_no.trim()]
	})) return c.json({
		error: "DUPLICATE",
		message: "本医院已存在该就诊卡号"
	}, 409);
	const id = uuid();
	const ag = resolveAge(b);
	await db.insert("patient", {
		id,
		name: b.name.trim(),
		gender: b.gender,
		birth_date: ag.birth_date,
		age_years: ag.age_years,
		visit_card_no: b.visit_card_no.trim(),
		contact_person: b.contact_person ?? null,
		contact_phone: b.contact_phone ?? null,
		qr_code_url: b.qr_code_url ?? null,
		card_photo_url: b.card_photo_url ?? null,
		age_display_cache: ag.age_display_cache,
		created_by: c.var.user.id
	});
	await audit(c, "CREATE_PATIENT", {
		resource_type: "patient",
		resource_id: id,
		after: b
	});
	return c.json({ id });
});
patients.put("/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("patient", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "患者不存在或不属于本医院"
	}, 404);
	const merged = {
		...before,
		...b
	};
	const err = validatePatient(merged);
	if (err) return c.json({
		error: "BAD_REQUEST",
		message: err
	}, 400);
	if (merged.visit_card_no !== before.visit_card_no) {
		if (await db.first("patient", {
			where: "visit_card_no = ? AND id <> ?",
			binds: [merged.visit_card_no, id]
		})) return c.json({
			error: "DUPLICATE",
			message: "本医院已存在该就诊卡号"
		}, 409);
	}
	const ag2 = resolveAge(merged);
	await db.update("patient", id, {
		name: merged.name.trim(),
		gender: merged.gender,
		birth_date: ag2.birth_date,
		age_years: ag2.age_years,
		visit_card_no: merged.visit_card_no.trim(),
		contact_person: merged.contact_person ?? null,
		contact_phone: merged.contact_phone ?? null,
		card_photo_url: merged.card_photo_url ?? null,
		age_display_cache: ag2.age_display_cache
	});
	await audit(c, "UPDATE_PATIENT", {
		resource_type: "patient",
		resource_id: id,
		before,
		after: b
	});
	return c.json({ ok: true });
});
patients.delete("/:id", async (c) => {
	const id = c.req.param("id");
	const tenant = c.var.tenant;
	const before = await tenantDb(c.env.DB, tenant).first("patient", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	const cnt = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report WHERE patient_id = ? AND hospital_id = ?`).bind(id, tenant).first();
	const reportCount = Number(cnt?.c ?? 0);
	let grant = null;
	if (reportCount > 0) {
		grant = await consumeDangerToken(c, "DELETE_PATIENT", id);
		if (!grant) return c.json({
			error: "IN_USE",
			message: `该患者已关联 ${reportCount} 份报告单。级联删除需双人密码授权。`,
			require_danger_auth: true,
			report_count: reportCount
		}, 409);
	}
	const stmts = [];
	if (reportCount > 0) {
		const ids = ((await c.env.DB.prepare(`SELECT id FROM spt_report WHERE patient_id = ? AND hospital_id = ?`).bind(id, tenant).all()).results ?? []).map((r) => r.id);
		for (const rid of ids) {
			const photos = await c.env.DB.prepare(`SELECT storage_key FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(rid, tenant).all();
			for (const p of photos.results ?? []) if (p.storage_key) await deleteFile(c.env, p.storage_key);
		}
		for (const rid of ids) stmts.push(c.env.DB.prepare(`DELETE FROM wheal_measurement WHERE session_id IN (SELECT id FROM wheal_session WHERE report_id = ?)`).bind(rid), c.env.DB.prepare(`DELETE FROM wheal_session WHERE report_id = ?`).bind(rid), c.env.DB.prepare(`DELETE FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(rid, tenant), c.env.DB.prepare(`DELETE FROM spt_report_row_snapshot WHERE report_id = ? AND hospital_id = ?`).bind(rid, tenant));
		stmts.push(c.env.DB.prepare(`DELETE FROM spt_report WHERE patient_id = ? AND hospital_id = ?`).bind(id, tenant));
	}
	stmts.push(c.env.DB.prepare(`DELETE FROM patient WHERE id = ? AND hospital_id = ?`).bind(id, tenant));
	await c.env.DB.batch(stmts);
	await audit(c, "DELETE_PATIENT", {
		resource_type: "patient",
		resource_id: id,
		before: {
			...before,
			cascade_reports: reportCount
		},
		after: grant ? {
			authorized_by: grant.approver_name,
			approver_id: grant.approver_id,
			jti: grant.jti,
			cascade: true
		} : { cascade: false }
	});
	return c.json({
		ok: true,
		deleted_reports: reportCount
	});
});
/** 批量导入（前端解析 Excel/CSV → JSON 数组） */
patients.post("/bulk-import", async (c) => {
	const { rows } = await c.req.json();
	if (!Array.isArray(rows) || !rows.length) return c.json({
		error: "BAD_REQUEST",
		message: "导入数据为空"
	}, 400);
	const db = tenantDb(c.env.DB, c.var.tenant);
	const result = {
		success: 0,
		failed: 0,
		errors: []
	};
	for (let i = 0; i < rows.length; i++) {
		const r = rows[i];
		try {
			const genderRaw = String(r.gender ?? r["性别"] ?? "").trim();
			const gender = /^(m|男|male)$/i.test(genderRaw) ? "M" : /^(f|女|female)$/i.test(genderRaw) ? "F" : "UNKNOWN";
			const rec = {
				name: String(r.name ?? r["姓名"] ?? "").trim(),
				gender,
				birth_date: String(r.birth_date ?? r["出生日期"] ?? "").trim().slice(0, 10),
				visit_card_no: String(r.visit_card_no ?? r["就诊卡号"] ?? "").trim(),
				contact_person: String(r.contact_person ?? r["联系人"] ?? "").trim() || null,
				contact_phone: String(r.contact_phone ?? r["联系电话"] ?? "").trim() || null
			};
			const err = validatePatient(rec);
			if (err) throw new Error(err);
			if (await db.first("patient", {
				where: "visit_card_no = ?",
				binds: [rec.visit_card_no]
			})) throw new Error("就诊卡号已存在");
			const agb = resolveAge(rec);
			await db.insert("patient", {
				id: uuid(),
				...rec,
				birth_date: agb.birth_date,
				age_years: agb.age_years,
				age_display_cache: agb.age_display_cache,
				created_by: c.var.user.id
			});
			result.success++;
		} catch (e) {
			result.failed++;
			result.errors.push({
				row: i + 1,
				name: r.name ?? r["姓名"] ?? "",
				message: e.message
			});
		}
	}
	await audit(c, "BULK_IMPORT_PATIENT", {
		resource_type: "patient",
		after: result
	});
	return c.json(result);
});
//#endregion
//#region src/routes/templates.ts
var templates = new Hono();
templates.use("*", authGuard, clinicalOnly, tenantGuard);
/**
* 规范化模版行：
* - 项目数**不做硬约束**，可多于 20 项（上限 MAX_POS_COUNT=100 仅防御异常入参）
* - 少于 20 项时仍补齐到 20，保持附件 2 纸质版式（未覆盖位置留空）
* - 多于 20 项时按实际最大位置号扩展
*/
function normalizeRows(input) {
	const map = /* @__PURE__ */ new Map();
	if (Array.isArray(input)) for (const r of input) {
		const pos = Number(r.position_no);
		if (isNormalPos(pos)) map.set(pos, String(r.allergen_name ?? "").trim());
	}
	const count = resolvePosCount(input);
	const out = [];
	for (let i = 1; i <= count; i++) out.push({
		position_no: i,
		allergen_name: map.get(i) ?? ""
	});
	return out;
}
/** 列表：**硬约束 —— 只返回 is_deleted = 0** */
templates.get("/", async (c) => {
	const db = tenantDb(c.env.DB, c.var.tenant);
	const conds = ["is_deleted = 0"];
	const binds = [];
	const kw = c.req.query("kw");
	if (kw) {
		conds.push("(name LIKE ? OR description LIKE ?)");
		binds.push(`%${kw}%`, `%${kw}%`);
	}
	const rows = await db.all("spt_template", {
		where: conds.join(" AND "),
		binds,
		orderBy: "created_at DESC"
	});
	for (const t of rows) {
		t.rows = normalizeRows(JSON.parse(t.rows_json || "[]"));
		t.allergen_count = t.rows.filter((r) => r.allergen_name).length;
		t.created_by_name = (t.created_by ? await c.env.DB.prepare(`SELECT real_name FROM user_account WHERE id = ? AND hospital_id = ?`).bind(t.created_by, c.var.tenant).first() : null)?.real_name ?? "—";
		t.usage_count = (await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report WHERE template_id = ? AND hospital_id = ?`).bind(t.id, c.var.tenant).first())?.c ?? 0;
		delete t.rows_json;
	}
	return c.json({ data: rows });
});
/** 单个模版（含已删除，供历史报告单预览显示"模版已删除"角标） */
templates.get("/:id", async (c) => {
	const t = await tenantDb(c.env.DB, c.var.tenant).first("spt_template", {
		where: "id = ?",
		binds: [c.req.param("id")]
	});
	if (!t) return c.json({
		error: "NOT_FOUND",
		message: "模版不存在或不属于本医院"
	}, 404);
	t.rows = normalizeRows(JSON.parse(t.rows_json || "[]"));
	delete t.rows_json;
	return c.json({ data: t });
});
/** 删除影响预估 */
templates.get("/:id/usage", async (c) => {
	const cnt = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report WHERE template_id = ? AND hospital_id = ?`).bind(c.req.param("id"), c.var.tenant).first();
	return c.json({ usage_count: cnt?.c ?? 0 });
});
templates.post("/", async (c) => {
	const b = await c.req.json();
	if (!b.name?.trim()) return c.json({
		error: "BAD_REQUEST",
		message: "模版名称必填"
	}, 400);
	const db = tenantDb(c.env.DB, c.var.tenant);
	const id = uuid();
	await db.insert("spt_template", {
		id,
		name: b.name.trim(),
		description: b.description ?? null,
		rows_json: JSON.stringify(normalizeRows(b.rows)),
		control_positive_allergen: b.control_positive_allergen ?? "组胺",
		control_negative_allergen: b.control_negative_allergen ?? "生理盐水",
		is_deleted: 0,
		created_by: c.var.user.id
	});
	await audit(c, "CREATE_TEMPLATE", {
		resource_type: "spt_template",
		resource_id: id,
		after: { name: b.name }
	});
	return c.json({ id });
});
templates.put("/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("spt_template", {
		where: "id = ? AND is_deleted = 0",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "模版不存在或已删除"
	}, 404);
	await db.update("spt_template", id, {
		name: (b.name ?? before.name).trim(),
		description: b.description ?? before.description,
		rows_json: JSON.stringify(normalizeRows(b.rows ?? JSON.parse(before.rows_json))),
		control_positive_allergen: b.control_positive_allergen ?? before.control_positive_allergen,
		control_negative_allergen: b.control_negative_allergen ?? before.control_negative_allergen
	});
	await audit(c, "UPDATE_TEMPLATE", {
		resource_type: "spt_template",
		resource_id: id,
		before,
		after: b
	});
	return c.json({ ok: true });
});
/** 复制 */
templates.post("/:id/duplicate", async (c) => {
	const db = tenantDb(c.env.DB, c.var.tenant);
	const src = await db.first("spt_template", {
		where: "id = ? AND is_deleted = 0",
		binds: [c.req.param("id")]
	});
	if (!src) return c.json({ error: "NOT_FOUND" }, 404);
	const id = uuid();
	await db.insert("spt_template", {
		id,
		name: `${src.name} - 副本`,
		description: src.description,
		rows_json: src.rows_json,
		control_positive_allergen: src.control_positive_allergen,
		control_negative_allergen: src.control_negative_allergen,
		is_deleted: 0,
		created_by: c.var.user.id
	});
	await audit(c, "DUPLICATE_TEMPLATE", {
		resource_type: "spt_template",
		resource_id: id
	});
	return c.json({ id });
});
/**
* 删除 —— **软删除，绝不物理删除**（硬约束 6.3.1）
* template_id 保留在 spt_report 中，历史报告单数据完全不受影响。
*/
templates.delete("/:id", async (c) => {
	const id = c.req.param("id");
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("spt_template", {
		where: "id = ? AND is_deleted = 0",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "模版不存在或已删除"
	}, 404);
	await db.rawRun(`UPDATE spt_template SET is_deleted = 1, deleted_at = datetime('now'), updated_at = datetime('now')
     WHERE id = ? AND hospital_id = ?`, id, c.var.tenant);
	const cnt = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report WHERE template_id = ? AND hospital_id = ?`).bind(id, c.var.tenant).first();
	await audit(c, "SOFT_DELETE_TEMPLATE", {
		resource_type: "spt_template",
		resource_id: id,
		before: { name: before.name },
		after: {
			is_deleted: 1,
			affected_reports: cnt?.c ?? 0
		}
	});
	return c.json({
		ok: true,
		soft_deleted: true,
		affected_reports: cnt?.c ?? 0,
		message: `模版已删除（软删除）。${cnt?.c ?? 0} 份历史报告单的数据不受影响，仍可完整读取。`
	});
});
/** 恢复（软删除可逆） */
templates.post("/:id/restore", async (c) => {
	const id = c.req.param("id");
	await tenantDb(c.env.DB, c.var.tenant).rawRun(`UPDATE spt_template SET is_deleted = 0, deleted_at = NULL, updated_at = datetime('now')
     WHERE id = ? AND hospital_id = ?`, id, c.var.tenant);
	await audit(c, "RESTORE_TEMPLATE", {
		resource_type: "spt_template",
		resource_id: id
	});
	return c.json({ ok: true });
});
/** 回收站 */
templates.get("/trash/list", async (c) => {
	const rows = await tenantDb(c.env.DB, c.var.tenant).all("spt_template", {
		where: "is_deleted = 1",
		orderBy: "deleted_at DESC"
	});
	for (const t of rows) {
		t.rows = normalizeRows(JSON.parse(t.rows_json || "[]"));
		delete t.rows_json;
	}
	return c.json({ data: rows });
});
//#endregion
//#region src/routes/reports.ts
var reports = new Hono();
reports.use("*", authGuard, clinicalOnly, tenantGuard);
/**
* 构造完整的 N+2 行（1..N + 阳性对照 101 + 阴性对照 102）。
* N 默认 20（严格复刻附件 2 布局：左栏 1-10 + 阳性对照，右栏 11-20 + 阴性对照）；
* 当实际项目数多于 20 时按实际数量扩展，左右分栏各占一半。
*/
function buildRows(input, controls) {
	const map = /* @__PURE__ */ new Map();
	if (Array.isArray(input)) for (const r of input) map.set(Number(r.position_no), r);
	const count = resolvePosCount(input);
	const out = [];
	for (let i = 1; i <= count; i++) {
		const r = map.get(i) ?? {};
		out.push({
			position_no: i,
			allergen_name: String(r.allergen_name ?? "").trim(),
			positive_area: r.positive_area ?? null,
			negative_area: r.negative_area ?? null,
			control_type: "NORMAL"
		});
	}
	const p = map.get(101) ?? {};
	out.push({
		position_no: 101,
		allergen_name: String(p.allergen_name ?? controls.pos ?? "组胺").trim(),
		positive_area: p.positive_area ?? null,
		negative_area: p.negative_area ?? null,
		control_type: "POSITIVE_CTRL"
	});
	const n = map.get(102) ?? {};
	out.push({
		position_no: 102,
		allergen_name: String(n.allergen_name ?? controls.neg ?? "生理盐水").trim(),
		positive_area: n.positive_area ?? null,
		negative_area: n.negative_area ?? null,
		control_type: "NEGATIVE_CTRL"
	});
	return out;
}
/**
* 需要跨"删后重插"保留的自动测量列。
*
* ⚠️ 本函数是 DELETE + INSERT 重建行快照。若只插入原有 8 列，
* /api/wheal/sessions/:id/apply 写入的测量与分级数据会在**医生下一次保存报告时
* 被静默清空**：positive_area（"++ / 6.5mm"）因前端回传而保留，报告看起来完全正常，
* 但 d_mean_mm / grade_confirmed / measure_source / mask_key 全部丢失 ——
* 可追溯性被销毁且没有任何可见症状。故这里必须先读出、再原样带回。
*/
var MEASURE_COLS = [
	"d_mean_mm",
	"d_max_mm",
	"d_perp_mm",
	"area_mm2",
	"solidity",
	"grade_suggested",
	"grade_confirmed",
	"grade_ratio",
	"measure_source",
	"segment_method",
	"measure_confidence",
	"manual_mm",
	"mask_key"
];
/**
* 手臂侧别规范化。
*
* 只接受 LEFT / RIGHT 两个明确值，其余一切（undefined、空串、拼错、
* 前端传来的 'left arm'、旧客户端根本不传）统一落到 UNKNOWN。
*
* 为什么不做「猜测式兜底」（比如没传就当 LEFT，或按上传顺序左右交替）：
* 侧别是判读依据——位点编号与手臂的对应关系错了，等于把 A 过敏原的结果
* 记到 B 名下。猜错的值和正确的值在界面上长得一模一样，没人会去核对；
* 而 UNKNOWN 会在界面上单独分组并显式提示，逼人来补标。
* 宁可显式承认「不知道」，也不要写一个看起来可信的假事实。
*/
function normSide(v) {
	const s = String(v ?? "").trim().toUpperCase();
	return s === "LEFT" || s === "RIGHT" ? s : "UNKNOWN";
}
/** 写入行快照（硬约束 6.3.2：逐行冗余复制过敏原名称） */
async function writeRowSnapshots(c, reportId, rows) {
	const tenant = c.var.tenant;
	const prev = await c.env.DB.prepare(`SELECT position_no, ${MEASURE_COLS.join(", ")}
       FROM spt_report_row_snapshot WHERE report_id = ? AND hospital_id = ?`).bind(reportId, tenant).all();
	const keep = /* @__PURE__ */ new Map();
	for (const r of prev.results ?? []) keep.set(Number(r.position_no), r);
	await c.env.DB.prepare(`DELETE FROM spt_report_row_snapshot WHERE report_id = ? AND hospital_id = ?`).bind(reportId, tenant).run();
	const cols = MEASURE_COLS.join(", ");
	const marks = MEASURE_COLS.map(() => "?").join(",");
	const stmts = rows.map((r) => {
		const k = keep.get(Number(r.position_no)) ?? {};
		return c.env.DB.prepare(`INSERT INTO spt_report_row_snapshot
        (id, report_id, hospital_id, position_no, allergen_name, positive_area, negative_area, control_type,
         ${cols})
       VALUES (?,?,?,?,?,?,?,?,${marks})`).bind(uuid(), reportId, tenant, r.position_no, r.allergen_name, r.positive_area, r.negative_area, r.control_type, ...MEASURE_COLS.map((col) => k[col] ?? null));
	});
	await c.env.DB.batch(stmts);
}
reports.get("/", async (c) => {
	const tenant = c.var.tenant;
	const q = c.req.query();
	const page = Math.max(1, Number(q.page || 1));
	const size = Math.min(100, Number(q.size || 20));
	const conds = ["r.hospital_id = ?"];
	const binds = [tenant];
	if (q.status) {
		conds.push("r.status = ?");
		binds.push(q.status);
	}
	if (q.patient_id) {
		conds.push("r.patient_id = ?");
		binds.push(q.patient_id);
	}
	if (q.doctor_id) {
		conds.push("r.doctor_id = ?");
		binds.push(q.doctor_id);
	}
	if (q.operator_id) {
		conds.push("r.operator_id = ?");
		binds.push(q.operator_id);
	}
	if (q.from) {
		conds.push("r.report_date >= ?");
		binds.push(q.from);
	}
	if (q.to) {
		conds.push("r.report_date <= ?");
		binds.push(q.to);
	}
	if (q.kw) {
		conds.push("(p.name LIKE ? OR p.visit_card_no LIKE ?)");
		binds.push(`%${q.kw}%`, `%${q.kw}%`);
	}
	const where = conds.join(" AND ");
	const total = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_report r
     JOIN patient p ON p.id = r.patient_id AND p.hospital_id = r.hospital_id
     WHERE ${where}`).bind(...binds).first();
	const data = ((await c.env.DB.prepare(`SELECT r.id, r.report_date, r.status, r.created_at, r.submitted_at, r.template_id, r.template_name_snapshot,
            p.name AS patient_name, p.gender, p.birth_date, p.age_years, p.visit_card_no,
            d.real_name AS doctor_name, o.real_name AS operator_name,
            (SELECT COUNT(*) FROM spt_photo WHERE report_id = r.id) AS photo_count,
            (SELECT is_deleted FROM spt_template WHERE id = r.template_id) AS template_deleted
     FROM spt_report r
     JOIN patient p ON p.id = r.patient_id AND p.hospital_id = r.hospital_id
     LEFT JOIN user_account d ON d.id = r.doctor_id AND d.hospital_id = r.hospital_id
     LEFT JOIN user_account o ON o.id = r.operator_id AND o.hospital_id = r.hospital_id
     WHERE ${where}
     ORDER BY r.report_date DESC, r.created_at DESC LIMIT ? OFFSET ?`).bind(...binds, size, (page - 1) * size).all()).results ?? []).map((r) => ({
		...r,
		age: patientAge(r)
	}));
	return c.json({
		data,
		total: total?.c ?? 0,
		page,
		size
	});
});
reports.get("/:id", async (c) => {
	const tenant = c.var.tenant;
	const id = c.req.param("id");
	const r = await c.env.DB.prepare(`SELECT * FROM spt_report WHERE id = ? AND hospital_id = ?`).bind(id, tenant).first();
	if (!r) return c.json({
		error: "NOT_FOUND",
		message: "报告单不存在或不属于本医院"
	}, 404);
	const patient = await c.env.DB.prepare(`SELECT * FROM patient WHERE id = ? AND hospital_id = ?`).bind(r.patient_id, tenant).first();
	if (!assertSameTenant(tenant, r, patient)) return c.json({
		error: "TENANT_MISMATCH",
		message: "数据归属校验失败"
	}, 403);
	const rows = await c.env.DB.prepare(`SELECT position_no, allergen_name, positive_area, negative_area, control_type,
            ${MEASURE_COLS.join(", ")}
     FROM spt_report_row_snapshot WHERE report_id = ? AND hospital_id = ? ORDER BY position_no`).bind(id, tenant).all();
	const photos = await c.env.DB.prepare(`SELECT id, photo_url, storage_key, taken_at, captured_by_device,
            COALESCE(arm_side, 'UNKNOWN') AS arm_side
     FROM spt_photo
     WHERE report_id = ? AND hospital_id = ?
     ORDER BY CASE COALESCE(arm_side, 'UNKNOWN')
                WHEN 'LEFT' THEN 0 WHEN 'RIGHT' THEN 1 ELSE 2 END, taken_at`).bind(id, tenant).all();
	const doctor = await c.env.DB.prepare(`SELECT id, real_name FROM user_account WHERE id = ? AND hospital_id = ?`).bind(r.doctor_id, tenant).first();
	const operator = await c.env.DB.prepare(`SELECT id, real_name FROM user_account WHERE id = ? AND hospital_id = ?`).bind(r.operator_id, tenant).first();
	const hospital = await c.env.DB.prepare(`SELECT name, code, address, phone FROM hospital WHERE id = ?`).bind(tenant).first();
	let templateStatus = null;
	if (r.template_id) {
		const t = await c.env.DB.prepare(`SELECT id, name, is_deleted, deleted_at FROM spt_template WHERE id = ? AND hospital_id = ?`).bind(r.template_id, tenant).first();
		templateStatus = t ? {
			id: t.id,
			name: t.name,
			is_deleted: !!t.is_deleted,
			deleted_at: t.deleted_at
		} : {
			id: r.template_id,
			name: r.template_name_snapshot,
			is_deleted: true,
			deleted_at: null,
			missing: true
		};
	}
	return c.json({ data: {
		...r,
		patient: patient ? {
			...patient,
			age: patientAge(patient)
		} : null,
		rows: rows.results ?? [],
		photos: photos.results ?? [],
		doctor,
		operator,
		hospital,
		template_status: templateStatus
	} });
});
async function resolveDoctorId(c, wanted) {
	const tenant = c.var.tenant;
	const user = c.var.user;
	if (wanted) {
		const d = await c.env.DB.prepare(`SELECT id FROM user_account WHERE id = ? AND hospital_id = ? AND role = 'DOCTOR' AND is_active = 1`).bind(wanted, tenant).first();
		if (d) return d.id;
	}
	if (user.role === "DOCTOR") return user.id;
	return "";
}
/** 报告单头部/页脚的自由文本字段：统一裁剪与长度上限 */
var FORM_TEXT_FIELDS = [
	"department",
	"applying_doctor",
	"serial_no",
	"clinical_diagnosis",
	"medical_record_no",
	"patient_age_snapshot",
	"tester_name"
];
/** 单字段上限：纸质单一格就那么宽，超长值印出来会串行，且多半是误粘贴 */
var FORM_TEXT_MAX = 120;
function trimField(v) {
	if (v === null || v === void 0) return null;
	const s = String(v).trim();
	if (!s) return null;
	return s.slice(0, FORM_TEXT_MAX);
}
/** YYYY-MM-DD */
function normDay(v) {
	const s = String(v ?? "").trim();
	return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : null;
}
/** YYYY-MM-DD HH:MM（容忍 ISO 的 T 与秒） */
function normMinute(v) {
	const s = String(v ?? "").trim().replace("T", " ");
	const m = /^(\d{4}-\d{2}-\d{2})[ ](\d{2}:\d{2})/.exec(s);
	return m ? `${m[1]} ${m[2]}` : null;
}
/**
* 执行时间 = 报告时间前推 20 分钟（点刺后观察 20 分钟判读）。
*
* 为什么在服务端也算一遍，而不是全信前端传来的 executed_at：
* 前端会把它填进输入框、用户可以手改，那是**允许的**（补录历史单时
* 两个时间未必刚好差 20 分钟）。但如果前端只给了报告时间没给执行时间
* （旧版页面、脚本调用、导入），服务端必须能自己补出来，
* 否则纸质单上会印一个空白的执行时间。
*/
function minus20(reported) {
	const t = /* @__PURE__ */ new Date(reported.replace(" ", "T") + ":00Z");
	if (isNaN(t.getTime())) return reported;
	return (/* @__PURE__ */ new Date(t.getTime() - 12e5)).toISOString().slice(0, 16).replace("T", " ");
}
/**
* 从请求体提取报告单表单字段。
* @param b       请求体
* @param before  更新时的原记录；创建时传 null。只有请求体里**显式出现**的键才覆盖，
*                否则保留原值 —— 否则前端一次只改症状的 PUT 会把整个头部清空。
*/
function pickFormFields(b, before) {
	const out = {};
	const has = (k) => Object.prototype.hasOwnProperty.call(b, k);
	const keep = (k) => before ? before[k] ?? null : null;
	for (const k of FORM_TEXT_FIELDS) out[k] = has(k) ? trimField(b[k]) : keep(k);
	out.applied_at = has("applied_at") ? normDay(b.applied_at) : keep("applied_at");
	const reported = has("reported_at") ? normMinute(b.reported_at) : keep("reported_at");
	out.reported_at = reported;
	if (has("executed_at")) out.executed_at = normMinute(b.executed_at);
	else if (before?.executed_at) out.executed_at = before.executed_at;
	else out.executed_at = reported ? minus20(String(reported)) : null;
	return out;
}
/**
* 审核者：只认**当前登录账号**，不接受前端传入的姓名。
* 审核是责任归属，前端可改的字段等于可伪造的签名。所以 id 取登录态，
* name 从账号表当场读取并快照（账号日后改名/停用，历史单仍要能读）。
*/
async function resolveReviewer(c, b, before) {
	if (!(b.reviewed === true || b.reviewer === "self" || b.status === "SUBMITTED")) return {
		reviewer_id: before?.reviewer_id ?? null,
		reviewer_name: before?.reviewer_name ?? null
	};
	const u = await c.env.DB.prepare(`SELECT id, real_name, username FROM user_account WHERE id = ? AND hospital_id = ?`).bind(c.var.user.id, c.var.tenant).first();
	return {
		reviewer_id: c.var.user.id,
		reviewer_name: u?.real_name || u?.username || null
	};
}
reports.post("/", async (c) => {
	const b = await c.req.json();
	const tenant = c.var.tenant;
	const user = c.var.user;
	const db = tenantDb(c.env.DB, tenant);
	if (!b.patient_id) return c.json({
		error: "BAD_REQUEST",
		message: "请先选择或创建患者"
	}, 400);
	const patient = await db.first("patient", {
		where: "id = ?",
		binds: [b.patient_id]
	});
	if (!patient) return c.json({
		error: "NOT_FOUND",
		message: "患者不存在或不属于本医院"
	}, 404);
	const status = b.status === "SUBMITTED" ? "SUBMITTED" : "DRAFT";
	if (status === "SUBMITTED" && user.role === "NURSE") return c.json({
		error: "FORBIDDEN",
		message: "护士仅可保存草稿，提交需由医生完成"
	}, 403);
	const doctorId = await resolveDoctorId(c, b.doctor_id);
	if (status === "SUBMITTED" && !doctorId) return c.json({
		error: "BAD_REQUEST",
		message: "提交前必须指定开单医生"
	}, 400);
	let templateName = null;
	let controls = {
		pos: b.control_positive_allergen,
		neg: b.control_negative_allergen
	};
	if (b.template_id) {
		const t = await db.first("spt_template", {
			where: "id = ?",
			binds: [b.template_id]
		});
		if (!t) return c.json({
			error: "NOT_FOUND",
			message: "模版不存在或不属于本医院"
		}, 404);
		templateName = t.name;
		controls = {
			pos: controls.pos ?? t.control_positive_allergen,
			neg: controls.neg ?? t.control_negative_allergen
		};
	}
	const rows = buildRows(b.rows, controls);
	const id = uuid();
	const form = pickFormFields(b, null);
	const reviewer = await resolveReviewer(c, b, null);
	if (!form.medical_record_no) form.medical_record_no = patient.visit_card_no ?? null;
	if (!form.patient_age_snapshot) form.patient_age_snapshot = `${patientAge(patient)}岁`;
	await db.insert("spt_report", {
		id,
		patient_id: b.patient_id,
		doctor_id: doctorId || user.id,
		operator_id: user.id,
		status,
		report_date: b.report_date || (/* @__PURE__ */ new Date()).toISOString().slice(0, 10),
		symptoms: b.symptoms ?? null,
		notes: b.notes ?? null,
		template_id: b.template_id ?? null,
		template_name_snapshot: templateName,
		...form,
		...reviewer,
		report_data_json: JSON.stringify({
			rows,
			patient_snapshot: {
				name: patient.name,
				gender: patient.gender,
				birth_date: patient.birth_date,
				age: patientAge(patient),
				visit_card_no: patient.visit_card_no,
				contact_person: patient.contact_person,
				contact_phone: patient.contact_phone
			},
			template_snapshot: templateName ? {
				id: b.template_id,
				name: templateName,
				...controls
			} : null,
			symptoms: b.symptoms ?? null,
			notes: b.notes ?? null
		}),
		submitted_at: status === "SUBMITTED" ? (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace("T", " ") : null
	});
	await writeRowSnapshots(c, id, rows);
	if (Array.isArray(b.photos)) for (const ph of b.photos) {
		if (!ph?.data) continue;
		const { bytes, contentType } = decodeImagePayload(ph.data);
		const f = await putFile(c.env, "arm", tenant, bytes, contentType);
		await c.env.DB.prepare(`INSERT INTO spt_photo (id, report_id, hospital_id, photo_url, storage_key, captured_by_device, file_hash, arm_side)
         VALUES (?,?,?,?,?,?,?,?)`).bind(uuid(), id, tenant, `/api/files/${f.key}`, f.key, ph.device ?? "unknown", f.hash, normSide(ph.arm_side)).run();
	}
	await audit(c, status === "SUBMITTED" ? "SUBMIT_REPORT" : "CREATE_REPORT_DRAFT", {
		resource_type: "spt_report",
		resource_id: id,
		after: {
			patient_id: b.patient_id,
			status,
			template_id: b.template_id ?? null
		}
	});
	return c.json({
		id,
		status
	});
});
reports.put("/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const tenant = c.var.tenant;
	const user = c.var.user;
	const db = tenantDb(c.env.DB, tenant);
	const before = await db.first("spt_report", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "报告单不存在或不属于本医院"
	}, 404);
	if (before.status === "ARCHIVED") return c.json({
		error: "LOCKED",
		message: "已归档报告单不可修改"
	}, 409);
	if (before.status === "SUBMITTED" && user.role === "NURSE") return c.json({
		error: "FORBIDDEN",
		message: "护士不可修改已提交的报告单"
	}, 403);
	const status = b.status ?? before.status;
	if (status === "SUBMITTED" && user.role === "NURSE") return c.json({
		error: "FORBIDDEN",
		message: "护士仅可保存草稿，提交需由医生完成"
	}, 403);
	if (status === "SUBMITTED") {
		const ph = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(id, tenant).first();
		const incoming = Array.isArray(b.photos) ? b.photos.filter((x) => x?.data).length : 0;
		if ((ph?.c ?? 0) + incoming < 1) return c.json({
			error: "VALIDATION",
			message: "提交前至少需要 1 张手臂实验区照片"
		}, 400);
	}
	let templateName = before.template_name_snapshot;
	let controls = {
		pos: b.control_positive_allergen,
		neg: b.control_negative_allergen
	};
	if (b.template_id && b.template_id !== before.template_id) {
		const t = await db.first("spt_template", {
			where: "id = ?",
			binds: [b.template_id]
		});
		if (!t) return c.json({
			error: "NOT_FOUND",
			message: "模版不存在或不属于本医院"
		}, 404);
		templateName = t.name;
		controls = {
			pos: controls.pos ?? t.control_positive_allergen,
			neg: controls.neg ?? t.control_negative_allergen
		};
	}
	const oldData = JSON.parse(before.report_data_json || "{}");
	const rows = buildRows(b.rows ?? oldData.rows, controls);
	const doctorId = b.doctor_id ? await resolveDoctorId(c, b.doctor_id) : before.doctor_id;
	const patient = await db.first("patient", {
		where: "id = ?",
		binds: [b.patient_id ?? before.patient_id]
	});
	if (!patient) return c.json({
		error: "NOT_FOUND",
		message: "患者不存在"
	}, 404);
	const form2 = pickFormFields(b, before);
	const reviewer2 = await resolveReviewer(c, b, before);
	await db.update("spt_report", id, {
		patient_id: patient.id,
		doctor_id: doctorId || before.doctor_id,
		status,
		report_date: b.report_date ?? before.report_date,
		symptoms: b.symptoms ?? before.symptoms,
		notes: b.notes ?? before.notes,
		template_id: b.template_id ?? before.template_id,
		template_name_snapshot: templateName,
		...form2,
		...reviewer2,
		report_data_json: JSON.stringify({
			...oldData,
			rows,
			patient_snapshot: {
				name: patient.name,
				gender: patient.gender,
				birth_date: patient.birth_date,
				age: patientAge(patient),
				visit_card_no: patient.visit_card_no,
				contact_person: patient.contact_person,
				contact_phone: patient.contact_phone
			},
			symptoms: b.symptoms ?? before.symptoms,
			notes: b.notes ?? before.notes
		}),
		submitted_at: status === "SUBMITTED" && !before.submitted_at ? (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace("T", " ") : before.submitted_at
	});
	await writeRowSnapshots(c, id, rows);
	if (Array.isArray(b.photos)) for (const ph of b.photos) {
		if (!ph?.data) continue;
		const { bytes, contentType } = decodeImagePayload(ph.data);
		const f = await putFile(c.env, "arm", tenant, bytes, contentType);
		if (await c.env.DB.prepare(`SELECT id FROM spt_photo WHERE report_id = ? AND hospital_id = ? AND file_hash = ?`).bind(id, tenant, f.hash).first()) continue;
		await c.env.DB.prepare(`INSERT INTO spt_photo (id, report_id, hospital_id, photo_url, storage_key, captured_by_device, file_hash, arm_side)
         VALUES (?,?,?,?,?,?,?,?)`).bind(uuid(), id, tenant, `/api/files/${f.key}`, f.key, ph.device ?? "unknown", f.hash, normSide(ph.arm_side)).run();
	}
	await audit(c, status === "SUBMITTED" && before.status !== "SUBMITTED" ? "SUBMIT_REPORT" : "UPDATE_REPORT", {
		resource_type: "spt_report",
		resource_id: id,
		before: { status: before.status },
		after: { status }
	});
	return c.json({
		ok: true,
		status
	});
});
/** 归档（仅医生） */
reports.post("/:id/archive", async (c) => {
	if (c.var.user.role !== "DOCTOR") return c.json({
		error: "FORBIDDEN",
		message: "仅医生可归档"
	}, 403);
	const id = c.req.param("id");
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("spt_report", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	if (before.status !== "SUBMITTED") return c.json({
		error: "BAD_STATE",
		message: "仅已提交的报告单可归档"
	}, 409);
	await db.update("spt_report", id, { status: "ARCHIVED" });
	await audit(c, "ARCHIVE_REPORT", {
		resource_type: "spt_report",
		resource_id: id
	});
	return c.json({ ok: true });
});
reports.delete("/:id", async (c) => {
	const id = c.req.param("id");
	const tenant = c.var.tenant;
	const before = await tenantDb(c.env.DB, tenant).first("spt_report", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({ error: "NOT_FOUND" }, 404);
	let grant = null;
	if (before.status !== "DRAFT") {
		if (c.var.user.role === "NURSE") return c.json({
			error: "FORBIDDEN",
			message: "护士仅可删除草稿"
		}, 403);
		grant = await consumeDangerToken(c, "DELETE_REPORT", id);
		if (!grant) return c.json({
			error: "NEED_DANGER_AUTH",
			message: `该报告单状态为${before.status === "SUBMITTED" ? "已提交" : "已归档"}，删除需双人密码授权。`,
			require_danger_auth: true,
			status: before.status
		}, 409);
	}
	const photos = await c.env.DB.prepare(`SELECT storage_key FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(id, tenant).all();
	for (const p of photos.results ?? []) if (p.storage_key) await deleteFile(c.env, p.storage_key);
	await c.env.DB.batch([
		c.env.DB.prepare(`DELETE FROM wheal_measurement WHERE session_id IN (SELECT id FROM wheal_session WHERE report_id = ?)`).bind(id),
		c.env.DB.prepare(`DELETE FROM wheal_session WHERE report_id = ?`).bind(id),
		c.env.DB.prepare(`DELETE FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(id, tenant),
		c.env.DB.prepare(`DELETE FROM spt_report_row_snapshot WHERE report_id = ? AND hospital_id = ?`).bind(id, tenant),
		c.env.DB.prepare(`DELETE FROM spt_report WHERE id = ? AND hospital_id = ?`).bind(id, tenant)
	]);
	await audit(c, "DELETE_REPORT", {
		resource_type: "spt_report",
		resource_id: id,
		before: {
			status: before.status,
			patient_id: before.patient_id,
			report_date: before.report_date
		},
		after: grant ? {
			authorized_by: grant.approver_name,
			approver_id: grant.approver_id,
			jti: grant.jti
		} : { authorized_by: null }
	});
	return c.json({ ok: true });
});
/** 追加照片（采集后立即上传） */
reports.post("/:id/photos", async (c) => {
	const id = c.req.param("id");
	const tenant = c.var.tenant;
	const b = await c.req.json();
	if (!b.data) return c.json({
		error: "BAD_REQUEST",
		message: "缺少图像数据"
	}, 400);
	const side = normSide(b.arm_side);
	const r = await c.env.DB.prepare(`SELECT id, status FROM spt_report WHERE id = ? AND hospital_id = ?`).bind(id, tenant).first();
	if (!r) return c.json({ error: "NOT_FOUND" }, 404);
	if (r.status === "ARCHIVED") return c.json({
		error: "LOCKED",
		message: "已归档不可追加照片"
	}, 409);
	const { bytes, contentType } = decodeImagePayload(b.data);
	const f = await putFile(c.env, "arm", tenant, bytes, contentType);
	const dup = await c.env.DB.prepare(`SELECT id, photo_url, COALESCE(arm_side, 'UNKNOWN') AS arm_side
       FROM spt_photo WHERE report_id = ? AND hospital_id = ? AND file_hash = ?`).bind(id, tenant, f.hash).first();
	if (dup) {
		if (side !== "UNKNOWN" && dup.arm_side === "UNKNOWN") {
			await c.env.DB.prepare(`UPDATE spt_photo SET arm_side = ? WHERE id = ? AND hospital_id = ?`).bind(side, dup.id, tenant).run();
			await audit(c, "UPDATE_REPORT_PHOTO_SIDE", {
				resource_type: "spt_photo",
				resource_id: dup.id,
				before: { arm_side: "UNKNOWN" },
				after: {
					arm_side: side,
					via: "dedup_upload"
				}
			});
			return c.json({
				id: dup.id,
				photo_url: dup.photo_url,
				deduped: true,
				arm_side: side
			});
		}
		return c.json({
			id: dup.id,
			photo_url: dup.photo_url,
			deduped: true,
			arm_side: dup.arm_side
		});
	}
	const pid = uuid();
	const device = b.device || (b.provider === "bridge" ? "bridge:unknown" : "webcam:unknown");
	await c.env.DB.prepare(`INSERT INTO spt_photo (id, report_id, hospital_id, photo_url, storage_key, captured_by_device, file_hash, arm_side)
     VALUES (?,?,?,?,?,?,?,?)`).bind(pid, id, tenant, `/api/files/${f.key}`, f.key, device, f.hash, side).run();
	await audit(c, "ADD_REPORT_PHOTO", {
		resource_type: "spt_photo",
		resource_id: pid,
		after: {
			report_id: id,
			device,
			size: f.size,
			arm_side: side
		}
	});
	return c.json({
		id: pid,
		photo_url: `/api/files/${f.key}`,
		size: f.size,
		arm_side: side
	});
});
/**
* 补标 / 更正照片的手臂侧别。
*
* 必须有这个接口，否则存量照片（arm_side = UNKNOWN）永远无法标注，
* 只能删掉重拍 —— 而照片是临床原始记录，为了补一个标签去删原始记录是本末倒置。
* 同时也覆盖「拍的时候按错了左右」这种现场高频失误。
*
* 已归档报告单不允许改：归档后的病历是封存件，改动要走解锁流程。
*/
reports.patch("/:id/photos/:photoId", async (c) => {
	const tenant = c.var.tenant;
	const { id, photoId } = c.req.param();
	const side = normSide((await c.req.json()).arm_side);
	const r = await c.env.DB.prepare(`SELECT status FROM spt_report WHERE id = ? AND hospital_id = ?`).bind(id, tenant).first();
	if (!r) return c.json({ error: "NOT_FOUND" }, 404);
	if (r.status === "ARCHIVED") return c.json({
		error: "LOCKED",
		message: "已归档不可修改照片标注"
	}, 409);
	const p = await c.env.DB.prepare(`SELECT id, COALESCE(arm_side, 'UNKNOWN') AS arm_side
       FROM spt_photo WHERE id = ? AND report_id = ? AND hospital_id = ?`).bind(photoId, id, tenant).first();
	if (!p) return c.json({ error: "NOT_FOUND" }, 404);
	await c.env.DB.prepare(`UPDATE spt_photo SET arm_side = ? WHERE id = ? AND hospital_id = ?`).bind(side, photoId, tenant).run();
	await audit(c, "UPDATE_REPORT_PHOTO_SIDE", {
		resource_type: "spt_photo",
		resource_id: photoId,
		before: { arm_side: p.arm_side },
		after: {
			arm_side: side,
			via: "manual"
		}
	});
	return c.json({
		ok: true,
		arm_side: side
	});
});
reports.delete("/:id/photos/:photoId", async (c) => {
	const tenant = c.var.tenant;
	const { id, photoId } = c.req.param();
	const p = await c.env.DB.prepare(`SELECT * FROM spt_photo WHERE id = ? AND report_id = ? AND hospital_id = ?`).bind(photoId, id, tenant).first();
	if (!p) return c.json({ error: "NOT_FOUND" }, 404);
	if (p.storage_key) await deleteFile(c.env, p.storage_key);
	await c.env.DB.prepare(`DELETE FROM spt_photo WHERE id = ? AND hospital_id = ?`).bind(photoId, tenant).run();
	await audit(c, "DELETE_REPORT_PHOTO", {
		resource_type: "spt_photo",
		resource_id: photoId
	});
	return c.json({ ok: true });
});
/** 本院医生/护士下拉 */
/* 临床端拉取本院可用的打印版式（含本院 logo key）。
 * 挂在 reports 下复用 authGuard+clinicalOnly+tenantGuard，
 * 医生/护士只能看到本院的版式，天然隔离。 */
reports.get("/meta/print-templates", async (c) => {
	const tenant = c.var.tenant;
	const r = await c.env.DB.prepare(`SELECT id, name, config_json, is_default FROM print_template WHERE hospital_id = ? AND is_active = 1 ORDER BY is_default DESC, created_at DESC`).bind(tenant).all();
	const h = await c.env.DB.prepare(`SELECT name, logo_url FROM hospital WHERE id = ?`).bind(tenant).first();
	return c.json({
		data: r.results ?? [],
		hospital: {
			name: h?.name || "",
			logo_url: h?.logo_url || null
		}
	});
});
reports.get("/meta/staff", async (c) => {
	const rows = await c.env.DB.prepare(`SELECT id, real_name, role FROM user_account WHERE hospital_id = ? AND is_active = 1 AND role IN ('DOCTOR','NURSE') ORDER BY role, real_name`).bind(c.var.tenant).all();
	return c.json({ data: rows.results ?? [] });
});
//#endregion
//#region src/lib/ocr.ts
var PROMPT = `你是医院就诊卡 OCR 助手。图片是通过摄像头拍摄的中国医院就诊卡（可能有反光、倾斜、手写体）。
请仔细辨认卡面上的文字，尽可能提取信息，并**仅**输出如下 JSON（不要输出解释、不要用 markdown 代码块）：
{"name":"姓名","gender":"男或女","birth_date":"YYYY-MM-DD","visit_card_no":"就诊卡号","contact_person":"联系人","contact_phone":"联系电话"}

识别要点：
- 姓名：卡面「姓名」「姓 名」后的中文姓名
- 出生日期：形如「2018年03月16日」「2018-03-16」，统一转成 YYYY-MM-DD
- 就诊卡号：「就诊卡号」「卡号」「门诊号」「病案号」后的数字/字母串，只保留数字与字母
- 联系人可能只有姓氏（如「刘」），照实填写
- 确实看不到的字段填空字符串 ""，不要猜测、不要编造`;
/** 支持 gpt-5 系列（max_completion_tokens）与旧模型（max_tokens）两种参数名 */
function buildBody(model, dataUrl, useCompletionTokens) {
	const body = {
		model,
		messages: [{
			role: "user",
			content: [{
				type: "text",
				text: PROMPT
			}, {
				type: "image_url",
				image_url: { url: dataUrl }
			}]
		}]
	};
	if (useCompletionTokens) body.max_completion_tokens = 1200;
	else {
		body.max_tokens = 800;
		body.temperature = 0;
	}
	return body;
}
async function runOcr(env, imageDataUrl) {
	const base = env.OCR_API_BASE;
	const key = env.OCR_API_KEY;
	if (!base || !key) return {
		available: false,
		engine: "none",
		text: "",
		error_code: "NOT_CONFIGURED",
		error_detail: "服务端未配置 OCR_API_BASE / OCR_API_KEY"
	};
	const dataUrl = imageDataUrl.startsWith("data:") ? imageDataUrl : `data:image/jpeg;base64,${imageDataUrl}`;
	if (dataUrl.length < 2e3) return {
		available: false,
		engine: "none",
		text: "",
		error_code: "BAD_IMAGE",
		error_detail: "图像数据过小，可能未成功抓帧"
	};
	const model = env.OCR_MODEL || "gpt-5-mini";
	const url = `${base.replace(/\/$/, "")}/chat/completions`;
	const started = Date.now();
	const preferCompletionTokens = /^(gpt-5|o[13])/.test(model);
	const attempts = [preferCompletionTokens, !preferCompletionTokens];
	let lastErr = null;
	for (const useCompletionTokens of attempts) try {
		const resp = await fetch(url, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				Authorization: `Bearer ${key}`,
				"User-Agent": "SPT-OCR/1.0"
			},
			body: JSON.stringify(buildBody(model, dataUrl, useCompletionTokens)),
			signal: AbortSignal.timeout(45e3)
		});
		if (!resp.ok) {
			const detail = (await resp.text().catch(() => "")).slice(0, 500);
			lastErr = {
				available: false,
				engine: model,
				text: "",
				error_code: "HTTP_ERROR",
				error_detail: `HTTP ${resp.status}: ${detail || resp.statusText}`,
				elapsed_ms: Date.now() - started
			};
			if (resp.status === 400) continue;
			return lastErr;
		}
		const text = (await resp.json())?.choices?.[0]?.message?.content ?? "";
		if (!text.trim()) {
			lastErr = {
				available: false,
				engine: model,
				text: "",
				error_code: "EMPTY_RESPONSE",
				error_detail: "视觉模型返回空内容（可能图像过暗/过曝或超出模型能力）",
				elapsed_ms: Date.now() - started
			};
			continue;
		}
		return {
			available: true,
			engine: model,
			text,
			elapsed_ms: Date.now() - started
		};
	} catch (e) {
		const err = e;
		const isTimeout = err?.name === "TimeoutError" || err?.name === "AbortError";
		lastErr = {
			available: false,
			engine: model,
			text: "",
			error_code: isTimeout ? "TIMEOUT" : "NETWORK",
			error_detail: isTimeout ? "OCR 服务响应超时（45 秒）" : String(err?.message ?? e).slice(0, 300),
			elapsed_ms: Date.now() - started
		};
		if (isTimeout) return lastErr;
	}
	return lastErr ?? {
		available: false,
		engine: model,
		text: "",
		error_code: "NETWORK",
		error_detail: "未知错误",
		elapsed_ms: Date.now() - started
	};
}
/**
* 解析 OCR 文本 → 结构化字段。
* 兼容两种输入：模型返回的 JSON，或纯文本 OCR 结果（正则兜底）。
*/
function parseVisitCard(text) {
	const out = {
		name: "",
		gender: "",
		birth_date: "",
		visit_card_no: "",
		contact_person: "",
		contact_phone: ""
	};
	if (!text) return out;
	const cleaned = text.replace(/```(?:json)?/gi, "");
	const jm = /\{[\s\S]*\}/.exec(cleaned);
	if (jm) try {
		const j = JSON.parse(jm[0]);
		out.name = String(j.name ?? "").trim();
		out.gender = normGender(String(j.gender ?? ""));
		out.birth_date = normDate(String(j.birth_date ?? ""));
		out.visit_card_no = String(j.visit_card_no ?? "").replace(/[^0-9A-Za-z]/g, "").trim();
		out.contact_person = String(j.contact_person ?? "").trim();
		out.contact_phone = String(j.contact_phone ?? "").replace(/[^\d-]/g, "").trim();
		if (out.name || out.visit_card_no || out.birth_date) return out;
	} catch {}
	const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
	const joined = lines.join("\n");
	/** 去掉尾部勾选符号与标点噪声 */
	const clean = (s) => s.replace(/[Vv√\s:：]+$/g, "").trim();
	/** 卡面固定话术/院名，绝不可作为字段值 */
	const NOISE = /本卡|限本人|禁止外借|凭证|妥善保管|妥着保管|医院|就诊卡|HOSPITAL|CHANGCHUN|有效/i;
	/**
	* 按标签取值：先看同行，再向下最多 look 行（遇到噪声或新标签立即停止）
	*/
	const labelValue = (labels, valPat, look = 2) => {
		for (let i = 0; i < lines.length; i++) {
			if (!labels.test(lines[i])) continue;
			const same = clean(lines[i].replace(new RegExp("^.*?" + labels.source + "\\s*[:：]?\\s*"), ""));
			if (same && !NOISE.test(same)) {
				const v = valPat.exec(same);
				if (v) return clean(v[0]);
			}
			for (let j = i + 1; j < Math.min(i + 1 + look, lines.length); j++) {
				const nxt = clean(lines[j]);
				if (NOISE.test(nxt)) break;
				if (/[:：]/.test(nxt)) break;
				const v = valPat.exec(nxt);
				if (v) return clean(v[0]);
				break;
			}
		}
		return "";
	};
	out.name = labelValue(/(?:姓\s*名|(?:^|\n)名)/, /[\u4e00-\u9fa5·]{2,10}/);
	out.gender = normGender(labelValue(/(?:性\s*别|(?:^|\n)别)/, /[男女]/));
	if (!out.name) {
		const NOT_NAME = /^(?:男|女|岁|周岁|联系人|监护人|家长|姓名|性别|出生|生日|卡号|门诊|病案|住院|电话|手机|地址|民族|汉族)/;
		let anchor = lines.length;
		for (let i = 0; i < lines.length; i++) if (/性\s*别|(?:^|\n)别\s*[:：]|出生日期|出生|生日/.test(lines[i])) {
			anchor = i;
			break;
		}
		for (let i = anchor - 1; i >= 0; i--) {
			const cand = clean(lines[i]);
			if (!/^[\u4e00-\u9fa5·]{2,4}$/.test(cand)) continue;
			if (NOISE.test(cand) || NOT_NAME.test(cand)) continue;
			out.name = cand;
			break;
		}
	}
	out.birth_date = normDate(labelValue(/(?:出生日期|出生|生日)/, /(?:19|20)\d{2}\s*[-年/.]\s*\d{1,2}\s*[-月/.]\s*\d{1,2}/) || (/((?:19|20)\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})/.exec(joined)?.[0] ?? ""));
	for (const re of [/就诊卡号\s*[:：]?\s*([0-9A-Za-z]{4,30})/, /(?:卡号|门诊号|病案号|住院号)\s*[:：]?\s*([0-9A-Za-z]{4,30})/]) {
		const m = re.exec(joined);
		if (m && /\d/.test(m[1])) {
			out.visit_card_no = m[1];
			break;
		}
	}
	if (!out.visit_card_no) out.visit_card_no = labelValue(/(?:就诊卡号|卡\s*号)/, /[0-9]{4,30}/);
	const cp = labelValue(/(?:联\s*系\s*人|监护人|家长)/, /[\u4e00-\u9fa5·]{1,6}/, 1);
	out.contact_person = /^[\u4e00-\u9fa5·]{1,6}$/.test(cp) && !NOISE.test(cp) ? cp : "";
	out.contact_phone = /(?:联系电话|电话|手机)\s*[:：]?\s*(1\d{10}|\d{3,4}-?\d{7,8})/.exec(joined)?.[1] ?? /(?:^|[^\d])(1[3-9]\d{9})(?:[^\d]|$)/.exec(joined)?.[1] ?? "";
	return out;
}
function normGender(s) {
	if (/男|^m$|male/i.test(s)) return "M";
	if (/女|^f$|female/i.test(s)) return "F";
	return s ? "UNKNOWN" : "";
}
/** HIS 界面上会出现、但绝不可能是字段值的词 */
var HIS_NOISE = /系统设置|窗口|设置|退出|查询|查间|开嘱|开瞩|费用类别|费用性质|费用合计|已收费|收费项目|医保等级|单价|数量|单位|金额|科室用药|申请单|胶片|当前状态|新增|删除|修改|工作单位|公费证号|优惠类别|联系电话|费用信息/;
/**
* 「岁」被 OCR 读成数字的修复。
*
* 实测样本：「10岁」→「105」。规律是「岁」在小字号 + 摩尔纹下被判成 5，
* 偶尔是 3 或 8。判据用**医学常识**而不是字符相似度：
*   - 儿童医院场景，三位数年龄一定是错的（现存最长寿者 122 岁）
*   - 若去掉末位后落在 0–120，则末位极可能就是被误读的「岁」
* 只在「明确带年龄标签」的上下文里调用，避免误伤真实的三位数（如卡号片段）。
*/
function fixAgeDigits(raw) {
	const m = /(\d{1,3})\s*([岁歳])?/.exec(raw);
	if (!m) return null;
	const digits = m[1];
	const hasAgeChar = !!m[2];
	const n = Number(digits);
	if (!Number.isFinite(n)) return null;
	if (hasAgeChar) return n >= 0 && n <= 120 ? n : null;
	if (digits.length === 3) {
		const t = Number(digits.slice(0, 2));
		return t >= 0 && t <= 120 ? t : null;
	}
	return n >= 0 && n <= 120 ? n : null;
}
/** 摩尔纹常见字形误识别的定向修正（只用于科室/项目这类**枚举性**文本） */
function fixMoire(s) {
	return s.replace(/其晶咽喉|耳晶咽喉|其鼻咽喉/g, "耳鼻咽喉").replace(/自善/g, "自费").replace(/过敏反应科[哮响][^\u4e00-\u9fa5]*$/g, "过敏反应科");
}
function parseHisScreen(text) {
	const out = {
		name: "",
		gender: "",
		age_years: null,
		visit_card_no: "",
		applied_at: "",
		department: "",
		applying_doctor: "",
		serial_no: "",
		clinical_diagnosis: "",
		exam_item: ""
	};
	if (!text) return out;
	const cleaned = text.replace(/```(?:json)?/gi, "");
	const jm = /\{[\s\S]*\}/.exec(cleaned);
	if (jm) try {
		const j = JSON.parse(jm[0]);
		out.name = String(j.name ?? "").trim();
		out.gender = normGender(String(j.gender ?? ""));
		out.age_years = fixAgeDigits(String(j.age ?? j.age_years ?? ""));
		out.visit_card_no = String(j.visit_card_no ?? j.medical_record_no ?? "").replace(/[^0-9A-Za-z]/g, "");
		out.applied_at = normDate(String(j.applied_at ?? ""));
		out.department = String(j.department ?? "").trim();
		out.applying_doctor = String(j.applying_doctor ?? j.doctor ?? "").trim();
		out.serial_no = String(j.serial_no ?? "").trim();
		out.clinical_diagnosis = String(j.clinical_diagnosis ?? j.diagnosis ?? "").trim();
		out.exam_item = String(j.exam_item ?? "").trim();
		if (out.name || out.visit_card_no) return out;
	} catch {}
	const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
	const sep = lines.join("");
	/** 去掉行边界的拼接：用于标签本身被切行的情况（实测「姓」+「名：张赫然」） */
	const flat = lines.join("");
	/**
	* 取标签后的值，三级回退。
	*
	* 为什么要三级：单一策略都会在真实数据上翻车 ——
	*   1) 行内模式（sep）：绝大多数字段标签与值同行（「性别男」「开单医生沙颖」），
	*      \u0001 天然截断，最准，优先用。
	*   2) 跨行 + 停止词（flat）：标签本身被切行时（「姓」/「名：张赫然」）
	*      行内模式匹配不到标签，必须去掉行边界；但这样值会黏住下一个标签，
	*      所以用惰性匹配 + STOP 前瞻把它切断。
	*   3) 跨行贪婪：值后面跟的既不是 STOP 也不是行尾（如诊断后面跟着「000」）时
	*      第 2 级会整体失配，用无约束匹配兜底。
	* 冒号可能是「：」「:」或整个缺失，故 [:：]? 而非必需。
	*/
	const grab = (label, valPat) => {
		const m1 = new RegExp(label.source + "\\s*[:：]?[ \\t]*(" + valPat + ")").exec(sep);
		if (m1?.[1]?.trim()) return m1[1].trim();
		const m2 = new RegExp(label.source + "\\s*[:：]?\\s*(" + valPat + "?)(?=姓名|性别|年龄|工作单位|检查项目|检查部位|检查科室|执行科室|开单科室|执行时间|开单时间|开单医生|申请医生|费用|当前状态|申请单|临床诊断|就诊卡号|联系电话|公费证号|优惠类别|收费项目|医保等级|单价|数量|单位|金额|胶片|备注|流水号|病历号|科室|$)").exec(flat);
		if (m2?.[1]?.trim()) return m2[1].trim();
		return new RegExp(label.source + "\\s*[:：]?\\s*(" + valPat + ")").exec(flat)?.[1]?.trim() ?? "";
	};
	const grabFlat = grab;
	out.name = grabFlat(/姓\s*名/, "[\\u4e00-\\u9fa5·]{2,6}");
	if (HIS_NOISE.test(out.name)) out.name = "";
	out.gender = normGender(grabFlat(/性\s*别/, "[男女]"));
	out.age_years = fixAgeDigits(grabFlat(/年\s*龄/, "\\d{1,3}\\s*[岁歳]?"));
	for (const re of [/就诊卡号\s*[:：]?\s*([0-9A-Za-z]{4,30})/, /(?:病历号|病案号|门诊号|卡号)\s*[:：]?\s*([0-9A-Za-z]{4,30})/]) {
		const m = re.exec(flat);
		if (m && /\d/.test(m[1])) {
			out.visit_card_no = m[1];
			break;
		}
	}
	out.applied_at = normDate(/开单时间\s*[:：]?\s*((?:19|20)\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2})/.exec(flat)?.[1] ?? /申请单\s*\d*\s*[（(]\s*((?:19|20)\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2})/.exec(flat)?.[1] ?? "");
	out.department = fixMoire(grabFlat(/开单科室/, "[\\u4e00-\\u9fa5][\\u4e00-\\u9fa5（()）]{1,14}") || grabFlat(/(?:检查科室|执行科室)/, "[\\u4e00-\\u9fa5（(][\\u4e00-\\u9fa5（()）]{1,14}") || grabFlat(/科\s*室/, "[\\u4e00-\\u9fa5]{2,12}")).replace(/[（(].*$/, "");
	out.applying_doctor = grabFlat(/(?:开单医生|申请医生|医\s*生)/, "[\\u4e00-\\u9fa5·]{2,4}");
	if (HIS_NOISE.test(out.applying_doctor)) out.applying_doctor = "";
	out.clinical_diagnosis = grabFlat(/临床诊断/, "[\\u4e00-\\u9fa5\\[\\]（()）、，,]{2,40}").replace(/(?:检查|执行|开单|费用|当前|申请).*$/, "").trim();
	out.exam_item = grabFlat(/检查项目/, "[\\u4e00-\\u9fa5]{2,30}").replace(/(?:检查|执行|开单|费用).*$/, "");
	out.serial_no = grabFlat(/流水号/, "[0-9A-Za-z-]{2,30}");
	return out;
}
function normDate(s) {
	if (!s) return "";
	const m = /((?:19|20)\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})/.exec(s);
	if (!m) return "";
	return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
}
//#endregion
//#region src/routes/capture.ts
/** 失败原因 → 可执行的中文处置建议（就诊卡与屏幕识别共用） */
var OCR_ADVICE = {
	NOT_CONFIGURED: "服务端尚未配置 OCR 识别服务，请联系管理员在部署环境中设置 OCR_API_BASE / OCR_API_KEY。",
	HTTP_ERROR: "OCR 服务返回错误，请稍后重试；若持续失败请联系管理员检查识别服务配置与额度。",
	TIMEOUT: "OCR 服务响应超时，请检查网络后重试。",
	EMPTY_RESPONSE: "识别服务未返回内容，请调整角度、避免反光后重拍。",
	NETWORK: "无法连接 OCR 服务，请检查服务端网络。",
	BAD_IMAGE: "拍摄的图像无效（可能是黑屏），请确认摄像头画面正常后重拍。"
};
var capture = new Hono();
capture.use("*", authGuard, clinicalOnly, tenantGuard);
capture.get("/settings", async (c) => {
	const s = await c.env.DB.prepare(`SELECT * FROM capture_setting WHERE user_id = ? AND hospital_id = ?`).bind(c.var.user.id, c.var.tenant).first();
	return c.json({ data: s ?? {
		user_id: c.var.user.id,
		mode: "AUTO",
		webcam_device_id: null,
		webcam_device_label: null,
		bridge_url: "ws://127.0.0.1:9911",
		mirror: 0,
		resolution: "1600x1200"
	} });
});
capture.put("/settings", async (c) => {
	const b = await c.req.json();
	const mode = [
		"AUTO",
		"WEBCAM",
		"BRIDGE"
	].includes(b.mode) ? b.mode : "AUTO";
	await c.env.DB.prepare(`INSERT INTO capture_setting (user_id, hospital_id, mode, webcam_device_id, webcam_device_label, bridge_url, mirror, resolution, updated_at)
     VALUES (?,?,?,?,?,?,?,?,datetime('now'))
     ON CONFLICT(user_id) DO UPDATE SET
       mode=excluded.mode, webcam_device_id=excluded.webcam_device_id,
       webcam_device_label=excluded.webcam_device_label, bridge_url=excluded.bridge_url,
       mirror=excluded.mirror, resolution=excluded.resolution, updated_at=datetime('now')`).bind(c.var.user.id, c.var.tenant, mode, b.webcam_device_id ?? null, b.webcam_device_label ?? null, b.bridge_url ?? "ws://127.0.0.1:9911", b.mirror ? 1 : 0, b.resolution ?? "1600x1200").run();
	await audit(c, "UPDATE_CAPTURE_SETTING", {
		resource_type: "capture_setting",
		resource_id: c.var.user.id,
		after: b
	});
	return c.json({ ok: true });
});
capture.get("/devices", async (c) => {
	const rows = await tenantDb(c.env.DB, c.var.tenant).all("device_bind", {
		where: "user_id = ? AND status <> ?",
		binds: [c.var.user.id, "REVOKED"],
		orderBy: "bound_at DESC"
	});
	return c.json({ data: rows.map((r) => ({
		...r,
		bind_token: void 0,
		pair_code: void 0
	})) });
});
/**
* 登记 USB 摄像头（当前阶段主用路径）。
* 浏览器 enumerateDevices() 得到 deviceId/label 后调用本接口做记录，便于审计与后续替换。
*/
capture.post("/devices/webcam", async (c) => {
	const b = await c.req.json();
	if (!b.device_id) return c.json({
		error: "BAD_REQUEST",
		message: "缺少摄像头 deviceId"
	}, 400);
	const db = tenantDb(c.env.DB, c.var.tenant);
	const exist = await db.first("device_bind", {
		where: "user_id = ? AND device_id = ? AND provider = ?",
		binds: [
			c.var.user.id,
			b.device_id,
			"webcam"
		]
	});
	if (exist) {
		await db.rawRun(`UPDATE device_bind SET device_model=?, os_version=?, status='ONLINE', last_seen_at=datetime('now')
       WHERE id = ? AND hospital_id = ?`, b.label ?? exist.device_model, b.os ?? exist.os_version, exist.id, c.var.tenant);
		return c.json({
			id: exist.id,
			updated: true
		});
	}
	const id = uuid();
	await db.insert("device_bind", {
		id,
		user_id: c.var.user.id,
		device_id: b.device_id,
		device_model: b.label ?? "USB Camera",
		os_version: b.os ?? null,
		provider: "webcam",
		bound_at: (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace("T", " "),
		last_seen_at: (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace("T", " "),
		status: "ONLINE"
	});
	await audit(c, "BIND_WEBCAM", {
		resource_type: "device_bind",
		resource_id: id,
		after: { label: b.label }
	});
	return c.json({ id });
});
/**
* 高拍仪绑定：生成 6 位配对码（预留通道，后期插上高拍仪即可用）
*/
capture.post("/devices/pair-code", async (c) => {
	const db = tenantDb(c.env.DB, c.var.tenant);
	const code = randomPairCode();
	const id = uuid();
	await db.insert("device_bind", {
		id,
		user_id: c.var.user.id,
		device_id: `PENDING-${code}`,
		provider: "bridge",
		pair_code: code,
		status: "OFFLINE"
	});
	await audit(c, "CREATE_PAIR_CODE", {
		resource_type: "device_bind",
		resource_id: id
	});
	return c.json({
		id,
		pair_code: code,
		expires_in: 600,
		ws_hint: "ws://127.0.0.1:9911"
	});
});
/** 本地客户端用配对码换取绑定 token（客户端侧调用，此处走登录态简化演示） */
capture.post("/devices/claim", async (c) => {
	const b = await c.req.json();
	if (!b.pair_code || !b.device_id) return c.json({
		error: "BAD_REQUEST",
		message: "缺少配对码或设备序列号"
	}, 400);
	const db = tenantDb(c.env.DB, c.var.tenant);
	const pending = await db.first("device_bind", {
		where: "pair_code = ? AND status = 'OFFLINE' AND provider = 'bridge'",
		binds: [b.pair_code]
	});
	if (!pending) return c.json({
		error: "NOT_FOUND",
		message: "配对码无效或已使用"
	}, 404);
	const token = uuid();
	await db.rawRun(`UPDATE device_bind SET device_id=?, device_model=?, os_version=?, bind_token=?, pair_code=NULL,
            bound_at=datetime('now'), last_seen_at=datetime('now'), status='ONLINE'
     WHERE id = ? AND hospital_id = ?`, b.device_id, b.device_model ?? "高拍仪", b.os ?? null, token, pending.id, c.var.tenant);
	await audit(c, "BIND_BRIDGE_DEVICE", {
		resource_type: "device_bind",
		resource_id: pending.id,
		after: { device_id: b.device_id }
	});
	return c.json({
		id: pending.id,
		bind_token: token
	});
});
/** 心跳 */
capture.post("/devices/:id/heartbeat", async (c) => {
	await tenantDb(c.env.DB, c.var.tenant).rawRun(`UPDATE device_bind SET last_seen_at=datetime('now'), status='ONLINE' WHERE id = ? AND hospital_id = ?`, c.req.param("id"), c.var.tenant);
	return c.json({ ok: true });
});
/** 解绑 */
capture.delete("/devices/:id", async (c) => {
	const db = tenantDb(c.env.DB, c.var.tenant);
	const id = c.req.param("id");
	const d = await db.first("device_bind", {
		where: "id = ? AND user_id = ?",
		binds: [id, c.var.user.id]
	});
	if (!d) return c.json({ error: "NOT_FOUND" }, 404);
	await db.rawRun(`UPDATE device_bind SET status='REVOKED', bind_token=NULL WHERE id = ? AND hospital_id = ?`, id, c.var.tenant);
	await audit(c, "REVOKE_DEVICE", {
		resource_type: "device_bind",
		resource_id: id,
		before: { device_id: d.device_id }
	});
	return c.json({ ok: true });
});
capture.post("/ocr/visit-card", async (c) => {
	const b = await c.req.json();
	if (!b.data) return c.json({
		error: "BAD_REQUEST",
		message: "缺少图像数据"
	}, 400);
	const tenant = c.var.tenant;
	const { bytes, contentType } = decodeImagePayload(b.data);
	const saved = await putFile(c.env, "cards", tenant, bytes, contentType);
	const ocr = await runOcr(c.env, b.data);
	const fields = parseVisitCard(ocr.text);
	let matched = null;
	if (fields.visit_card_no) {
		matched = await tenantDb(c.env.DB, tenant).first("patient", {
			where: "visit_card_no = ?",
			binds: [fields.visit_card_no]
		});
		if (matched) matched.age = patientAge(matched);
	}
	await audit(c, "OCR_VISIT_CARD", {
		resource_type: "patient",
		resource_id: matched?.id,
		after: {
			engine: ocr.engine,
			provider: b.provider ?? "webcam",
			device: b.device ?? null,
			recognized_fields: Object.keys(fields).filter((k) => fields[k]),
			matched: !!matched,
			ocr_available: ocr.available,
			error_code: ocr.error_code ?? null,
			elapsed_ms: ocr.elapsed_ms ?? null
		}
	});
	const recognizedCount = Object.keys(fields).filter((k) => fields[k]).length;
	const ADVICE = OCR_ADVICE;
	let message;
	if (!ocr.available) message = ADVICE[ocr.error_code ?? "NETWORK"] ?? "识别服务不可用，已保存卡片图像，请人工核对填写。";
	else if (recognizedCount === 0) message = "识别服务已返回，但未能从卡面提取到有效信息。建议：将卡片放平、避开反光、让卡面填满取景框后重拍。";
	else if (matched) message = "已匹配到患者档案，信息已自动带出";
	else message = `已识别 ${recognizedCount} 项信息，但患者库中无此就诊卡号，请核对后新建患者`;
	return c.json({
		engine: ocr.engine,
		ocr_available: ocr.available,
		raw_text: ocr.text,
		fields,
		/** 未识别字段 → 前端标黄提示人工校对 */
		unrecognized: [
			"name",
			"gender",
			"birth_date",
			"visit_card_no",
			"contact_person",
			"contact_phone"
		].filter((k) => !fields[k]),
		matched_patient: matched,
		card_image_url: `/api/files/${saved.key}`,
		recognized_count: recognizedCount,
		error_code: ocr.error_code ?? null,
		error_detail: ocr.error_detail ?? null,
		elapsed_ms: ocr.elapsed_ms ?? null,
		message
	});
});
capture.post("/ocr/his-screen", async (c) => {
	const b = await c.req.json();
	if (!b.data) return c.json({
		error: "BAD_REQUEST",
		message: "缺少图像数据"
	}, 400);
	const tenant = c.var.tenant;
	const { bytes, contentType } = decodeImagePayload(b.data);
	const saved = await putFile(c.env, "cards", tenant, bytes, contentType);
	const ocr = await runOcr(c.env, b.data);
	const fields = parseHisScreen(ocr.text);
	let matched = null;
	if (fields.visit_card_no) {
		matched = await tenantDb(c.env.DB, tenant).first("patient", {
			where: "visit_card_no = ?",
			binds: [fields.visit_card_no]
		});
		if (matched) matched.age = patientAge(matched);
	}
	await audit(c, "OCR_HIS_SCREEN", {
		resource_type: "patient",
		resource_id: matched?.id,
		after: {
			engine: ocr.engine,
			provider: b.provider ?? "webcam",
			device: b.device ?? null,
			recognized_fields: Object.keys(fields).filter((k) => fields[k]),
			matched: !!matched,
			ocr_available: ocr.available,
			error_code: ocr.error_code ?? null,
			elapsed_ms: ocr.elapsed_ms ?? null
		}
	});
	const unrecognized = [
		"name",
		"gender",
		"age_years",
		"visit_card_no",
		"applied_at",
		"department"
	].filter((k) => {
		const v = fields[k];
		return v === "" || v === null || v === void 0;
	});
	const recognizedCount = Object.keys(fields).filter((k) => {
		const v = fields[k];
		return v !== "" && v !== null && v !== void 0;
	}).length;
	let message;
	if (!ocr.available) message = OCR_ADVICE[ocr.error_code ?? "NETWORK"] ?? "识别服务不可用，已保存屏幕照片，请人工核对填写。";
	else if (recognizedCount === 0) message = "识别服务已返回，但未能从屏幕提取到有效信息。建议：正对屏幕（避免斜拍）、把患者信息区填满取景框、关掉屏幕反光后重拍。";
	else if (unrecognized.length > 0) message = `已识别 ${recognizedCount} 项，其中 ${unrecognized.length} 项关键信息未识别，请人工补全后再保存。`;
	else if (matched) message = "已匹配到患者档案，申请信息已带出";
	else message = `已识别 ${recognizedCount} 项信息，患者库中无此病历号，可据此新建患者`;
	return c.json({
		engine: ocr.engine,
		ocr_available: ocr.available,
		raw_text: ocr.text,
		fields,
		/** 拆成两块，前端可分别填到「患者档案」与「本次申请」 */
		patient: {
			name: fields.name,
			gender: fields.gender,
			age_years: fields.age_years,
			visit_card_no: fields.visit_card_no
		},
		report: {
			applied_at: fields.applied_at,
			department: fields.department,
			applying_doctor: fields.applying_doctor,
			serial_no: fields.serial_no,
			clinical_diagnosis: fields.clinical_diagnosis,
			medical_record_no: fields.visit_card_no,
			exam_item: fields.exam_item
		},
		unrecognized,
		matched_patient: matched,
		screen_image_url: `/api/files/${saved.key}`,
		recognized_count: recognizedCount,
		error_code: ocr.error_code ?? null,
		error_detail: ocr.error_detail ?? null,
		elapsed_ms: ocr.elapsed_ms ?? null,
		message
	});
});
/** 通用图像暂存（tmp/ 前缀，OCR 中间产物） */
capture.post("/upload-temp", async (c) => {
	const b = await c.req.json();
	if (!b.data) return c.json({ error: "BAD_REQUEST" }, 400);
	const { bytes, contentType } = decodeImagePayload(b.data);
	const f = await putFile(c.env, "tmp", c.var.tenant, bytes, contentType);
	return c.json({
		key: f.key,
		url: `/api/files/${f.key}`,
		size: f.size
	});
});
//#endregion
//#region src/lib/xlsx.ts
/**
* 极简 SpreadsheetML (Excel 2003 XML) 生成器。
* 选型理由：Workers 有 10MB 体积上限与 CPU 时限，引入 SheetJS 过重；
* Excel 2003 XML 是纯文本格式，Excel / WPS / LibreOffice 均可直接打开，且支持多 sheet 与列宽。
*/
function esc(v) {
	return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "");
}
function buildWorkbook(sheets) {
	return `<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Styles>
 <Style ss:ID="Default" ss:Name="Normal"><Alignment ss:Vertical="Center"/><Font ss:FontName="微软雅黑" ss:Size="10"/></Style>
 <Style ss:ID="sHead"><Font ss:FontName="微软雅黑" ss:Size="10" ss:Bold="1" ss:Color="#FFFFFF"/>
  <Interior ss:Color="#1F6FEB" ss:Pattern="Solid"/><Alignment ss:Horizontal="Center" ss:Vertical="Center"/></Style>
</Styles>
${sheets.map((s) => {
		const cols = (s.colWidths ?? s.header.map(() => 110)).map((w) => `<Column ss:Width="${w}"/>`).join("");
		const head = `<Row ss:StyleID="sHead">${s.header.map((h) => `<Cell><Data ss:Type="String">${esc(h)}</Data></Cell>`).join("")}</Row>`;
		const rows = s.rows.map((r) => `<Row>${r.map((v) => typeof v === "number" ? `<Cell><Data ss:Type="Number">${v}</Data></Cell>` : `<Cell><Data ss:Type="String">${esc(v)}</Data></Cell>`).join("")}</Row>`).join("");
		return `<Worksheet ss:Name="${esc(s.name).slice(0, 31)}"><Table>${cols}${head}${rows}</Table>
        <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel"><FreezePanes/><SplitHorizontal>1</SplitHorizontal><TopRowBottomPane>1</TopRowBottomPane><ActivePane>2</ActivePane></WorksheetOptions></Worksheet>`;
	}).join("")}
</Workbook>`;
}
function toCsv(header, rows) {
	const q = (v) => {
		const s = String(v ?? "");
		return /[",\n]/.test(s) ? `"${s.replace(/"/g, "\"\"")}"` : s;
	};
	return "﻿" + [header.map(q).join(","), ...rows.map((r) => r.map(q).join(","))].join("\r\n");
}
//#endregion
//#region src/routes/exports.ts
var exportsRoute = new Hono();
exportsRoute.use("*", authGuard, tenantGuard);
function buildQuery(c, f) {
	const user = c.var.user;
	const conds = ["1=1"];
	const binds = [];
	if (user.role === "PLATFORM_ADMIN") {
		if (f.hospital_id && f.hospital_id !== "PLATFORM") {
			conds.push("r.hospital_id = ?");
			binds.push(f.hospital_id);
		}
	} else {
		conds.push("r.hospital_id = ?");
		binds.push(user.hospital_id);
	}
	if (f.from) {
		conds.push("r.report_date >= ?");
		binds.push(f.from);
	}
	if (f.to) {
		conds.push("r.report_date <= ?");
		binds.push(f.to);
	}
	if (f.status) {
		conds.push("r.status = ?");
		binds.push(f.status);
	}
	if (f.doctor_id) {
		conds.push("r.doctor_id = ?");
		binds.push(f.doctor_id);
	}
	if (f.operator_id) {
		conds.push("r.operator_id = ?");
		binds.push(f.operator_id);
	}
	if (f.kw) {
		conds.push("(p.name LIKE ? OR p.visit_card_no LIKE ?)");
		binds.push(`%${f.kw}%`, `%${f.kw}%`);
	}
	if (f.ids?.length) {
		conds.push(`r.id IN (${f.ids.map(() => "?").join(",")})`);
		binds.push(...f.ids);
	}
	return {
		where: conds.join(" AND "),
		binds
	};
}
var SQL_BASE = `
  FROM spt_report r
  JOIN patient p ON p.id = r.patient_id AND p.hospital_id = r.hospital_id
  LEFT JOIN user_account d ON d.id = r.doctor_id AND d.hospital_id = r.hospital_id
  LEFT JOIN user_account o ON o.id = r.operator_id AND o.hospital_id = r.hospital_id
  LEFT JOIN hospital h ON h.id = r.hospital_id
  LEFT JOIN spt_template t ON t.id = r.template_id
`;
async function fetchRows(c, f, limit, offset) {
	const { where, binds } = buildQuery(c, f);
	return (await c.env.DB.prepare(`SELECT r.id, r.hospital_id, r.report_date, r.status, r.symptoms, r.notes, r.created_at, r.submitted_at,
            r.template_id, r.template_name_snapshot, t.is_deleted AS template_deleted,
            p.name AS patient_name, p.gender, p.birth_date, p.age_years, p.visit_card_no, p.contact_person, p.contact_phone,
            d.real_name AS doctor_name, o.real_name AS operator_name, h.name AS hospital_name
     ${SQL_BASE} WHERE ${where}
     ORDER BY r.report_date DESC, r.created_at DESC LIMIT ? OFFSET ?`).bind(...binds, limit, offset).all()).results ?? [];
}
async function countRows(c, f) {
	const { where, binds } = buildQuery(c, f);
	return (await c.env.DB.prepare(`SELECT COUNT(*) AS c ${SQL_BASE} WHERE ${where}`).bind(...binds).first())?.c ?? 0;
}
/**
* 计算本次导出应输出的位置列数。
*
* 取本批报告单中实际出现的最大位置号（排除对照位 101/102），且不低于默认 20。
* **异步分片导出必须全批使用同一列数**，否则各分片列数不一致，合并后会整体错列——
* 因此任务创建时就把列数固化进 filter_json，后续每个分片都复用该值。
*/
async function resolveExportPosCount(c, f) {
	const { where, binds } = buildQuery(c, f);
	const r = await c.env.DB.prepare(`SELECT MAX(s.position_no) AS m
       FROM spt_report_row_snapshot s
      WHERE s.position_no <= ?
        AND s.report_id IN (SELECT r.id ${SQL_BASE} WHERE ${where})`).bind(100, ...binds).first();
	const max = Number(r?.m ?? 0);
	return Math.max(20, Number.isFinite(max) ? max : 0);
}
/** 表头：报告ID、患者姓名、就诊卡号、报告日期、N 行编号、阳性对照、阴性对照、医生姓名、护士姓名 */
function buildHeader(posCount = 20) {
	const h = [
		"报告ID",
		"医院",
		"患者姓名",
		"性别",
		"出生日期",
		"年龄",
		"就诊卡号",
		"联系人",
		"联系电话",
		"报告日期",
		"状态",
		"模版名称",
		"模版是否已删除"
	];
	for (let i = 1; i <= posCount; i++) h.push(`${i}-过敏原`, `${i}-阳性/面积`, `${i}-阴性/面积`);
	h.push("阳性对照-过敏原", "阳性对照-阳性/面积", "阳性对照-阴性/面积");
	h.push("阴性对照-过敏原", "阴性对照-阳性/面积", "阴性对照-阴性/面积");
	h.push("症状", "备注", "医生姓名", "护士姓名", "照片数量", "创建时间", "提交时间");
	return h;
}
async function buildRow(c, r, posCount = 20) {
	const rowsRes = await c.env.DB.prepare(`SELECT position_no, allergen_name, positive_area, negative_area FROM spt_report_row_snapshot
     WHERE report_id = ? AND hospital_id = ? ORDER BY position_no`).bind(r.id, r.hospital_id).all();
	const map = /* @__PURE__ */ new Map();
	for (const x of rowsRes.results ?? []) map.set(x.position_no, x);
	const photo = await c.env.DB.prepare(`SELECT COUNT(*) AS c FROM spt_photo WHERE report_id = ? AND hospital_id = ?`).bind(r.id, r.hospital_id).first();
	const out = [
		r.id,
		r.hospital_name ?? "",
		r.patient_name,
		r.gender === "M" ? "男" : r.gender === "F" ? "女" : "未知",
		r.birth_date === "0001-01-01" ? "" : r.birth_date,
		patientAge(r),
		r.visit_card_no,
		r.contact_person ?? "",
		r.contact_phone ?? "",
		r.report_date,
		r.status === "DRAFT" ? "草稿" : r.status === "SUBMITTED" ? "已提交" : "已归档",
		r.template_name_snapshot ?? "",
		r.template_id ? r.template_deleted ? "是" : "否" : "—"
	];
	for (let i = 1; i <= posCount; i++) {
		const x = map.get(i);
		out.push(x?.allergen_name ?? "", x?.positive_area ?? "", x?.negative_area ?? "");
	}
	for (const pos of [101, 102]) {
		const x = map.get(pos);
		out.push(x?.allergen_name ?? "", x?.positive_area ?? "", x?.negative_area ?? "");
	}
	out.push(r.symptoms ?? "", r.notes ?? "", r.doctor_name ?? "", r.operator_name ?? "", photo?.c ?? 0, r.created_at, r.submitted_at ?? "");
	return out;
}
exportsRoute.post("/preview", async (c) => {
	const f = await c.req.json() ?? {};
	const total = await countRows(c, f);
	const rows = await fetchRows(c, f, 20, 0);
	return c.json({
		total,
		sample: rows.map((r) => ({
			id: r.id,
			hospital_name: r.hospital_name,
			patient_name: r.patient_name,
			visit_card_no: r.visit_card_no,
			report_date: r.report_date,
			status: r.status,
			doctor_name: r.doctor_name,
			operator_name: r.operator_name
		}))
	});
});
exportsRoute.post("/download", async (c) => {
	const f = await c.req.json() ?? {};
	const format = f.format === "csv" ? "csv" : f.format === "pdf" ? "pdf" : "xlsx";
	const total = await countRows(c, f);
	if (total > 2e3) return c.json({
		error: "TOO_LARGE",
		message: `共 ${total} 条，请使用异步全量导出`,
		total
	}, 413);
	const rows = await fetchRows(c, f, 2e3, 0);
	const posCount = await resolveExportPosCount(c, f);
	const header = buildHeader(posCount);
	const data = [];
	for (const r of rows) data.push(await buildRow(c, r, posCount));
	await audit(c, "EXPORT_REPORTS", {
		resource_type: "spt_report",
		after: {
			format,
			count: data.length,
			pos_count: posCount,
			filter: f
		}
	});
	const stamp = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
	if (format === "csv") return new Response(toCsv(header, data), { headers: {
		"Content-Type": "text/csv; charset=utf-8",
		"Content-Disposition": `attachment; filename="spt_reports_${stamp}.csv"`
	} });
	if (format === "pdf") return new Response(renderPrintableHtml(header, data, stamp), { headers: { "Content-Type": "text/html; charset=utf-8" } });
	return new Response(buildWorkbook([{
		name: "皮肤点刺报告单",
		header,
		rows: data
	}]), { headers: {
		"Content-Type": "application/vnd.ms-excel; charset=utf-8",
		"Content-Disposition": `attachment; filename="spt_reports_${stamp}.xls"`
	} });
});
function renderPrintableHtml(header, rows, stamp) {
	return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>皮肤点刺实验报告单导出 ${stamp}</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;font-size:10px;padding:12px}
 h1{font-size:16px} table{border-collapse:collapse;width:100%}
 th,td{border:1px solid #999;padding:3px 4px;white-space:nowrap}
 th{background:#1f6feb;color:#fff}
 @media print{@page{size:A3 landscape;margin:8mm}}
</style></head><body>
<h1>过敏原皮肤点刺实验报告单导出（${rows.length} 条） — ${stamp}</h1>
<p style="color:#666">提示：使用浏览器"打印 → 另存为 PDF"即可生成 PDF 文件。</p>
<table><thead><tr>${header.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
<tbody>${rows.map((r) => `<tr>${r.map((v) => `<td>${String(v ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>
<script>window.onload=()=>setTimeout(()=>window.print(),400)<\/script>
</body></html>`;
}
exportsRoute.post("/jobs", async (c) => {
	const f = await c.req.json() ?? {};
	const scope = f.scope === "FULL" ? "FULL" : "FILTERED";
	const format = f.format === "csv" ? "csv" : "xlsx";
	const total = await countRows(c, f);
	const id = uuid();
	const hid = c.var.user.role === "PLATFORM_ADMIN" ? f.hospital_id ?? null : c.var.user.hospital_id;
	const posCount = await resolveExportPosCount(c, f);
	await c.env.DB.prepare(`INSERT INTO export_job (id, hospital_id, actor_id, scope, format, filter_json, status, total)
     VALUES (?,?,?,?,?,?,'PENDING',?)`).bind(id, hid, c.var.user.id, scope, format, JSON.stringify({
		...f,
		__pos_count: posCount
	}), total).run();
	await audit(c, "CREATE_EXPORT_JOB", {
		resource_type: "export_job",
		resource_id: id,
		after: {
			scope,
			format,
			total,
			pos_count: posCount
		}
	});
	return c.json({
		id,
		total,
		status: "PENDING"
	});
});
/** 分片处理：前端轮询调用直至 DONE（规避 Workers CPU 时限） */
exportsRoute.post("/jobs/:id/process", async (c) => {
	const id = c.req.param("id");
	const job = await c.env.DB.prepare(`SELECT * FROM export_job WHERE id = ? AND actor_id = ?`).bind(id, c.var.user.id).first();
	if (!job) return c.json({ error: "NOT_FOUND" }, 404);
	if (job.status === "DONE") return c.json({
		status: "DONE",
		progress: 100,
		download_url: `/api/exports/jobs/${id}/download`
	});
	const f = JSON.parse(job.filter_json || "{}");
	const posCount = Number(f.__pos_count) >= 20 ? Number(f.__pos_count) : 20;
	const CHUNK = 200;
	const rows = await fetchRows(c, f, CHUNK, job.processed);
	const header = buildHeader(posCount);
	const data = [];
	for (const r of rows) data.push(await buildRow(c, r, posCount));
	await c.env.DB.prepare(`INSERT INTO export_chunk (id, job_id, seq, payload) VALUES (?,?,?,?)`).bind(uuid(), id, job.processed, JSON.stringify(data)).run();
	const processed = job.processed + rows.length;
	const done = rows.length < CHUNK || processed >= job.total;
	const progress = job.total ? Math.min(100, Math.round(processed / job.total * 100)) : 100;
	if (done) {
		const chunks = await c.env.DB.prepare(`SELECT payload FROM export_chunk WHERE job_id = ? ORDER BY seq`).bind(id).all();
		const all = [];
		for (const ch of chunks.results ?? []) all.push(...JSON.parse(ch.payload));
		const content = job.format === "csv" ? toCsv(header, all) : buildWorkbook([{
			name: "皮肤点刺报告单",
			header,
			rows: all
		}]);
		const bytes = new TextEncoder().encode(content);
		const ct = job.format === "csv" ? "text/csv" : "application/vnd.ms-excel";
		const saved = await putFile(c.env, "exports", job.hospital_id ?? "PLATFORM", bytes, ct);
		await c.env.DB.prepare(`UPDATE export_job SET status='DONE', progress=100, processed=?, download_key=?, updated_at=datetime('now') WHERE id=?`).bind(processed, saved.key, id).run();
		await c.env.DB.prepare(`DELETE FROM export_chunk WHERE job_id = ?`).bind(id).run();
		await audit(c, "FINISH_EXPORT_JOB", {
			resource_type: "export_job",
			resource_id: id,
			after: { count: all.length }
		});
		return c.json({
			status: "DONE",
			progress: 100,
			processed,
			total: job.total,
			download_url: `/api/exports/jobs/${id}/download`
		});
	}
	await c.env.DB.prepare(`UPDATE export_job SET status='RUNNING', progress=?, processed=?, updated_at=datetime('now') WHERE id=?`).bind(progress, processed, id).run();
	return c.json({
		status: "RUNNING",
		progress,
		processed,
		total: job.total
	});
});
exportsRoute.get("/jobs", async (c) => {
	const r = await c.env.DB.prepare(`SELECT id, scope, format, status, progress, total, processed, created_at FROM export_job
     WHERE actor_id = ? ORDER BY created_at DESC LIMIT 20`).bind(c.var.user.id).all();
	return c.json({ data: r.results ?? [] });
});
exportsRoute.get("/jobs/:id/download", async (c) => {
	const id = c.req.param("id");
	const job = await c.env.DB.prepare(`SELECT * FROM export_job WHERE id = ? AND actor_id = ?`).bind(id, c.var.user.id).first();
	if (!job || job.status !== "DONE" || !job.download_key) return c.json({ error: "NOT_READY" }, 404);
	const { getFile } = await Promise.resolve().then(() => storage_exports);
	const file = await getFile(c.env, job.download_key);
	if (!file) return c.json({ error: "NOT_FOUND" }, 404);
	const ext = job.format === "csv" ? "csv" : "xls";
	await audit(c, "DOWNLOAD_EXPORT", {
		resource_type: "export_job",
		resource_id: id
	});
	return new Response(file.body, { headers: {
		"Content-Type": file.contentType,
		"Content-Disposition": `attachment; filename="spt_full_export_${id.slice(0, 8)}.${ext}"`
	} });
});
var ALL_SEEDS = [
	{
		kind: "NORMAL",
		items: [
			{
				group: "吸入性 · 尘螨与室内",
				name: "户尘螨",
				keywords: "hcm dust mite",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "粉尘螨",
				keywords: "fcm dust mite",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "屋尘",
				keywords: "wc house dust",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "蟑螂",
				keywords: "zl cockroach",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "猫毛皮屑",
				keywords: "mmpx cat",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "狗毛皮屑",
				keywords: "gmpx dog",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "霉菌混合",
				keywords: "mjhh mould",
				common: true
			},
			{
				group: "吸入性 · 尘螨与室内",
				name: "尘土",
				keywords: "ct"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "艾蒿",
				keywords: "ah mugwort",
				common: true
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "葵花粉",
				keywords: "khf sunflower",
				common: true
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "梧桐",
				keywords: "wt plane",
				common: true
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "柳树",
				keywords: "ls willow"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "杨树",
				keywords: "ys poplar"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "白桦",
				keywords: "bh birch"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "榆树",
				keywords: "yus elm"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "松树",
				keywords: "ss pine"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "豚草",
				keywords: "tc ragweed"
			},
			{
				group: "吸入性 · 花粉与树木",
				name: "狗牙根草",
				keywords: "gygc bermuda"
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "牛奶",
				keywords: "nn milk",
				common: true
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "鸡蛋白",
				keywords: "jdb egg white",
				common: true
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "鸡蛋黄",
				keywords: "jdh egg yolk",
				common: true
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "大豆",
				keywords: "dd soy",
				common: true
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "小麦",
				keywords: "xm wheat",
				common: true
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "玉米",
				keywords: "ym corn"
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "大米",
				keywords: "dm rice"
			},
			{
				group: "食物性 · 蛋奶与谷物",
				name: "荞麦",
				keywords: "qm buckwheat"
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "虾",
				keywords: "xia shrimp",
				common: true
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "蟹",
				keywords: "xie crab",
				common: true
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "鱼",
				keywords: "yu fish",
				common: true
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "贝类",
				keywords: "bl shellfish"
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "牛肉",
				keywords: "nr beef"
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "羊肉",
				keywords: "yr lamb mutton"
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "猪肉",
				keywords: "zr pork"
			},
			{
				group: "食物性 · 肉蛋水产",
				name: "鸡肉",
				keywords: "jr chicken"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "花生",
				keywords: "hs peanut",
				common: true
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "腰果",
				keywords: "yg cashew"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "核桃",
				keywords: "ht walnut"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "芝麻",
				keywords: "zm sesame"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "杏仁",
				keywords: "xr almond"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "榛子",
				keywords: "zz hazelnut"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "芒果",
				keywords: "mg mango",
				common: true
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "桃",
				keywords: "tao peach"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "草莓",
				keywords: "cm strawberry"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "菠萝",
				keywords: "blo pineapple"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "番茄",
				keywords: "fq tomato"
			},
			{
				group: "食物性 · 坚果与果蔬",
				name: "芹菜",
				keywords: "qc celery"
			},
			{
				group: "其他",
				name: "乳胶",
				keywords: "rj latex"
			},
			{
				group: "其他",
				name: "青霉素",
				keywords: "qms penicillin"
			},
			{
				group: "其他",
				name: "蜂毒",
				keywords: "fd bee venom"
			}
		]
	},
	{
		kind: "POSITIVE_CTRL",
		items: [{
			group: "阳性对照常用",
			name: "组胺",
			keywords: "za histamine"
		}, {
			group: "阳性对照常用",
			name: "磷酸组胺",
			keywords: "lsza histamine phosphate"
		}]
	},
	{
		kind: "NEGATIVE_CTRL",
		items: [{
			group: "阴性对照常用",
			name: "生理盐水",
			keywords: "slys saline"
		}, {
			group: "阴性对照常用",
			name: "甘油缓冲液",
			keywords: "gyhcy glycerin"
		}]
	}
];
//#endregion
//#region src/routes/allergens.ts
/**
* 过敏原候选库 API
*
* 定位：候选库是**输入建议**，不是数据约束。
*  - 医生始终可以在模版/报告单里手输库外名称，不会被拦截；
*  - 报告单已把过敏原名称逐行冗余快照入库（spt_report_row_snapshot），
*    因此改动候选库**绝不影响任何历史报告单**。
*
* 隔离：全部经 tenantDb 访问，SQL 强制注入 hospital_id 条件（硬约束 6.2）。
*
* 初始化策略：**懒初始化**。医院首次调用 GET / 时，若本院一条记录都没有，
* 自动写入内置种子库（allergen-seed.ts）。这样新医院不需要额外跑脚本，
* 也避免在迁移里为未知的医院列表预填数据。
*/
var allergens = new Hono();
allergens.use("*", authGuard, clinicalOnly, tenantGuard);
var KINDS = [
	"NORMAL",
	"POSITIVE_CTRL",
	"NEGATIVE_CTRL"
];
var MAX_ITEMS = 1e3;
function normKind(v) {
	return KINDS.includes(v) ? v : "NORMAL";
}
/** 名称清洗：去首尾空格、压缩内部空白；分组名同理 */
function clean(v, max = 60) {
	return String(v ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}
/**
* 写入种子库。
*
* sort_no 采用**分组优先（group-major）**：sort = 分组序号 * 1000 + 组内序号。
* 这样排序既决定组内顺序，也决定**分组本身的出现顺序**（列表按 sort_no 遍历，
* 分组按首次出现排序）。若改用「常用项优先」的编号，常用项分散在各组，
* 会把分组顺序打乱（例如坚果组因含花生被提到肉蛋水产之前）。
* 「填入常见」的顺序因此也变成按分组归拢，临床更易核对。
*/
async function seedHospital(c, tenant) {
	const db = tenantDb(c.env.DB, tenant);
	const stmts = [];
	for (const { kind, items } of ALL_SEEDS) {
		const groupOrder = [];
		for (const it of items) if (!groupOrder.includes(it.group)) groupOrder.push(it.group);
		items.forEach((it, i) => {
			const sort = groupOrder.indexOf(it.group) * 1e3 + i;
			stmts.push(c.env.DB.prepare(`INSERT OR IGNORE INTO allergen_catalog
             (id, hospital_id, kind, group_name, name, keywords, is_common, is_active, sort_no, created_by)
           VALUES (?,?,?,?,?,?,?,1,?,NULL)`).bind(uuid(), tenant, kind, it.group, it.name, it.keywords ?? null, it.common ? 1 : 0, sort));
		});
	}
	if (stmts.length) await c.env.DB.batch(stmts);
	return db;
}
/** 确保本院已有候选库；返回是否刚刚初始化 */
async function ensureSeeded(c) {
	if (await tenantDb(c.env.DB, c.var.tenant).count("allergen_catalog") > 0) return false;
	await seedHospital(c, c.var.tenant);
	return true;
}
/** 按 group_name 聚合成下拉需要的 { label, items } 结构 */
function groupRows(rows) {
	const out = [];
	const idx = /* @__PURE__ */ new Map();
	for (const r of rows) {
		const g = r.group_name || "未分组";
		if (!idx.has(g)) {
			idx.set(g, out.length);
			out.push({
				label: g,
				items: []
			});
		}
		out[idx.get(g)].items.push({
			value: r.name,
			keywords: r.keywords || ""
		});
	}
	return out;
}
/**
* GET /api/allergens
* 供**下拉候选**使用的精简结构（前端 allergens.js 消费）。
* 只返回 is_active = 1。
*/
allergens.get("/", async (c) => {
	const seeded = await ensureSeeded(c);
	const rows = await tenantDb(c.env.DB, c.var.tenant).all("allergen_catalog", {
		where: "is_active = 1",
		orderBy: "sort_no ASC, name ASC"
	});
	const pick = (k) => rows.filter((r) => r.kind === k);
	const common = pick("NORMAL").filter((r) => r.is_common).map((r) => r.name);
	return c.json({
		seeded,
		total: rows.length,
		GROUPS: groupRows(pick("NORMAL")),
		COMMON: common,
		CONTROL_POSITIVE: groupRows(pick("POSITIVE_CTRL")),
		CONTROL_NEGATIVE: groupRows(pick("NEGATIVE_CTRL"))
	});
});
/**
* GET /api/allergens/manage?kind=&kw=&include_inactive=1
* 供**管理界面**使用的完整结构（含 id / is_active / sort_no / 停用项）。
*/
allergens.get("/manage", async (c) => {
	const seeded = await ensureSeeded(c);
	const db = tenantDb(c.env.DB, c.var.tenant);
	const kind = normKind(c.req.query("kind") || "NORMAL");
	const conds = ["kind = ?"];
	const binds = [kind];
	if (c.req.query("include_inactive") !== "1") conds.push("is_active = 1");
	const kw = clean(c.req.query("kw"), 40);
	if (kw) {
		conds.push("(name LIKE ? OR keywords LIKE ? OR group_name LIKE ?)");
		binds.push(`%${kw}%`, `%${kw}%`, `%${kw}%`);
	}
	const rows = await db.all("allergen_catalog", {
		where: conds.join(" AND "),
		binds,
		orderBy: "sort_no ASC, name ASC"
	});
	const groups = [];
	const idx = /* @__PURE__ */ new Map();
	for (const r of rows) {
		const g = r.group_name || "未分组";
		if (!idx.has(g)) {
			idx.set(g, groups.length);
			groups.push({
				label: g,
				items: []
			});
		}
		groups[idx.get(g)].items.push(r);
	}
	const allGroups = await db.all("allergen_catalog", {
		select: "DISTINCT group_name AS g",
		where: "kind = ?",
		binds: [kind],
		orderBy: "group_name ASC"
	});
	const stat = await c.env.DB.prepare(`SELECT COUNT(*) AS total,
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN is_common = 1 AND is_active = 1 THEN 1 ELSE 0 END) AS common
       FROM allergen_catalog WHERE hospital_id = ? AND kind = ?`).bind(c.var.tenant, kind).first();
	return c.json({
		seeded,
		kind,
		groups,
		all_groups: allGroups.map((r) => r.g),
		stat: {
			total: stat?.total ?? 0,
			active: stat?.active ?? 0,
			common: stat?.common ?? 0
		}
	});
});
/** POST /api/allergens —— 新增一项 */
allergens.post("/", async (c) => {
	const b = await c.req.json();
	const name = clean(b.name);
	if (!name) return c.json({
		error: "BAD_REQUEST",
		message: "过敏原名称必填"
	}, 400);
	const kind = normKind(b.kind);
	const db = tenantDb(c.env.DB, c.var.tenant);
	if (await db.count("allergen_catalog") >= MAX_ITEMS) return c.json({
		error: "LIMIT",
		message: `候选库最多 ${MAX_ITEMS} 项，请先清理停用项`
	}, 400);
	const dup = await db.first("allergen_catalog", {
		where: "kind = ? AND name = ?",
		binds: [kind, name]
	});
	if (dup) {
		if (dup.is_active) return c.json({
			error: "DUPLICATE",
			message: `「${name}」已存在于分组「${dup.group_name}」`
		}, 409);
		await db.update("allergen_catalog", dup.id, {
			is_active: 1,
			group_name: clean(b.group_name) || dup.group_name,
			keywords: clean(b.keywords, 120) || dup.keywords
		});
		await audit(c, "REACTIVATE_ALLERGEN", {
			resource_type: "allergen_catalog",
			resource_id: dup.id,
			after: { name }
		});
		return c.json({
			id: dup.id,
			reactivated: true
		});
	}
	const group = clean(b.group_name) || "未分组";
	const sums = await c.env.DB.prepare(`SELECT MAX(sort_no) AS gmax,
            (SELECT MAX(sort_no) FROM allergen_catalog WHERE hospital_id = ? AND kind = ?) AS amax
       FROM allergen_catalog
      WHERE hospital_id = ? AND kind = ? AND group_name = ?`).bind(c.var.tenant, kind, c.var.tenant, kind, group).first();
	const sortNo = sums?.gmax != null ? Number(sums.gmax) + 1 : (Math.floor((Number(sums?.amax) || 0) / 1e3) + 1) * 1e3;
	const id = uuid();
	await db.insert("allergen_catalog", {
		id,
		kind,
		group_name: group,
		name,
		keywords: clean(b.keywords, 120) || null,
		is_common: b.is_common ? 1 : 0,
		is_active: 1,
		sort_no: sortNo,
		created_by: c.var.user.id
	});
	await audit(c, "CREATE_ALLERGEN", {
		resource_type: "allergen_catalog",
		resource_id: id,
		after: {
			kind,
			name
		}
	});
	return c.json({ id });
});
/** PUT /api/allergens/:id —— 修改名称 / 分组 / 关键词 / 常用标记 / 启停 */
allergens.put("/:id", async (c) => {
	const id = c.req.param("id");
	const b = await c.req.json();
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("allergen_catalog", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "候选项不存在或不属于本医院"
	}, 404);
	const patch = {};
	if (b.name != null) {
		const name = clean(b.name);
		if (!name) return c.json({
			error: "BAD_REQUEST",
			message: "名称不能为空"
		}, 400);
		if (name !== before.name) {
			if (await db.first("allergen_catalog", {
				where: "kind = ? AND name = ? AND id <> ?",
				binds: [
					before.kind,
					name,
					id
				]
			})) return c.json({
				error: "DUPLICATE",
				message: `「${name}」已存在`
			}, 409);
		}
		patch.name = name;
	}
	if (b.group_name != null) patch.group_name = clean(b.group_name) || "未分组";
	if (b.keywords != null) patch.keywords = clean(b.keywords, 120) || null;
	if (b.is_common != null) patch.is_common = b.is_common ? 1 : 0;
	if (b.is_active != null) patch.is_active = b.is_active ? 1 : 0;
	if (b.sort_no != null) patch.sort_no = Number(b.sort_no) || 0;
	const n = await db.update("allergen_catalog", id, patch);
	await audit(c, "UPDATE_ALLERGEN", {
		resource_type: "allergen_catalog",
		resource_id: id,
		before,
		after: patch
	});
	return c.json({
		ok: true,
		changed: n
	});
});
/**
* DELETE /api/allergens/:id
* 默认**停用**（is_active = 0）——候选库是纯建议数据，但停用比删除更安全：
* 历史模版里已写入的同名项仍能被搜索到。?hard=1 时才真正物理删除。
*/
allergens.delete("/:id", async (c) => {
	const id = c.req.param("id");
	const hard = c.req.query("hard") === "1";
	const db = tenantDb(c.env.DB, c.var.tenant);
	const before = await db.first("allergen_catalog", {
		where: "id = ?",
		binds: [id]
	});
	if (!before) return c.json({
		error: "NOT_FOUND",
		message: "候选项不存在或不属于本医院"
	}, 404);
	if (hard) await db.rawRun(`DELETE FROM allergen_catalog WHERE id = ? AND hospital_id = ?`, id, c.var.tenant);
	else await db.update("allergen_catalog", id, { is_active: 0 });
	await audit(c, hard ? "DELETE_ALLERGEN" : "DEACTIVATE_ALLERGEN", {
		resource_type: "allergen_catalog",
		resource_id: id,
		before: {
			name: before.name,
			kind: before.kind
		}
	});
	return c.json({
		ok: true,
		hard
	});
});
/** POST /api/allergens/reorder —— 批量排序（拖拽/上下移动后提交 id 数组） */
allergens.post("/reorder", async (c) => {
	const b = await c.req.json();
	const ids = Array.isArray(b.ids) ? b.ids.filter((x) => typeof x === "string") : [];
	if (!ids.length) return c.json({
		error: "BAD_REQUEST",
		message: "ids 不能为空"
	}, 400);
	const base = Number(b.base_sort) || 0;
	const stmts = ids.map((id, i) => c.env.DB.prepare(`UPDATE allergen_catalog SET sort_no = ?, updated_at = datetime('now')
        WHERE id = ? AND hospital_id = ?`).bind(base + i, id, c.var.tenant));
	await c.env.DB.batch(stmts);
	await audit(c, "REORDER_ALLERGEN", {
		resource_type: "allergen_catalog",
		after: { count: ids.length }
	});
	return c.json({
		ok: true,
		count: ids.length
	});
});
/** POST /api/allergens/group/rename —— 分组重命名（批量改本院同 kind 下该分组所有项） */
allergens.post("/group/rename", async (c) => {
	const b = await c.req.json();
	const kind = normKind(b.kind);
	const from = clean(b.from);
	const to = clean(b.to) || "未分组";
	if (!from) return c.json({
		error: "BAD_REQUEST",
		message: "原分组名必填"
	}, 400);
	const r = await tenantDb(c.env.DB, c.var.tenant).rawRun(`UPDATE allergen_catalog SET group_name = ?, updated_at = datetime('now')
      WHERE hospital_id = ? AND kind = ? AND group_name = ?`, to, c.var.tenant, kind, from);
	await audit(c, "RENAME_ALLERGEN_GROUP", {
		resource_type: "allergen_catalog",
		before: { from },
		after: { to }
	});
	return c.json({
		ok: true,
		changed: r.meta?.changes ?? 0
	});
});
/**
* POST /api/allergens/reset
* 「恢复内置库」：把内置种子重新补齐。
*  - mode = 'merge'（默认）：只补缺失项，保留自建项与现有改动
*  - mode = 'replace'：清空本院候选库后重建为内置库（自建项会丢失，前端需二次确认）
* 无论哪种模式，都**不影响任何模版与报告单**。
*/
allergens.post("/reset", async (c) => {
	const mode = (await c.req.json().catch(() => ({})))?.mode === "replace" ? "replace" : "merge";
	const db = tenantDb(c.env.DB, c.var.tenant);
	if (mode === "replace") await db.rawRun(`DELETE FROM allergen_catalog WHERE hospital_id = ?`, c.var.tenant);
	await seedHospital(c, c.var.tenant);
	const total = await db.count("allergen_catalog", { where: "is_active = 1" });
	await audit(c, "RESET_ALLERGEN_CATALOG", {
		resource_type: "allergen_catalog",
		after: {
			mode,
			total
		}
	});
	return c.json({
		ok: true,
		mode,
		total
	});
});
//#endregion
//#region src/lib/wheal.ts
/** 失败原因 → 可执行的中文处置建议。与 capture.ts 的 ADVICE 同一套做法 */
var WHEAL_ADVICE = {
	NOT_CONFIGURED: "服务端尚未配置风团测量服务，请联系管理员设置 WHEAL_API_BASE / WHEAL_API_KEY。当前请人工测量并填写。",
	HTTP_ERROR: "测量服务返回错误，请稍后重试；若持续失败请联系管理员检查院内 GPU 服务状态。",
	TIMEOUT: "测量服务响应超时。请检查院内网络与 GPU 服务负载，本次请人工填写。",
	NETWORK: "无法连接测量服务（院内 GPU 服务器可能未开机或网络不通），请人工填写。",
	BAD_RESPONSE: "测量服务返回内容异常，请联系管理员查看服务日志，本次请人工填写。",
	NOT_CALIBRATED: "尚未完成像素-毫米标定，无法给出 mm 尺寸。请先在标定纸上完成标定，或人工填写。"
};
function normBase(base) {
	return base.replace(/\/+$/, "");
}
/**
* 探活。前端据此决定是否显示"自动测量"入口。
*
* 超时设得很短（6s）：探活是**前置**动作，医生在等界面响应。
* 真正的测量请求由浏览器直连，超时由前端自己控制，与此无关。
*/
async function checkWhealHealth(env) {
	const base = env.WHEAL_API_BASE;
	if (!base) return {
		available: false,
		paths_available: [],
		sam_loaded: false,
		error_code: "NOT_CONFIGURED",
		error_detail: "服务端未配置 WHEAL_API_BASE"
	};
	const t0 = Date.now();
	try {
		const res = await fetch(`${normBase(base)}/health`, {
			method: "GET",
			headers: { "ngrok-skip-browser-warning": "1" },
			signal: AbortSignal.timeout(6e3)
		});
		const elapsed = Date.now() - t0;
		if (!res.ok) return {
			available: false,
			paths_available: [],
			sam_loaded: false,
			error_code: "HTTP_ERROR",
			error_detail: `HTTP ${res.status}`,
			elapsed_ms: elapsed
		};
		const j = await res.json();
		const paths = Array.isArray(j?.paths_available) ? j.paths_available : [];
		if (!j?.ok || !paths.length) return {
			available: false,
			paths_available: paths,
			sam_loaded: false,
			error_code: "BAD_RESPONSE",
			error_detail: "响应缺少 ok / paths_available",
			elapsed_ms: elapsed
		};
		return {
			available: true,
			paths_available: paths,
			sam_loaded: !!j.sam_loaded,
			degraded_note: j.degraded_note ?? null,
			version: j.version,
			elapsed_ms: elapsed
		};
	} catch (e) {
		return {
			available: false,
			paths_available: [],
			sam_loaded: false,
			error_code: e?.name === "TimeoutError" || /timeout/i.test(String(e?.message)) ? "TIMEOUT" : "NETWORK",
			error_detail: String(e?.message ?? e),
			elapsed_ms: Date.now() - t0
		};
	}
}
var SEGMENT_METHODS = [
	"SAM",
	"RING",
	"ARBITRATED",
	"MANUAL"
];
var POINT_SOURCES = ["DOT_ARRAY", "CLICK"];
/**
* 阴性对照位点号，必须与 types.ts 的 NEGATIVE_CTRL_POS 一致。
*
* 为什么在这里重新定义而不从 types.ts 导入：本文件被 tests/sanitize_check.mjs
* 用 `node` 直接以类型剥离方式加载，此时 `from './types'` 这种无扩展名的
* **值**导入解析不了（vite 能解析，node 不能），整个测试文件会以
* ERR_MODULE_NOT_FOUND 崩掉 —— 报错信息与被测逻辑毫无关系，很难定位。
* 原先这里只有 `import type`（编译期擦除）所以不受影响。
*
* 重复定义的漂移风险由 tests/ctrl_baseline_check.mjs 里的一致性断言兜住：
* 它同时读两个文件，值不相等就失败。
*/
var NEGATIVE_CTRL_POS = 102;
/**
* 阳性对照（组胺）位点号，必须与 types.ts 的 POSITIVE_CTRL_POS 一致。
* 在此重新定义的原因与上方 NEGATIVE_CTRL_POS 完全相同（node 直接加载 .ts
* 时无法解析无扩展名的值导入），漂移风险同样由 tests/ctrl_baseline_check.mjs
* 的双文件一致性断言兜住。
*/
var POSITIVE_CTRL_POS = 101;
/**
* 清洗单条测量结果。
*
* ⚠️ 为什么必须校验：结果由**浏览器**回传（图像直连 GPU 服务器的必然后果），
* 也就是说这些数字经过了一个不可信的中间环节。若直接落库，
* 一个被篡改或出错的 d_mean_mm 会经由比值法污染整张报告的分级 ——
* 而且是静默的，医生看不出来。所以这里按生理范围硬校验。
*/
function sanitizeMeasurement(raw, uncalibrated = false) {
	const pos = Number(raw?.position_no);
	if (!Number.isInteger(pos) || pos < 1) return {
		ok: false,
		reason: `position_no 非法（${raw?.position_no}）`
	};
	const num = (v) => {
		if (v == null || v === "") return null;
		const n = Number(v);
		return Number.isFinite(n) ? n : null;
	};
	const dMax = num(raw.d_max_mm);
	const dPerp = num(raw.d_perp_mm);
	let dMean = num(raw.d_mean_mm);
	if (dMean == null && dMax != null && dPerp != null) dMean = (dMax + dPerp) / 2;
	if (dMean == null) return {
		ok: false,
		reason: `位点 ${pos} 缺少平均径 d_mean_mm`
	};
	const isNegCtrl = pos === NEGATIVE_CTRL_POS;
	const isPosCtrl = pos === POSITIVE_CTRL_POS;
	const isCtrlRuleInjected = (isNegCtrl || isPosCtrl) && !!raw.edited;
	const useUncalibrated = uncalibrated && !isCtrlRuleInjected;
	const lo = useUncalibrated ? 7 : 1;
	const hi = useUncalibrated ? 175 : 25;
	const unit = useUncalibrated ? "px" : "mm";
	const autoNeg = !!raw.auto_negative && dMean === 0 && !isNegCtrl && !isPosCtrl;
	if (isNegCtrl && dMean === 0) {} else if (autoNeg) {} else if (dMean < lo || dMean > hi) return {
		ok: false,
		reason: isNegCtrl ? `位点 ${pos} 平均径 ${dMean.toFixed(1)}${unit} 超出 ${lo}-${hi}${unit} 合理范围（阴性对照如无风团请用「确认无风团」记 0）` : isPosCtrl && dMean < lo ? `位点 ${pos} 平均径 ${dMean.toFixed(1)}${unit} 小于 ${lo}${unit}，组胺几乎未起风团，请确认对照是否有效` : `位点 ${pos} 平均径 ${dMean.toFixed(1)}${unit} 超出 ${lo}-${hi}${unit} 合理范围`
	};
	if (dMax != null && dPerp != null) {
		const expect = (dMax + dPerp) / 2;
		const tol = uncalibrated ? .05 * 7 : .05;
		if (Math.abs(expect - dMean) > tol) return {
			ok: false,
			reason: `位点 ${pos} 平均径 ${dMean} 与 (${dMax}+${dPerp})/2=${expect.toFixed(2)} 不符`
		};
		if (dPerp > dMax + tol) return {
			ok: false,
			reason: `位点 ${pos} 垂直径 ${dPerp} 大于最长径 ${dMax}，几何上不可能`
		};
	}
	const solidity = num(raw.solidity);
	if (solidity != null && (solidity <= 0 || solidity > 1.01)) return {
		ok: false,
		reason: `位点 ${pos} solidity ${solidity} 不在 (0,1] 区间`
	};
	const conf = num(raw.confidence);
	if (conf != null && (conf < 0 || conf > 1)) return {
		ok: false,
		reason: `位点 ${pos} confidence ${conf} 不在 [0,1] 区间`
	};
	const method = raw.segment_method == null ? null : String(raw.segment_method);
	if (method && !SEGMENT_METHODS.includes(method)) return {
		ok: false,
		reason: `位点 ${pos} segment_method ${method} 非法`
	};
	const psrc = raw.point_source == null ? null : String(raw.point_source);
	if (psrc && !POINT_SOURCES.includes(psrc)) return {
		ok: false,
		reason: `位点 ${pos} point_source ${psrc} 非法`
	};
	return {
		ok: true,
		value: {
			position_no: pos,
			d_max_mm: dMax,
			d_perp_mm: dPerp,
			d_mean_mm: Math.round(dMean * 1e3) / 1e3,
			area_mm2: num(raw.area_mm2),
			area_ellipse_mm2: num(raw.area_ellipse_mm2),
			solidity,
			segment_method: method,
			confidence: conf,
			sam_d_mean_mm: num(raw.sam_d_mean_mm),
			ring_d_mean_mm: num(raw.ring_d_mean_mm),
			click_x: num(raw.click_x),
			click_y: num(raw.click_y),
			point_source: psrc,
			has_pseudopod: !!raw.has_pseudopod,
			mask_key: raw.mask_key == null ? null : String(raw.mask_key).slice(0, 200),
			edited: !!raw.edited,
			neg_ctrl_assumed: !!raw.neg_ctrl_assumed && isNegCtrl && dMean === 0,
			auto_negative: autoNeg
		}
	};
}
//#endregion
//#region src/lib/grading.ts
var GRADES = [
	"-",
	"+",
	"++",
	"+++",
	"++++"
];
/**
* 国内 II 类 —— 默认标准。
*
* 选它作默认的核心理由：**区间连续、无空隙、无需人为编造容差**。
* 对比北京协和标准，其 '+++' 原文为「与阳性对照风团相同大小」，
* "相同"没有容差定义 → ratio=1.05 判 +++ 还是 ++++ 无法确定，
* 必须由实现者编一个数字，这是不应该由代码替临床决定的事。
*
* 原文 → 区间：
*   -     与阴性对照相同                → D < 3mm（不看 ratio）
*   +     >阴性对照 但 <1/2 阳性对照     → ratio < 0.5
*   ++    ≥1/2 但 <1倍 阳性对照         → 0.5 ≤ ratio < 1.0
*   +++   1 ~ <2倍 阳性对照             → 1.0 ≤ ratio < 2.0
*   ++++  ≥2倍 阳性对照 或 伴有伪足      → ratio ≥ 2.0
*/
var CN_CLASS_II = {
	code: "CN_CLASS_II",
	name: "国内 II 类标准",
	criteria_type: "RATIO",
	min_positive_mm: 3,
	rules: [
		{
			grade: "+",
			min: 0,
			max: .5
		},
		{
			grade: "++",
			min: .5,
			max: 1
		},
		{
			grade: "+++",
			min: 1,
			max: 2
		},
		{
			grade: "++++",
			min: 2,
			max: null
		}
	],
	ctrl_pos_min_mm: 3,
	ctrl_neg_max_mm: 3
};
var BUILTIN_STANDARDS = {
	CN_CLASS_II,
	PUMCH: {
		code: "PUMCH",
		name: "北京协和医院标准",
		criteria_type: "RATIO",
		min_positive_mm: 3,
		rules: [
			{
				grade: "+",
				min: .333,
				max: .667
			},
			{
				grade: "++",
				min: .667,
				max: .95
			},
			{
				grade: "+++",
				min: .95,
				max: 1.15
			},
			{
				grade: "++++",
				min: 1.15,
				max: null
			}
		],
		ctrl_pos_min_mm: 3,
		ctrl_neg_max_mm: 3
	},
	EAACI: {
		code: "EAACI",
		name: "欧洲标准（阳性/阴性二分）",
		criteria_type: "BINARY",
		min_positive_mm: 3,
		rules: [{
			grade: "+",
			min: 0,
			max: null
		}],
		ctrl_pos_min_mm: 3,
		ctrl_neg_max_mm: 3
	}
};
/**
* 对外显示用的取整：0.5mm 步进。
*
* 为什么不显示原始精度：1080p @7.3px/mm 下 3mm 风团仅 22px，
* 而照片证明风团边界是**高度渐变**的，真实不确定度约 ±0.5-1mm。
* 显示 "6.43mm" 会让医生认为系统在宣称一个它并不具备的精度，
* 这种"假精度"会直接损害对整个工具的信任。
* 数据库仍存原始值，不影响后续精度分析。
*/
function displayMm(mm) {
	if (mm == null || !Number.isFinite(mm) || mm <= 0) return "—";
	return (Math.round(mm * 2) / 2).toFixed(1);
}
/**
* 试验有效性互锁。
*
* 这不是"加固项"，是硬拦截：漏掉它，系统会在一次**无效试验**上
* 输出一整张看起来完全正常的分级报告 —— 这比不输出更危险，
* 因为错误不可见。
*
* 判据（A3 已确认）：
*   组胺   < 3mm → 阳性对照未起效（患者可能服过抗组胺药）→ 全部等级作废
*   生理盐水 > 3mm → 阴性对照异常（人工划痕症/皮肤高敏）→ 结果不可信
*/
function checkValidity(histamineDMm, salineDMm, std = CN_CLASS_II, uncalibrated = false) {
	const baseline = std.ctrl_pos_min_mm;
	if (histamineDMm == null || !Number.isFinite(histamineDMm)) return {
		validity: "PENDING",
		can_apply: false,
		message: "尚未测量阳性对照（组胺）。比值法分级必须先有组胺基准，请先测量组胺位点。",
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: null
	};
	if (uncalibrated) return {
		validity: "PENDING",
		can_apply: true,
		message: `未标定比例尺：分级按比值法计算（准确，不受比例尺影响），但 ${std.ctrl_pos_min_mm}mm 对照互锁无法自动判定。请医生目视确认：组胺已起风团、生理盐水未起风团，再写入报告单。`,
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: null
	};
	if (histamineDMm < std.ctrl_pos_min_mm) return {
		validity: "INVALID_POSITIVE_CTRL",
		can_apply: false,
		message: `阳性对照（组胺）风团仅 ${displayMm(histamineDMm)}mm，小于 ${std.ctrl_pos_min_mm}mm，说明对照未起效（常见原因：患者近期服用抗组胺药）。本次试验结果不可信，不能自动填表。`,
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: false
	};
	if (salineDMm == null || !Number.isFinite(salineDMm)) return {
		validity: "PENDING",
		can_apply: false,
		message: "尚未测量阴性对照（生理盐水）。请完成对照测量后再应用分级结果。",
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: true
	};
	if (salineDMm > std.ctrl_neg_max_mm) return {
		validity: "INVALID_NEGATIVE_CTRL",
		can_apply: false,
		message: `阴性对照（生理盐水）风团 ${displayMm(salineDMm)}mm，大于 ${std.ctrl_neg_max_mm}mm，提示皮肤高敏或人工划痕症。本次试验结果不可信，不能自动填表。`,
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: true
	};
	return {
		validity: "VALID",
		can_apply: true,
		message: "对照有效，分级结果可用。",
		ctrl_pos_baseline_mm: baseline,
		ctrl_pos_ok: true
	};
}
/**
* 计算单个位点的建议等级。
*
* ⚠️ 返回的是**建议**，不是结论。最终判读必须由医生确认后
* 才写入 positive_area / negative_area。
*/
function computeGrade(input, std = CN_CLASS_II) {
	const d = Number(input.d_mean_mm);
	const solidity = input.solidity;
	const pseudopod = solidity != null && Number.isFinite(solidity) && solidity < .88;
	const uncal = input.uncalibrated === true;
	if (input.assumed_negative === true && (!Number.isFinite(d) || d <= 0)) return {
		grade: "-",
		ratio: null,
		pseudopod_suspected: false,
		reason: "自动测量未检出风团，自动判阴（可手动修正）"
	};
	if (!Number.isFinite(d) || d <= 0) return {
		grade: null,
		ratio: null,
		pseudopod_suspected: false,
		reason: `尺寸缺失或非法（${input.d_mean_mm}），无法判定`
	};
	if (!uncal && d < std.min_positive_mm) return {
		grade: "-",
		ratio: null,
		pseudopod_suspected: false,
		reason: `最长径 ${displayMm(d)}mm < ${std.min_positive_mm}mm 阳性门槛，判阴性`
	};
	if (uncal && std.criteria_type !== "RATIO") return {
		grade: null,
		ratio: null,
		pseudopod_suspected: pseudopod,
		reason: `「${std.name}」按绝对毫米分档，未标定比例尺时无法判定。请先完成两点标定，或改用比值法标准（如国内 II 类）。`
	};
	if (std.criteria_type === "BINARY") return {
		grade: "+",
		ratio: null,
		pseudopod_suspected: pseudopod,
		reason: `最长径 ${displayMm(d)}mm ≥ ${std.min_positive_mm}mm，判阳性（该标准不分级）`
	};
	if (std.criteria_type === "ABSOLUTE") {
		const g = matchRule(d, std.rules);
		return {
			grade: g ?? "+",
			ratio: null,
			pseudopod_suspected: pseudopod,
			reason: `最长径 ${displayMm(d)}mm 按绝对分档判 ${g ?? "+"}（注意：绝对判据受放大率误差影响）`
		};
	}
	const h = input.histamine_d_mm;
	if (h == null || !Number.isFinite(h) || h <= 0) return {
		grade: null,
		ratio: null,
		pseudopod_suspected: pseudopod,
		reason: "尚未测量阳性对照（组胺），比值法缺少基准，无法判定"
	};
	const ratio = d / h;
	const g = matchRule(ratio, std.rules);
	const unit = "mm";
	const show = (v) => uncal ? "约" + v.toFixed(1) : displayMm(v);
	return {
		grade: g ?? "+",
		ratio,
		pseudopod_suspected: pseudopod,
		needs_size_confirm: uncal,
		reason: `最长径 ${show(d)}${unit} ÷ 组胺 ${show(h)}${unit} = ${ratio.toFixed(2)} → ${g ?? "+"}` + (uncal ? `（未标定比例尺：比值法不受影响，等级可靠；但无法验证是否满足 ${std.min_positive_mm}mm 阳性门槛，请医生确认风团确实达到该尺寸）` : "") + (pseudopod ? `；实心度 ${solidity.toFixed(2)} 偏低，疑似伪足，请医生确认是否升为 ++++` : "")
	};
}
/**
* 区间匹配，语义固定 [min, max)，max=null 为 +∞。
*
* ⚠️ 这里有一个必须显式处理的情况：**value 低于最低档的下界**。
* 协和标准最低档 '+' 的下界是 1/3，原文「<1/3 阳性对照」判阴性。
* 若只做「匹配不到就兜底成 '+'」，ratio=0.32 会被判成 '+' ——
* 把阴性判成阳性，是本引擎最不能犯的一类错。故低于最低下界一律 '-'。
*
* 另一种情况是 value 落在两档之间的**空隙**里（协和「相同大小」无容差
* 定义就可能产生空隙）。此时取"下界不超过 value 的最高一档"，
* 即保守地不越级升档，绝不凭空升到更高等级。
*/
function matchRule(value, rules) {
	if (!rules.length || !Number.isFinite(value)) return null;
	const sorted = [...rules].sort((a, b) => a.min - b.min);
	if (value < sorted[0].min) return "-";
	for (const r of sorted) {
		const okMin = value >= r.min;
		const okMax = r.max == null || value < r.max;
		if (okMin && okMax) return r.grade;
	}
	let best = null;
	for (const r of sorted) if (value >= r.min) best = r.grade;
	return best;
}
/**
* 生成报告单单元格文本，形如 `++ / 6.5mm`。
*
* 为什么不新增"分级"列：git 历史 9135e56 刚把报告单打印压缩到单页
* （患者信息压成单行 7 列、症状备注并列）。表格是左右分栏的，
* 加一个分级列实际是加两列，很可能直接挤破单页版式。
* 复用现有单元格既零打印影响，又与 positive_area 原本就存
* "√ / 5mm" 这类复合文本的惯例一致。结构化值另存新列，不丢数据。
*/
function cellText(grade, dMm) {
	if (grade === "-") return "—";
	const mm = displayMm(dMm);
	return mm === "—" ? grade : `${grade} / ${mm}mm`;
}
/**
* 把 grading_standard 表的一行解析为 GradingStandard。
* rules_json 损坏时回落到内置同名标准，再不行回落国内 II 类 ——
* 分级引擎不能因为一条配置坏了就整体不可用。
*/
function parseStandardRow(row) {
	const fallback = BUILTIN_STANDARDS[row?.code] ?? CN_CLASS_II;
	let rules = fallback.rules;
	try {
		const parsed = JSON.parse(String(row?.rules_json ?? "[]"));
		if (Array.isArray(parsed) && parsed.length) {
			rules = parsed.filter((r) => GRADES.includes(r?.grade)).map((r) => ({
				grade: r.grade,
				min: Number(r.min ?? 0),
				max: r.max == null ? null : Number(r.max)
			}));
			if (!rules.length) rules = fallback.rules;
		}
	} catch {
		rules = fallback.rules;
	}
	const ct = String(row?.criteria_type ?? fallback.criteria_type);
	return {
		code: String(row?.code ?? fallback.code),
		name: String(row?.name ?? fallback.name),
		criteria_type: [
			"RATIO",
			"ABSOLUTE",
			"BINARY"
		].includes(ct) ? ct : "RATIO",
		min_positive_mm: numOr(row?.min_positive_mm, fallback.min_positive_mm),
		rules,
		ctrl_pos_min_mm: numOr(row?.ctrl_pos_min_mm, fallback.ctrl_pos_min_mm),
		ctrl_neg_max_mm: numOr(row?.ctrl_neg_max_mm, fallback.ctrl_neg_max_mm)
	};
}
function numOr(v, d) {
	const n = Number(v);
	return Number.isFinite(n) && n > 0 ? n : d;
}
//#endregion
//#region src/routes/wheal.ts
/**
* 风团自动测量 API
* =============================================================
*
* 【本路由不接收图像】
*
* 图像由浏览器**直传**院内 GPU 服务器（4K 照片 10-20MB，云服务器上行仅
* 3-5Mbps，中转一张要 30-50 秒）。因此本路由只处理 KB 级的结构化数据：
*
*   GET  /config              下发连接信息 + 探活，前端据此决定是否显示入口
*   GET  /standards           可用分级标准
*   POST /sessions            建会话（记录标定与远端句柄）
*   PATCH /sessions/:id/calib 更新标定
*   POST /sessions/:id/measurements  回传测量结果 → 落库 + 算等级
*   GET  /sessions/:id        读会话与全部测量
*   POST /sessions/:id/apply  确认并写入报告单
*   POST /sessions/:id/abandon 放弃会话
*
* 【判读只在一处实现】
* 等级一律由 src/lib/grading.ts 计算，GPU 服务不返回 grade。
* 规则若两处实现必然漂移，而漂移后的错误分级是不可见的。
*/
var wheal = new Hono();
/** 未标定标记：calib_confidence 恰为 0（真实标定必 >0 或为 null） */
var UNCALIBRATED_CONF = 0;
/** 会话是否处于未标定（估算比例尺）模式 */
function isUncalibrated(session) {
	const p = Number(session?.px_per_mm);
	if (!Number.isFinite(p) || p <= 0) return true;
	const conf = session?.calib_confidence;
	if (conf == null) return false;
	return Number(conf) === UNCALIBRATED_CONF;
}
wheal.use("*", authGuard, clinicalOnly, tenantGuard);
/** 读取医院配置的分级标准；无配置回落内置。分级不能因缺一条配置而不可用 */
async function loadStandard(c, code) {
	const tenant = c.var.tenant;
	const wanted = code || null;
	try {
		const row = wanted ? await c.env.DB.prepare(`SELECT * FROM grading_standard
            WHERE code = ? AND is_active = 1 AND hospital_id IN (?, 'PLATFORM')
            ORDER BY CASE WHEN hospital_id = ? THEN 0 ELSE 1 END LIMIT 1`).bind(wanted, tenant, tenant).first() : await c.env.DB.prepare(`SELECT * FROM grading_standard
            WHERE is_default = 1 AND is_active = 1 AND hospital_id IN (?, 'PLATFORM')
            ORDER BY CASE WHEN hospital_id = ? THEN 0 ELSE 1 END LIMIT 1`).bind(tenant, tenant).first();
		if (row) return parseStandardRow(row);
	} catch (e) {
		console.error("loadStandard failed", e);
	}
	return BUILTIN_STANDARDS[wanted ?? "CN_CLASS_II"] ?? BUILTIN_STANDARDS["CN_CLASS_II"];
}
/** 取会话并做租户校验。跨医院访问一律当作不存在，不泄露存在性 */
async function getSession(c, id) {
	return await c.env.DB.prepare(`SELECT * FROM wheal_session WHERE id = ? AND hospital_id = ?`).bind(id, c.var.tenant).first();
}
async function listMeasurements(c, sessionId) {
	return (await c.env.DB.prepare(`SELECT * FROM wheal_measurement WHERE session_id = ? AND hospital_id = ? ORDER BY position_no`).bind(sessionId, c.var.tenant).all()).results ?? [];
}
/**
* 重算会话的对照值与有效性。
*
* 每次测量回传后都重跑：组胺可能被重测、修正，一旦它变了
* **所有**位点的等级都跟着变（比值法的基准就是它）。
*/
async function refreshValidity(c, session, std) {
	const rows = await listMeasurements(c, session.id);
	const find = (p) => rows.find((r) => r.position_no === p);
	const hist = find(101);
	const saline = find(102);
	const histMm = hist?.d_max_mm ?? hist?.d_mean_mm ?? null;
	const salineMm = saline?.d_max_mm ?? saline?.d_mean_mm ?? null;
	const uncal = isUncalibrated(session);
	const v = checkValidity(histMm, salineMm, std, uncal);
	await c.env.DB.prepare(`UPDATE wheal_session
       SET histamine_d_mm = ?, saline_d_mm = ?, validity = ?, validity_note = ?,
           updated_at = datetime('now')
     WHERE id = ? AND hospital_id = ?`).bind(histMm, salineMm, v.validity, v.message, session.id, c.var.tenant).run();
	return {
		validity: v,
		histMm,
		salineMm,
		rows,
		uncalibrated: uncal
	};
}
/** 给每条测量附上建议等级。等级是算出来的，不落 measurement 表（避免与基准脱钩） */
function gradeRows(rows, histMm, std, uncalibrated = false) {
	return rows.map((r) => {
		const isCtrl = r.position_no === 101 || r.position_no === 102;
		const g = isCtrl ? null : computeGrade({
			d_mean_mm: r.d_max_mm ?? r.d_mean_mm,
			histamine_d_mm: histMm,
			solidity: r.solidity,
			uncalibrated,
			assumed_negative: !!r.auto_negative
		}, std);
		const machineMeasured = !!r.segment_method && r.segment_method !== "MANUAL";
		const ctrlUnconfirmed = isCtrl && machineMeasured && !r.edited;
		return {
			...r,
			has_pseudopod: !!r.has_pseudopod,
			edited: !!r.edited,
			neg_ctrl_assumed: !!r.neg_ctrl_assumed,
			auto_negative: !!r.auto_negative,
			needs_review: (r.confidence ?? 1) < .8 || (g?.pseudopod_suspected ?? false) || ctrlUnconfirmed,
			ctrl_unconfirmed: ctrlUnconfirmed,
			grade_suggested: g?.grade ?? null,
			grade_ratio: g?.ratio ?? null,
			grade_reason: g?.reason ?? null,
			pseudopod_suspected: g?.pseudopod_suspected ?? false,
			needs_size_confirm: g?.needs_size_confirm ?? false,
			d_display: uncalibrated ? null : displayMm(r.d_max_mm ?? r.d_mean_mm),
			cell_text: g?.grade ? cellText(g.grade, uncalibrated ? null : r.d_max_mm ?? r.d_mean_mm) : null
		};
	});
}
wheal.get("/config", async (c) => {
	const health = await checkWhealHealth(c.env);
	const std = await loadStandard(c);
	return c.json({ data: {
		api_base: c.env.WHEAL_API_BASE ?? null,
		api_key: c.env.WHEAL_API_KEY ?? null,
		available: health.available,
		paths_available: health.paths_available,
		sam_loaded: health.sam_loaded,
		degraded_note: health.degraded_note ?? null,
		default_standard: std.code,
		control_positions: {
			positive: 101,
			negative: 102
		},
		histamine_first: true,
		message: health.available ? null : WHEAL_ADVICE[health.error_code ?? "NETWORK"] ?? "测量服务不可用，请人工填写。",
		error_code: health.error_code ?? null,
		elapsed_ms: health.elapsed_ms ?? null
	} });
});
wheal.get("/standards", async (c) => {
	const list = ((await c.env.DB.prepare(`SELECT code, name, criteria_type, min_positive_mm, rules_json,
            ctrl_pos_min_mm, ctrl_neg_max_mm, is_default
       FROM grading_standard
      WHERE is_active = 1 AND hospital_id IN (?, 'PLATFORM')
      ORDER BY is_default DESC, code`).bind(c.var.tenant).all()).results ?? []).map((row) => {
		const s = parseStandardRow(row);
		return {
			code: s.code,
			name: s.name,
			criteria_type: s.criteria_type,
			min_positive_mm: s.min_positive_mm,
			rules: s.rules,
			ctrl_pos_min_mm: s.ctrl_pos_min_mm,
			ctrl_neg_max_mm: s.ctrl_neg_max_mm,
			is_default: !!row.is_default,
			accuracy_note: s.criteria_type === "RATIO" ? "比值法：免疫手臂高于标定平面导致的放大率误差，精度最高" : "绝对 mm 判据：受放大率误差影响（本系统中约高估 19%），精度低于比值法"
		};
	});
	return c.json({ data: list });
});
wheal.post("/sessions", async (c) => {
	const b = await c.req.json();
	const tenant = c.var.tenant;
	if (!b.report_id) return c.json({
		error: "BAD_REQUEST",
		message: "缺少 report_id"
	}, 400);
	if (!await c.env.DB.prepare(`SELECT id, status FROM spt_report WHERE id = ? AND hospital_id = ?`).bind(b.report_id, tenant).first()) return c.json({
		error: "NOT_FOUND",
		message: "报告不存在"
	}, 404);
	const std = await loadStandard(c, b.grading_standard);
	const pxPerMm = b.px_per_mm == null ? null : Number(b.px_per_mm);
	if (pxPerMm != null && (!Number.isFinite(pxPerMm) || pxPerMm <= 0)) return c.json({
		error: "BAD_REQUEST",
		message: "px_per_mm 非法"
	}, 400);
	let mag = b.mag_correction == null ? 1 : Number(b.mag_correction);
	if (!Number.isFinite(mag) || mag <= 0) mag = 1;
	const id = uuid();
	await c.env.DB.prepare(`INSERT INTO wheal_session
      (id, hospital_id, report_id, operator_id, remote_session_id, photo_id,
       px_per_mm, mag_correction, calib_confidence, grading_standard, validity, status)
     VALUES (?,?,?,?,?,?,?,?,?,?,'PENDING','OPEN')`).bind(id, tenant, b.report_id, c.var.user.id, b.remote_session_id ?? null, b.photo_id ?? null, pxPerMm, mag, b.calib_confidence == null ? null : Number(b.calib_confidence), std.code).run();
	await audit(c, "WHEAL_SESSION_CREATE", {
		resource_type: "wheal_session",
		resource_id: id,
		after: {
			report_id: b.report_id,
			px_per_mm: pxPerMm,
			mag_correction: mag,
			standard: std.code
		}
	});
	return c.json({ data: {
		id,
		report_id: b.report_id,
		px_per_mm: pxPerMm,
		mag_correction: mag,
		grading_standard: std.code,
		status: "OPEN",
		validity: "PENDING",
		next_action: `请先测量阳性对照（组胺，位点 101）`
	} });
});
wheal.patch("/sessions/:id/calib", async (c) => {
	const s = await getSession(c, c.req.param("id"));
	if (!s) return c.json({
		error: "NOT_FOUND",
		message: "会话不存在"
	}, 404);
	if (s.status !== "OPEN" && s.status !== "MEASURED") return c.json({
		error: "CONFLICT",
		message: `会话已${s.status === "CONFIRMED" ? "确认" : "放弃"}，不可修改标定`
	}, 409);
	const b = await c.req.json();
	const px = Number(b.px_per_mm);
	if (!Number.isFinite(px) || px <= 0) return c.json({
		error: "BAD_REQUEST",
		message: "px_per_mm 非法"
	}, 400);
	let mag = b.mag_correction == null ? s.mag_correction : Number(b.mag_correction);
	if (!Number.isFinite(mag) || mag <= 0) mag = 1;
	await c.env.DB.prepare(`UPDATE wheal_session SET px_per_mm = ?, mag_correction = ?, calib_confidence = ?,
            updated_at = datetime('now')
      WHERE id = ? AND hospital_id = ?`).bind(px, mag, b.calib_confidence == null ? null : Number(b.calib_confidence), s.id, c.var.tenant).run();
	await audit(c, "WHEAL_CALIB_UPDATE", {
		resource_type: "wheal_session",
		resource_id: s.id,
		before: {
			px_per_mm: s.px_per_mm,
			mag_correction: s.mag_correction
		},
		after: {
			px_per_mm: px,
			mag_correction: mag
		}
	});
	const existing = await listMeasurements(c, s.id);
	return c.json({ data: {
		px_per_mm: px,
		mag_correction: mag,
		px_for_3mm: Math.round(3 * px * 10) / 10,
		stale_measurements: existing.length,
		warning: existing.length > 0 ? `已有 ${existing.length} 条测量是按旧标定计算的，请重新测量以保证一致` : px < 5 ? `px/mm 仅 ${px.toFixed(1)}，3mm 风团只有 ${(3 * px).toFixed(0)}px，测量误差会显著放大` : null
	} });
});
wheal.post("/sessions/:id/measurements", async (c) => {
	const s = await getSession(c, c.req.param("id"));
	if (!s) return c.json({
		error: "NOT_FOUND",
		message: "会话不存在"
	}, 404);
	if (s.status === "CONFIRMED" || s.status === "ABANDONED") return c.json({
		error: "CONFLICT",
		message: "会话已结束，不可再写入测量"
	}, 409);
	const uncalMeasure = isUncalibrated(s);
	const b = await c.req.json();
	const items = Array.isArray(b.measurements) ? b.measurements : b.measurement ? [b.measurement] : [];
	if (!items.length) return c.json({
		error: "BAD_REQUEST",
		message: "缺少测量数据"
	}, 400);
	if (items.length > 200) return c.json({
		error: "BAD_REQUEST",
		message: "单次最多 200 条"
	}, 400);
	const accepted = [];
	const rejected = [];
	for (const raw of items) {
		const r = sanitizeMeasurement(raw, uncalMeasure);
		if (r.ok && r.value) accepted.push(r.value);
		else rejected.push({
			position_no: raw?.position_no ?? null,
			reason: r.reason ?? "未知"
		});
	}
	if (accepted.length) {
		const stmts = [];
		for (const m of accepted) {
			stmts.push(c.env.DB.prepare(`DELETE FROM wheal_measurement WHERE session_id = ? AND hospital_id = ? AND position_no = ?`).bind(s.id, c.var.tenant, m.position_no));
			stmts.push(c.env.DB.prepare(`INSERT INTO wheal_measurement
            (id, session_id, hospital_id, position_no, click_x, click_y, point_source,
             d_max_mm, d_perp_mm, d_mean_mm, area_mm2, area_ellipse_mm2,
             solidity, has_pseudopod, segment_method, confidence,
             sam_d_mean_mm, ring_d_mean_mm, mask_key, edited, neg_ctrl_assumed,
             auto_negative)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(uuid(), s.id, c.var.tenant, m.position_no, m.click_x, m.click_y, m.point_source, m.d_max_mm, m.d_perp_mm, m.d_mean_mm, m.area_mm2, m.area_ellipse_mm2, m.solidity, m.has_pseudopod ? 1 : 0, m.segment_method, m.confidence, m.sam_d_mean_mm, m.ring_d_mean_mm, m.mask_key, m.edited ? 1 : 0, m.neg_ctrl_assumed ? 1 : 0, m.auto_negative ? 1 : 0));
		}
		await c.env.DB.batch(stmts);
	}
	const std = await loadStandard(c, s.grading_standard);
	const { validity, histMm, rows, uncalibrated: uncal } = await refreshValidity(c, s, std);
	if (rows.length) await c.env.DB.prepare(`UPDATE wheal_session SET status = 'MEASURED', updated_at = datetime('now')
        WHERE id = ? AND hospital_id = ? AND status = 'OPEN'`).bind(s.id, c.var.tenant).run();
	await audit(c, "WHEAL_MEASURE", {
		resource_type: "wheal_session",
		resource_id: s.id,
		after: {
			accepted: accepted.map((m) => m.position_no),
			rejected,
			validity: validity.validity,
			methods: accepted.map((m) => m.segment_method)
		}
	});
	const graded = gradeRows(rows, histMm, std, uncal);
	const review = graded.filter((r) => r.needs_review);
	return c.json({ data: {
		accepted: accepted.length,
		rejected,
		validity: validity.validity,
		can_apply: validity.can_apply,
		validity_message: validity.message,
		histamine_d_mm: histMm,
		ctrl_pos_baseline_mm: validity.ctrl_pos_baseline_mm,
		ctrl_pos_ok: validity.ctrl_pos_ok,
		standard: std.code,
		measurements: graded,
		need_review: review.map((r) => ({
			position_no: r.position_no,
			confidence: r.confidence,
			pseudopod_suspected: r.pseudopod_suspected,
			ctrl_unconfirmed: r.ctrl_unconfirmed,
			reason: r.ctrl_unconfirmed ? "对照位点为自动测得，请核对读数后确认（对照决定本次试验是否有效）" : r.pseudopod_suspected ? "疑似伪足，请确认是否升为 ++++" : "算法置信度偏低，请复核边界"
		}))
	} });
});
wheal.get("/sessions/:id", async (c) => {
	const s = await getSession(c, c.req.param("id"));
	if (!s) return c.json({
		error: "NOT_FOUND",
		message: "会话不存在"
	}, 404);
	const std = await loadStandard(c, s.grading_standard);
	const rows = await listMeasurements(c, s.id);
	const v = checkValidity(s.histamine_d_mm, s.saline_d_mm, std, isUncalibrated(s));
	return c.json({ data: {
		...s,
		standard: {
			code: std.code,
			name: std.name,
			criteria_type: std.criteria_type,
			min_positive_mm: std.min_positive_mm
		},
		validity_detail: v,
		measurements: gradeRows(rows, s.histamine_d_mm, std, isUncalibrated(s))
	} });
});
wheal.post("/sessions/:id/apply", async (c) => {
	const s = await getSession(c, c.req.param("id"));
	if (!s) return c.json({
		error: "NOT_FOUND",
		message: "会话不存在"
	}, 404);
	if (s.status === "ABANDONED") return c.json({
		error: "CONFLICT",
		message: "会话已放弃"
	}, 409);
	if (!s.report_id) return c.json({
		error: "CONFLICT",
		message: "会话未关联报告"
	}, 409);
	const std = await loadStandard(c, s.grading_standard);
	const rows = await listMeasurements(c, s.id);
	if (!rows.length) return c.json({
		error: "CONFLICT",
		message: "尚无测量数据"
	}, 409);
	const v = checkValidity(s.histamine_d_mm, s.saline_d_mm, std, isUncalibrated(s));
	if (!v.can_apply) return c.json({
		error: "INVALID_TEST",
		validity: v.validity,
		message: v.message,
		histamine_d_mm: s.histamine_d_mm,
		saline_d_mm: s.saline_d_mm
	}, 409);
	/** 医生可覆盖建议等级：{ "3": "+++" }。覆盖后 measure_source 记 AUTO_EDITED */
	const overrides = (await c.req.json().catch(() => ({})))?.overrides ?? {};
	const graded = gradeRows(rows, s.histamine_d_mm, std, isUncalibrated(s));
	const applied = [];
	const stmts = [];
	for (const r of graded) {
		if (r.position_no === 101 || r.position_no === 102) continue;
		const ov = overrides[String(r.position_no)];
		const finalGrade = ov ?? r.grade_suggested;
		if (!finalGrade) continue;
		const source = r.segment_method === "MANUAL" ? ov ? "MANUAL_EDITED" : "MANUAL" : ov || r.edited ? "AUTO_EDITED" : r.auto_negative ? "AUTO_NEGATIVE" : "AUTO";
		const text = cellText(finalGrade, r.d_max_mm ?? r.d_mean_mm);
		stmts.push(c.env.DB.prepare(`UPDATE spt_report_row_snapshot
            SET positive_area = ?, d_mean_mm = ?, d_max_mm = ?, d_perp_mm = ?,
                area_mm2 = ?, solidity = ?,
                grade_suggested = ?, grade_confirmed = ?, grade_ratio = ?,
                measure_source = ?, segment_method = ?, measure_confidence = ?,
                mask_key = ?
          WHERE report_id = ? AND hospital_id = ? AND position_no = ?`).bind(text, r.d_mean_mm, r.d_max_mm, r.d_perp_mm, r.area_mm2, r.solidity, r.grade_suggested, finalGrade, r.grade_ratio, source, r.segment_method, r.confidence, r.mask_key, s.report_id, c.var.tenant, r.position_no));
		applied.push({
			position_no: r.position_no,
			grade_suggested: r.grade_suggested,
			grade_confirmed: finalGrade,
			overridden: !!ov,
			cell_text: text
		});
	}
	if (!applied.length) return c.json({
		error: "CONFLICT",
		message: "没有可写入的位点（仅有对照测量）"
	}, 409);
	for (const p of [101, 102]) {
		const r = graded.find((x) => x.position_no === p);
		if (!r) continue;
		const ctrlSource = r.neg_ctrl_assumed ? "ASSUMED_NEGATIVE" : r.segment_method === "MANUAL" ? "MANUAL" : r.edited ? "AUTO_EDITED" : "AUTO";
		stmts.push(c.env.DB.prepare(`UPDATE spt_report_row_snapshot
            SET d_mean_mm = ?, d_max_mm = ?, d_perp_mm = ?, area_mm2 = ?, solidity = ?,
                measure_source = ?, segment_method = ?, measure_confidence = ?
          WHERE report_id = ? AND hospital_id = ? AND position_no = ?`).bind(r.d_mean_mm, r.d_max_mm, r.d_perp_mm, r.area_mm2, r.solidity, ctrlSource, r.segment_method, r.confidence, s.report_id, c.var.tenant, p));
	}
	stmts.push(c.env.DB.prepare(`UPDATE wheal_session SET status = 'CONFIRMED', validity = ?, updated_at = datetime('now')
        WHERE id = ? AND hospital_id = ?`).bind(v.validity, s.id, c.var.tenant));
	await c.env.DB.batch(stmts);
	await audit(c, "WHEAL_APPLY", {
		resource_type: "spt_report",
		resource_id: s.report_id,
		after: {
			session_id: s.id,
			standard: std.code,
			histamine_d_mm: s.histamine_d_mm,
			saline_d_mm: s.saline_d_mm,
			applied,
			overrides_count: Object.keys(overrides).length
		}
	});
	return c.json({ data: {
		report_id: s.report_id,
		applied_count: applied.length,
		applied,
		standard: std.code,
		validity: v.validity,
		message: `已写入 ${applied.length} 个位点的判读结果。请医生在报告单上核对后提交。`
	} });
});
wheal.post("/sessions/:id/abandon", async (c) => {
	const s = await getSession(c, c.req.param("id"));
	if (!s) return c.json({
		error: "NOT_FOUND",
		message: "会话不存在"
	}, 404);
	if (s.status === "CONFIRMED") return c.json({
		error: "CONFLICT",
		message: "会话已确认，不能放弃"
	}, 409);
	await c.env.DB.prepare(`UPDATE wheal_session SET status = 'ABANDONED', updated_at = datetime('now')
      WHERE id = ? AND hospital_id = ?`).bind(s.id, c.var.tenant).run();
	await audit(c, "WHEAL_SESSION_ABANDON", {
		resource_type: "wheal_session",
		resource_id: s.id
	});
	return c.json({ data: {
		id: s.id,
		status: "ABANDONED"
	} });
});
//#endregion
//#region src/page.ts
var INDEX_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>过敏原皮肤点刺实验记录管理系统</title>
<link rel="icon" href="/static/img/favicon.ico" sizes="any">
<link rel="icon" href="/static/img/icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="/static/img/icon-192.png">
<script src="https://cdn.tailwindcss.com"><\/script>
<link href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/dayjs@1.11.10/dayjs.min.js"><\/script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"><\/script>
<script>
tailwind.config = {
  theme: { extend: {
    colors: {
      brand: { 50:'#eff6ff',100:'#dbeafe',500:'#1f6feb',600:'#1a5fd0',700:'#164ea8' },
      ink: { 700:'#334155',800:'#1e293b',900:'#0f172a' }
    }
  }}
}
<\/script>
<link href="/static/style.css" rel="stylesheet">
</head>
<body class="bg-slate-100 text-slate-800 antialiased">

<!-- ============================ 登录页 ============================ -->
<!-- flex-col（而非默认的横向 flex）：底部要放技术支持信息，
     若保持横向排列，页脚会与登录区左右并排而不是落到底部。
     改为纵向后：中间区 flex-1 吸收剩余高度使登录框保持垂直居中，页脚自然贴底。
     app.js 会在显示时补上 flex 类。 -->
<section id="login-view" class="hidden min-h-screen flex-col p-4 sm:p-6 bg-gradient-to-br from-slate-800 via-slate-900 to-brand-700">
  <!-- 中间区：撑开剩余空间，让登录内容垂直居中（页脚不参与居中计算） -->
  <div id="login-main" class="flex-1 w-full flex items-center justify-center">
  <!-- 左右两栏：左=Logo与系统名称，右=登录窗口；窄屏（<lg）自动上下堆叠 -->
  <div id="login-layout" class="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-14 items-center">

    <!-- 左侧：品牌区 -->
    <header id="login-brand" class="text-center lg:text-left">
      <!-- AllergyOS 品牌标识：深色背景上直接放透明底 PNG（logo 本身为亮蓝，深底对比足够，
           不再套 bg-white/10 圆角容器，避免出现一个多余的方块把 logo 框住） -->
      <img id="login-logo" src="/static/img/logo-full.png" alt="AllergyOS"
           class="h-24 lg:h-28 w-auto mb-6 mx-auto lg:mx-0 drop-shadow-lg">
      <h1 class="text-3xl lg:text-4xl font-bold text-white leading-tight tracking-wide">过敏原皮肤点刺实验</h1>
      <p class="text-slate-300 mt-3 text-base">记录管理系统 · Skin Prick Test</p>
      <ul class="mt-7 space-y-2.5 text-sm text-slate-300/90 hidden lg:block">
        <li><i class="fas fa-shield-halved text-brand-500 w-5"></i>院内数据隔离，各医院数据互不可见</li>
        <li><i class="fas fa-camera text-brand-500 w-5"></i>手臂实验区拍照留档与结果识别</li>
        <li><i class="fas fa-table-list text-brand-500 w-5"></i>模版库与过敏原库院内可编辑</li>
      </ul>
    </header>

    <!-- 右侧：登录窗口 -->
    <form id="login-form" class="w-full max-w-md mx-auto lg:mx-0 bg-white rounded-2xl shadow-2xl p-8 space-y-5">
      <div>
        <label class="block text-sm font-medium mb-1.5" for="login-username">用户名</label>
        <div class="relative">
          <i class="fas fa-user absolute left-3 top-3 text-slate-400"></i>
          <input id="login-username" autocomplete="username" required
            class="w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none"
            placeholder="请输入用户名">
        </div>
      </div>
      <div>
        <label class="block text-sm font-medium mb-1.5" for="login-password">密码</label>
        <div class="relative">
          <i class="fas fa-lock absolute left-3 top-3 text-slate-400"></i>
          <input id="login-password" type="password" autocomplete="current-password" required
            class="w-full pl-10 pr-10 py-2.5 border border-slate-300 rounded-lg focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none"
            placeholder="请输入密码">
          <button type="button" id="toggle-pwd" class="absolute right-3 top-3 text-slate-400 hover:text-slate-600" aria-label="显示密码">
            <i class="fas fa-eye"></i>
          </button>
        </div>
      </div>
      <!-- 「所属医院」下拉已移除：医院由账号自身归属自动匹配（服务端 /api/auth/login）。
           一是少一个必填项、消除选错医院导致的登录失败；
           二是公网登录页不再枚举全部医院名单。
           「记住我（7 天免登录）」同步移除：诊室为共用终端，令牌只存 sessionStorage，
           关闭浏览器即失效，且超过 2 小时无操作自动退出。 -->
      <div class="flex items-center justify-between text-sm">
        <span id="session-hint" class="inline-flex items-center gap-1.5 text-slate-500">
          <i class="fas fa-clock text-slate-400"></i>2 小时无操作自动退出
        </span>
        <button type="button" id="forgot-link" class="text-brand-500 hover:underline">忘记密码？</button>
      </div>
      <p id="login-error" class="hidden text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2"></p>
      <button type="submit" id="login-submit"
        class="w-full bg-brand-500 hover:bg-brand-600 text-white font-semibold py-2.5 rounded-lg transition shadow-lg shadow-brand-500/30">
        <i class="fas fa-sign-in-alt mr-2"></i>登录
      </button>
    </form>
  </div>
  </div>

  <!-- 技术支持信息（页脚）
       深色背景上用 slate-400/300：既能读清又不与登录框抢注意力。
       各项之间用 · 分隔并允许换行（flex-wrap），窄屏下会自动折行而不是被裁掉。
       电话与邮箱做成可点链接：移动端可直接拨号/发信。 -->
  <footer id="login-footer" class="shrink-0 w-full max-w-5xl mx-auto mt-8 pt-5 border-t border-white/10 text-center">
    <p class="text-sm text-slate-300">
      本系统由<span class="text-slate-100 font-medium">杭州数智医济医疗科技有限公司</span>提供技术开发与维护
    </p>
    <div class="mt-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-xs text-slate-400">
      <span class="inline-flex items-center gap-1.5">
        <i class="fas fa-phone text-slate-500"></i>
        <a href="tel:17610331100" class="hover:text-slate-200 transition">17610331100</a>
        <span class="text-slate-600">/</span>
        <a href="tel:057188581295" class="hover:text-slate-200 transition">0571-88581295</a>
      </span>
      <span class="inline-flex items-center gap-1.5">
        <i class="fas fa-location-dot text-slate-500"></i>
        杭州市余杭区南湖未来科学园4幢101室
      </span>
      <!-- 邮箱：需方尚未提供具体地址，暂不展示（宁可缺项，也不放占位符或编造地址上线）。
           拿到邮箱后把下面两行的注释去掉、替换 support@example.com 即可，无需改其他地方： -->
      <!-- <span id="login-footer-email" class="inline-flex items-center gap-1.5">
        <i class="fas fa-envelope text-slate-500"></i>
        <a href="mailto:support@example.com" class="hover:text-slate-200 transition">support@example.com</a>
      </span> -->
    </div>
  </footer>
</section>

<!-- ============================ 主应用 ============================ -->
<!-- h-screen + overflow-hidden：原来是 min-h-screen，那只是「最小」高度，
     内容一多整个容器就变高，#page-body 的 overflow-auto 因父级没有确定高度而失效，
     结果是整页滚动、侧栏跟着上下移动。固定成一屏高后，只有右侧内容区滚动。
     注意：打印时必须把这个限制解除（见 style.css 的 @media print），
     否则报告单会被裁成一屏。 -->
<div id="app-view" class="hidden h-screen overflow-hidden flex">
  <!-- 侧边栏 -->
  <!-- 侧栏宽度 w-60(240px) → w-64(256px)：字号整体提一档后，
       「点刺实验管理系统」、「报告单列表」等文字在 240px 下会顶到边或被 truncate -->
  <aside id="sidebar" class="w-64 shrink-0 bg-ink-900 text-slate-300 flex flex-col h-full overflow-hidden">
    <div class="h-16 flex items-center gap-2.5 px-4 border-b border-white/10">
      <!-- 侧栏用不含 ® 的方形图标：60px 高的栏内 ® 会缩成噪点且把重心带偏 -->
      <img src="/static/img/logo-icon.png" alt="AllergyOS" class="h-10 w-10 shrink-0 object-contain">
      <div class="leading-tight min-w-0">
        <p class="text-white font-semibold text-base truncate">AllergyOS</p>
        <p class="text-xs text-slate-400 truncate">点刺实验管理系统</p>
      </div>
    </div>
    <nav id="main-nav" class="flex-1 py-4 space-y-1 px-3 overflow-y-auto"></nav>
    <div class="border-t border-white/10 p-3">
      <div class="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/5">
        <div id="user-avatar" class="h-10 w-10 rounded-full bg-brand-500 text-white grid place-items-center text-base font-bold"></div>
        <div class="flex-1 min-w-0">
          <p id="user-name" class="text-[0.9375rem] text-white truncate"></p>
          <p id="user-role" class="text-xs text-slate-400 truncate"></p>
        </div>
        <button id="logout-btn" title="退出登录" class="text-slate-400 hover:text-red-400 px-1 text-lg">
          <i class="fas fa-power-off"></i>
        </button>
      </div>
    </div>
  </aside>

  <!-- 主区 -->
  <!-- min-h-0 是关键：flex 子项默认 min-height:auto，会被内容撑开，
       导致下面 #page-body 的 overflow-auto 依然不生效 -->
  <main class="flex-1 min-w-0 flex flex-col h-full min-h-0">
    <header class="h-16 bg-white border-b border-slate-200 flex items-center px-6 gap-4 shrink-0">
      <!-- 返回按钮：只在「下钻进来的页面」显示（如报告单详情）。
           顶层页面侧栏本身就是入口，出现返回反而会让人误以为能退到更上层。 -->
      <button id="page-back" type="button" title="返回上一页（Alt+←）"
              class="hidden items-center gap-1.5 shrink-0 text-sm text-slate-600 hover:text-brand-600
                     border border-slate-300 hover:border-brand-300 hover:bg-brand-50
                     rounded-lg px-2.5 py-1.5 no-print">
        <i class="fas fa-arrow-left"></i><span id="page-back-label">返回</span>
      </button>
      <h2 id="page-title" class="text-lg font-semibold text-ink-800"></h2>
      <span id="hospital-badge" class="hidden text-xs px-2.5 py-1 rounded-full bg-brand-50 text-brand-700 border border-brand-100"></span>
      <div class="flex-1"></div>
      <div id="capture-status" class="flex items-center gap-2 text-xs"></div>
      <button id="change-pwd-btn" class="text-slate-500 hover:text-brand-500 text-sm" title="修改密码">
        <i class="fas fa-key"></i>
      </button>
    </header>
    <div id="page-body" class="flex-1 overflow-auto p-6"></div>
  </main>
</div>

<!-- 通用弹窗容器 -->
<div id="modal-root"></div>
<div id="toast-root" class="fixed top-4 right-4 z-[9999] space-y-2"></div>
<div id="loading-mask" class="hidden fixed inset-0 z-[9998] bg-black/30 backdrop-blur-sm items-center justify-center">
  <div class="bg-white rounded-xl px-6 py-4 flex items-center gap-3 shadow-2xl">
    <i class="fas fa-circle-notch fa-spin text-brand-500 text-xl"></i>
    <span id="loading-text" class="text-sm font-medium">处理中…</span>
  </div>
</div>

<script src="/static/api.js"><\/script>
<script src="/static/ui.js"><\/script>
<!-- danger.js 依赖 UI/API，且被 pages-patients / pages-reports 使用，必须排在它们之前 -->
<script src="/static/danger.js"><\/script>
<script src="/static/allergens.js"><\/script>
<script src="/static/capture.js"><\/script>
<script src="/static/pages-admin.js"><\/script>
<script src="/static/pages-patients.js"><\/script>
<script src="/static/pages-wheal.js"><\/script>
<script src="/static/print-render.js"><\/script>
<script src="/static/pages-report.js"><\/script>
<script src="/static/pages-reports.js"><\/script>
<script src="/static/pages-allergens.js"><\/script>
<script src="/static/pages-templates.js"><\/script>
<script src="/static/pages-settings.js"><\/script>
<script src="/static/pages-exports.js"><\/script>
<script src="/static/app.js"><\/script>
</body>
</html>`;
//#endregion
//#region src/index.tsx
var app = new Hono();
app.use("*", logger());
app.use("/api/*", cors());
app.route("/api/auth", auth);
app.route("/api/admin", admin);
app.route("/api/patients", patients);
app.route("/api/templates", templates);
app.route("/api/reports", reports);
app.route("/api/capture", capture);
app.route("/api/exports", exportsRoute);
app.route("/api/allergens", allergens);
app.route("/api/wheal", wheal);
app.route("/api/danger", danger);
app.get("/api/files/*", authGuard, async (c) => {
	const key = c.req.path.replace("/api/files/", "");
	const owner = hospitalOfKey(key);
	const user = c.var.user;
	if (user.role !== "PLATFORM_ADMIN" && owner !== user.hospital_id) return c.json({
		error: "FORBIDDEN",
		message: "无权访问该文件"
	}, 403);
	const f = await getFile(c.env, key);
	if (!f) return c.json({ error: "NOT_FOUND" }, 404);
	return new Response(f.body, { headers: {
		"Content-Type": f.contentType,
		"Cache-Control": "private, max-age=3600"
	} });
});
app.get("/api/health", async (c) => {
	let db = "unknown";
	try {
		await c.env.DB.prepare("SELECT 1").first();
		db = "ok";
	} catch (e) {
		db = "error: " + e.message;
	}
	return c.json({
		ok: true,
		service: "SPT 过敏原皮肤点刺实验记录管理系统",
		db,
		storage: c.env.R2 ? "r2" : "d1-fallback",
		ocr: c.env.OCR_API_BASE && c.env.OCR_API_KEY ? "configured" : "not-configured",
		time: (/* @__PURE__ */ new Date()).toISOString()
	});
});
app.post("/api/setup", async (c) => {
	if (await c.env.DB.prepare(`SELECT id FROM user_account WHERE role = 'PLATFORM_ADMIN' LIMIT 1`).first()) return c.json({
		ok: true,
		message: "已初始化",
		created: false
	});
	const id = uuid();
	await c.env.DB.prepare(`INSERT INTO user_account (id, hospital_id, username, password_hash, real_name, role, is_active, must_change_password)
     VALUES (?,?,?,?,?,?,1,1)`).bind(id, PLATFORM_TENANT, "admin", await hashPassword("Admin@123"), "平台管理员", "PLATFORM_ADMIN").run();
	return c.json({
		ok: true,
		created: true,
		username: "admin"
	});
});
app.onError((err, c) => {
	console.error("[UNHANDLED]", err && err.stack ? err.stack : err);
	if (c.req.path.startsWith("/api/")) return c.json({
		error: "INTERNAL",
		message: err?.message || "服务器内部错误"
	}, 500);
	return c.text("Internal Server Error", 500);
});
app.get("*", (c) => c.html(INDEX_HTML));
//#endregion
export { app as default };
