#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jscheck.py —— 无 Node 环境下的 JS 结构校验器

沙箱内没有任何 JS 运行时（node/nodejs/deno/bun 都不存在），无法用
`node --check` 做真正的语法解析。本脚本用一个**统一模式栈状态机**扫描源码，
正确区分下列上下文，并只在"代码"上下文里做括号配对：

    code            普通代码
    ( [ {           括号层
    `...`           模板字符串（其内部不是代码）
    ${...}          模板串里的插值（其内部又是代码，可再嵌套模板串）
    '...' "..."     普通字符串（不能跨行）
    // /* */        注释
    /re/            正则字面量

为什么必须做词法区分：直接 grep 计数 { } ( ) 会把
    `${g.label}${k==='S'?'水平':'倾向'}`
    "background:linear-gradient(90deg,#d8452f,#ff6b52)"
里的符号一并算入，得到毫无意义的"不平衡"结论。

设计要点：模板串本身作为一个栈层存在，所以"模板串没闭合"在扫到文件末尾时
自然表现为"栈非空"——不需要额外的特判逻辑（上一版正是因为用旁路变量
跟踪模板闭合而漏报了这类错误）。

局限：这是**词法级**检查，不做表达式语法解析。它能可靠抓出括号/引号/
模板串未闭合与错配，但抓不出 `if (a) else` 这类纯语法错误。
因此它是必要门禁，不是充分证明。
"""
import sys, re

# 出现在这些 token 之后的 '/' 应解释为正则开头，而不是除号
KEYWORDS_BEFORE_REGEX = {
    "return", "typeof", "case", "in", "of", "new", "delete", "void",
    "instanceof", "do", "else", "yield", "await", "throw",
}


class Ctx:
    """栈上的一层上下文"""
    __slots__ = ("kind", "line", "col")

    def __init__(self, kind, line, col):
        self.kind = kind          # '(' '[' '{' '`' '${'
        self.line = line
        self.col = col

    def __repr__(self):
        return f"{self.kind}@{self.line}:{self.col}"


CLOSERS = {")": "(", "]": "[", "}": "{"}


def lex_check(src, path):
    errors = []
    stack = []
    i, n = 0, len(src)
    line = 1
    line_start = 0
    last_tok = ""      # '' | 'punct' | 'kw' | 'val'
    counts = dict(template_literals=0, tpl_exprs=0, line_comments=0,
                  block_comments=0, strings=0, regexes=0, max_depth=0)

    def col():
        return i - line_start + 1

    def in_template():
        return bool(stack) and stack[-1].kind == "`"

    while i < n:
        c = src[i]

        if c == "\n":
            line += 1
            i += 1
            line_start = i
            continue

        # ============ 模板字符串内部（不是代码） ============
        if in_template():
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
                last_tok = "val"
                i += 1
                continue
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                stack.append(Ctx("${", line, col()))
                counts["tpl_exprs"] += 1
                counts["max_depth"] = max(counts["max_depth"], len(stack))
                last_tok = "punct"
                i += 2
                continue
            i += 1
            continue

        # ============ 以下均为"代码"上下文 ============

        # ---- 注释 ----
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            counts["line_comments"] += 1
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            counts["block_comments"] += 1
            j = src.find("*/", i + 2)
            if j == -1:
                errors.append(f"{path}:{line}:{col()}: 块注释 /* 未闭合")
                break
            seg = src[i:j + 2]
            nl = seg.count("\n")
            if nl:
                line += nl
                line_start = i + seg.rfind("\n") + 1
            i = j + 2
            continue

        # ---- 普通字符串 ----
        if c in "'\"":
            counts["strings"] += 1
            q, j, closed = c, i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "\n":
                    break
                if src[j] == q:
                    closed = True
                    break
                j += 1
            if not closed:
                errors.append(f"{path}:{line}:{col()}: 字符串 {q} 未在本行闭合")
                i += 1
                last_tok = "val"
                continue
            i = j + 1
            last_tok = "val"
            continue

        # ---- 模板串开始 ----
        if c == "`":
            counts["template_literals"] += 1
            stack.append(Ctx("`", line, col()))
            counts["max_depth"] = max(counts["max_depth"], len(stack))
            i += 1
            continue

        # ---- 正则 or 除号 ----
        if c == "/":
            if last_tok in ("", "punct", "kw"):
                j, in_class, closed = i + 1, False, False
                while j < n:
                    d = src[j]
                    if d == "\\":
                        j += 2
                        continue
                    if d == "\n":
                        break
                    if d == "[":
                        in_class = True
                    elif d == "]":
                        in_class = False
                    elif d == "/" and not in_class:
                        closed = True
                        break
                    j += 1
                if closed:
                    counts["regexes"] += 1
                    k = j + 1
                    while k < n and src[k].isalpha():
                        k += 1
                    i = k
                    last_tok = "val"
                    continue
            i += 1
            last_tok = "punct"
            continue

        # ---- 开括号 ----
        if c in "([{":
            stack.append(Ctx(c, line, col()))
            counts["max_depth"] = max(counts["max_depth"], len(stack))
            i += 1
            last_tok = "punct"
            continue

        # ---- 闭括号 ----
        if c in ")]}":
            if not stack:
                errors.append(f"{path}:{line}:{col()}: 多余的 '{c}'（栈已空）")
            else:
                top = stack[-1]
                if c == "}" and top.kind == "${":
                    stack.pop()            # 弹回模板串上下文
                elif top.kind == CLOSERS[c]:
                    stack.pop()
                elif top.kind == "`":
                    errors.append(
                        f"{path}:{line}:{col()}: 遇到 '{c}' 但仍在 "
                        f"{top.line} 行开始的模板字符串内（模板串未闭合？）")
                else:
                    errors.append(
                        f"{path}:{line}:{col()}: '{c}' 与 {top.line}:{top.col} "
                        f"的 '{top.kind}' 不匹配")
                    stack.pop()
            i += 1
            last_tok = "val"
            continue

        # ---- 标识符 / 数字 ----
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word = src[i:j]
            last_tok = "kw" if word in KEYWORDS_BEFORE_REGEX else "val"
            i = j
            continue

        # ---- 其它标点 ----
        if not c.isspace():
            last_tok = "punct"
        i += 1

    for ctx in stack:
        what = {"`": "模板字符串 `", "${": "模板插值 ${"}.get(ctx.kind, f"'{ctx.kind}'")
        errors.append(f"{path}:{ctx.line}:{ctx.col}: {what} 未闭合（到文件末尾仍在栈中）")

    return errors, counts


def main():
    argv = sys.argv[1:]
    if not argv:
        print("用法: jscheck.py <file.js> [more.js ...]")
        return 2
    rc = 0
    for path in argv:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        errors, counts = lex_check(src, path)
        print(f"=== {path} ({len(src)} B, {src.count(chr(10)) + 1} 行) ===")
        print("  词法统计: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        if errors:
            rc = 1
            print(f"  ❌ {len(errors)} 个结构问题：")
            for e in errors[:40]:
                print("    -", e)
            if len(errors) > 40:
                print(f"    ... 另有 {len(errors) - 40} 个")
        else:
            print("  ✅ 括号 / 引号 / 模板串结构平衡")
    print("\n说明：词法级检查，不做表达式语法解析。"
          "通过 ≠ 语法 100% 正确，仅排除括号与字符串/模板串未闭合、错配这类错误。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
