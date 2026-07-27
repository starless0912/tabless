# Wiring up your agent

*[English](#english) · [中文](#中文)*

The point of tabless is that you stop doing the filing. Paste one of the
snippets below into the instructions your coding agent always reads, and every
HTML deliverable it produces lands on the shelf instead of in a browser tab.

Where to put it:

| Agent | File |
|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (global) or `./CLAUDE.md` (per project) |
| Codex / Cursor / most others | `AGENTS.md` in the repo root |
| Anything else | wherever its system prompt or rules live |

Keep it in the *global* file if you want the habit everywhere. That is the whole
difference between a tool you remember to use and a tool that just works.

---

## English

```markdown
## Delivering HTML

When you finish something meant to be **read** — a report, a prototype, a
reference doc, an experiment write-up — write it as HTML and file it:

    tabless add "<path>" --type <type>

Do not open it with `start` / `open` / `xdg-open`, and do not just hand back the
path. Filing it keeps a durable copy and opens it in the reader, which is where
the user actually reads things.

**Choosing `--type`.** Run `tabless types` first and reuse an existing type when
one fits — those are the vocabulary. Inventing a synonym (`reports` beside
`report`) splits one shelf into two, which is the exact problem this tool
exists to solve. If nothing fits, pass a new name; the set is open on purpose.

The test is **what the page is for the reader**, not what it is about:

| type | it holds | test |
|---|---|---|
| `report` | analysis, progress, retros, meeting notes | read once, then done |
| `prototype` | something interactive | used to try an idea out |
| `doc` | rules, specs, references | you will come back to it |
| `eval` | comparisons, experiment boards, blind reviews | a by-product of an experiment |

**Titles identify documents.** Same project + same title means a new version of
the same document — exactly right for a progress page you keep updating, and
exactly wrong for two unrelated things. Pass `--title` to keep those apart.

**Don't pass `--project` unless you have to.** It is inferred from the path.
Guessing wrong (passing a directory name instead of the registered project) is
how one project ends up as two tabs.

**Still changing? Register it, don't archive it.** A prototype you are still
editing belongs in `tabless live <url> --project <p> --title <t>`, which stores
a pointer. A snapshot of a moving target expires, and usually arrives broken.
```

---

## 中文

```markdown
## HTML 交付纪律

凡是产出给**人看**的东西——报告、原型、参考文档、实验产物——一律写成 HTML 并入库：

    tabless add "<路径>" --type <类型>

不要用 `start` / `open` / `xdg-open` 打开它，也不要只把路径贴回来。入库会存下一份
不会失效的副本，并在 reader 里打开——那才是用户真正读东西的地方。

**怎么选 `--type`。** 先跑 `tabless types` 看现有类型，能复用就复用——那就是这个库的
词汇表。造一个同义词（`report` 旁边再来个 `reports`）会把一个架子劈成两个，而
「东西分散在多处」正是这个工具要解决的问题本身。确实装不下就传个新名字，类型是开放
集合，这是有意的。

判据是**「它对读者是什么」，不是「它讲什么内容」**：

| 类型 | 装什么 | 判据 |
|---|---|---|
| `report` | 分析、进展、复盘、会议纪要 | 一次性，看完就完 |
| `prototype` | 可交互的东西 | 用来体验和验证 |
| `doc` | 规则书、规范、正典 | 会反复回来查 |
| `eval` | 盲评、对比、实验站 | 实验过程的产物 |

**标题就是文档的身份。** 同项目 + 同标题 = 同一份文档的新版本——对持续更新的进度页
正是想要的，对两份不同的东西则是灾难。后者请用 `--title` 显式拆开。

**没必要别传 `--project`。** 它能从路径推断出来。传错（比如传了工作目录名而不是注册
的项目名）正是一个项目在文库里裂成两个 tab 的原因。

**还在改的东西登记，不要归档。** 仍在编辑的原型应该用
`tabless live <地址> --project <项目> --title <标题>`，只存指针。给移动目标存快照，
存下来的是个会过期的副本，而且多半一开始就是坏的。
```

---

## Does it actually work?

Ask your agent to write you something and watch. If it opens a browser tab
instead of calling `tabless add`, the snippet is in a file the agent does not
read — move it to the global one.

Two things to keep an eye on early:

- **`tabless types` growing synonyms.** `tabless add` prints the existing types
  whenever it sees a name it has not seen before, precisely so a typo announces
  itself. Fix a stray one with `tabless retype <id> <type>`.
- **Everything landing in `_inbox`.** That means the path could not be matched
  to a project. Either add the project to `projects.toml` (`tabless where` says
  where that file goes) or accept `_inbox` — it is a perfectly good single-tab
  setup for one-project workflows.
