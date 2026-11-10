# Agent 工程实践笔记（STAR 版）

按「场景 → 任务 → 动作 → 结果 → 理想方案」组织。每条都落在本仓库的真实实现与真实返工上，理想方案给的是成熟做法与升级时机，不是空话。

源码链接指向 GitHub `main` 分支，行号对应本文件所在提交；链接文本直接写成「路径：行号」，即使锚点失效也能看出该看哪几行。

项目基线：18 个模块 1854 行 Python，手写 ReAct loop（`app/agent.py` 83 行）+ 工具注册表 + 预算层 + 本地检索 + 六维打分 + SQLite 会话 + Web 界面。所有结论可用 git log 与 `eval/README.md` 复核。



***

# 一、执行层：循环、工具、上下文

## 1. 核心循环：停止条件要能被证明

**S 场景**　Agent 最大的工程风险不是答得不好，是不收敛 —— 模型反复调工具、空转、最后什么都不返回。

**T 任务**　循环必须有硬边界：任何输入都能退出，且退出时能说清为什么停。

**A 动作**



* 手写 `run_agent(llm, system, user_msg, schemas, impls, max_iter=8)`

* 每轮先追加 assistant 消息（含 `tool_calls` 原文），再判断：**没有 tool\_calls 就返回 final**
* 有 tool\_calls 则逐个执行，结果以 `role=tool` 回喂；工具抛异常时转成 `error: ...` 回喂，不中断循环

* 到 `max_iter` 返回 `iterations=max_iter, final=None, stop_reason=max_iter`

* 返回值固定带 `stop_reason`：`answered / max_iter / repeated_tool_call`；预算超限不吞掉，直接抛给调用方

* 同一工具 + 同一组参数第二次出现就不执行，塞一条 `error: 重复调用…` 让模型换路；累计 2 次直接停（`max_repeats=2`）

* **源码**　[app/agent.py:15-83](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py#L15-L83)（`run_agent` 主循环）、[tests/test\_offline.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_offline.py)（循环与工具分发测试）

**R 结果**　stub 演示 3 轮内收敛；真实单题 2 次调用完成；单测覆盖三条出口（`answered` / `max_iter` / `repeated_tool_call`）与「重复调用只执行 1 次」，全过。

**理想方案**



* 停止原因做成枚举（**已做**）：`answered / max_iter / repeated_tool_call`；`budget_exceeded` 走异常、不混进返回值，`error` 类归到工具级而不是循环级——一次工具失败不该等同于整轮失败。

* 迭代上限和预算上限是两个维度（调用次数 vs 时间 /token），两道都要有，不能互相替代。

* 重复调用检测（**已做**）：同一工具同一参数第二次出现就注入提示，累计 2 次强制停。只加 max\_iter 治不了原地打转——模型会用同样的调用把 8 轮烧完。

* 长任务要可恢复：每轮把 messages + 已完成工具结果落盘，断点重跑不重复付费。

## 2. 工具注册与 schema：别让模型猜参数类型

**S 场景**　工具一多，绝大多数失败不是模型不会做，是参数不合法。

**T 任务**　schema 稳定、与实现同源、失败可诊断可回喂。

**A 动作**



* `@tool` 装饰器注册进 `TOOLS` 字典，重名直接抛错（防止静默覆盖）

* `tool_schemas()` 用 `inspect.signature` 生成 OpenAI function schema，docstring 第一行当 description

* 类型从注解推导：`int → integer`、`float → number`、`bool → boolean`、`list[str] → array[string]`、`Literal["a","b"] → enum`；`Optional[X]` 或有默认值的参数不进 `required`

* docstring 的 `Args:` 段按 `name: 说明` 解析成每个参数的 description；没有注解的参数退回 `string`，与旧行为兼容

* 业务工具两个：`next_question`（题库轮换）、`save_feedback`

* **源码**　[app/tools.py:34-134](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py#L34-L134)（类型推导 + 参数说明解析 + `schema_for`）、[app/tools.py:136-163](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py#L136-L163)（`@tool` 注册与两个业务工具）、[tests/test\_guards.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_guards.py)（schema 类型测试）

**R 结果**　demo 全流程工具调度跑通，schema 与实现零漂移；`tests/test_guards.py` 7 条覆盖 int/bool/ 枚举 / 数组 / 可选 / 无注解 / 参数说明；`tests/test_hardening.py` 6 条覆盖类型不符、缺必填、非法 JSON、未知工具、多余字段、以及「校验失败后回喂、模型下一轮改对」。

**踩过的坑（照实写）**　第一版是「所有参数一律 `{"type": "string"}`」，模型只能靠描述去猜 int / 枚举 / 可选。改成注解推导后又踩了一个：本模块开了 `from __future__ import annotations`，签名里的注解是**字符串**，`inspect.signature` 拿到的 `p.annotation` 是 `"int"` 而不是 `int`，查表永远查不到、全部退回 string。必须用 `typing.get_type_hints(fn)` 还原成真类型对象，并且解析失败时不能炸，只退化成 string。

**执行前校验（已做）**　`validate_args()` 用 `pydantic.create_model` 按同一份 schema 动态建模型：必填给 `...`、可选给 `None` 默认、`enum` 转 `Literal`、`extra="forbid"`。模型给的 JSON 先过模型再进函数，失败抛 `参数不合法 <字段>: <原因>`，由循环转成 `role=tool` 的 `error:` 回喂，不需要新机制。它不是新加一个给模型看的 tool —— schema 是给模型的说明书，pydantic 是代码里执行前的守门人。

**这一层又踩了两个坑**　① 校验后把「可选参数的 None」原样 `**args` 传下去，会覆盖掉函数自己的默认值，必须过滤掉值为 None 的键；② pydantic 默认**忽略多余字段**，模型把 `top_k` 拼成 `top\_kk` 会被静默丢掉、函数照样跑，必须 `extra="forbid"` 才报错。

**仍然没做（照实写）**　`dict[str, X]` 只标 `object` 不细化 properties；没有取值范围约束（`top_k` 传 -1 类型合法，只是语义不对），要加得在 schema 里带 `minimum/maximum` 再映射到 `Field(ge=, le=)`。

**理想方案**



* 类型从注解推导：`int → integer`、`Optional → 不必填`、`Literal → enum`；入参用 pydantic 校验后再执行。

* 工具分三类治理：只读 / 幂等写 / 非幂等写。非幂等写必须带 idempotency key，否则重试就是事故。

* 每个工具单独超时，重试策略只给只读工具。

* 工具数量过阈值（经验 15–20）时改两段式：先选工具族，再选具体工具，否则 schema 本身就把上下文吃满了。

## 3. 上下文与提示分层

**S 场景**　system prompt 一锅炖，改评分规则会顺带改变出题行为。

**T 任务**　角色 / 规则 / 知识三层分离，能单独改、能回归验证。

**A 动作**



* `SYSTEM` 只写面试官人设与节奏（每轮只做一件事）

* 打分规则单独放 `app/prompts.py` 的 `SCORING_SYSTEM`；报告另有 `REPORT_SYSTEM`

* 检索到的要点以「以下是参考要点，不是候选人的话」的形式注入打分 prompt，不入 system

* **源码**　[app/main.py:29-33](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py#L29-L33)（人设 `SYSTEM`）、[app/prompts.py:10-40](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#L10-L40)（`SCORING_SYSTEM`，第 41 行起是 `REPORT_SYSTEM`）

**R 结果**　改评分口径（提交 `85f5ba7`）没有改变出题与追问行为；Q8-good 4.10 → 4.50，其余题排序不变。

**理想方案**



* 三层加版本号：prompt 进 git，评测前后跑同一批 golden，改动可归因。

* 固定内容放前、易变内容放后，提高服务端缓存命中，降低成本。

* 知识注入必须带边界声明，否则模型会拿要点冒充候选人的回答，等于替候选人答题。



***

# 二、判断层：谁做判断，判断得准不准

## 4. 模型判断 vs 代码裁决

**S 场景**　模型给的分数和布尔判断不可复现，而且会「自我一致地错」—— 编得越完整越像有工程能力。

**T 任务**　结果可解释、可复现、换模型口径不变。

**A 动作**



* 模型只负责语义判断：六维分 + evidence + `fact_mismatch`

* 总分由 `app/scoring.py` 按 .30/.20/.20/.10/.10/.10 重算，模型的 `total` 直接丢弃

* 六维 clamp 0–5；`fact_mismatch: true` 时 `FACT_MISMATCH_CAP = 2.0` 强制封顶，同时记 `cap_applied`

* **源码**　[app/scoring.py:74-105](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#L74-L105)（`score_answer` + 重算 + 封顶，常量在第 23 行）、[app/prompts.py:10-40](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#L10-L40)（`fact_mismatch` 字段的来源）

**R 结果**　Q10 编造「丢 3 万单、回补两小时」的回答先 2.40，prompt 加「事实不符不加分」无效；加「correctness ≤ 2」只降到 1.80（engineering 仍有保底 3）；代码封顶后稳定压在 2.00 以下，排序恢复正确。

**理想方案**



* 分工写死：**模型管语义（命中 / 未命中、事实是否相符），代码管算术与边界**。

* 凡「绝不允许超过 X」的规则，prompt 只做引导，代码必须再夹一遍。

* 封顶带原因码（**已做**）：`cap_reason = "fact_mismatch:4.10->2.0"`，同时写进打分结果与 trace 的 `score` 事件——能回答「为什么这题只有 2.0」。用字符串不用 bool，是为以后加「回答为空 / 答非所问」也能区分。

* 争议样本能直接回放模型原话（trace 里存 raw）——**仍未做**。

* 六维分之间加一致性检查：correctness 低而 engineering 高的组合要么是真的工程强，要么是模型在圆场，需要二次判定。

## 5. 评测集：先保证样本对，再谈打分器

**S 场景**　排序倒挂时，第一反应总是改 prompt；五次里有五次，错在样本。

**T 任务**　判断「错在样本还是打分器」要有可执行顺序，不能靠感觉。

**A 动作**



* 15 条 = 5 题 × good/mid/bad，`eval/build_golden.py` 的 `ANSWERS` 为唯一真源，改完必须重生成 `eval/golden.jsonl`

* 分档按「信息完整度 + 机制正确性」，不按长度；bad 档刻意写成「比 mid 更专业」的对抗样本

* 倒挂时先读 `covered / missing`：模型把关键点列进 missing，说明样本根本没答到题

* 每个数字指回事实源（`/tmp/deck_src.txt` 的演示稿原文）

* **源码**　[eval/build\_golden.py:20](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/build_golden.py#L20)（`ANSWERS` 唯一真源）、[eval/golden.jsonl](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/golden.jsonl)（15 条产物）、[eval/run.py:74-95](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/run.py#L74-L95)（跑法与排序表）

**R 结果**　5 次倒挂全部定位到样本：Q10 根因写错（说成内存泄漏）、Q3 口径不符（删除缓存 vs 写前校验）、Q8-good 答非所问、Q8-mid 三处与事实相反（checkpointer / 权限靠 prompt / 做了重排）、Q8「4 节点 / 5 步」两个数混说。修完后 5/5 排序正确，平均分差 2.84。

**理想方案**



* 倒挂排查顺序固定：读 missing → 核事实源 → 修样本 → 再动 prompt → 最后动权重。顺序反了就是在给错误样本调参。

* 每题 ≥2 条独立写法才算稳定；当前 1 条 / 档，是明确的缺口。

* 单独建「事实源」文件，样本里每个数字可指到具体一行，避免两处口径打架。

* 评测指标分开看：排序一致性（是否 good>mid>bad）与刻度稳定性（同答案重复跑抖动）。本项目排序稳、刻度抖 ±0.3–0.8，报告里只敢写「分档经本人确认」，不敢写「分数已校准」。

## 6. 检索：便宜的召回也能有净收益，前提是零调用

**S 场景**　想让打分有固定参照，又不想为每道题多花一次调用。

**T 任务**　召回准确、可解释，且知识库缺失时不能拖垮主链路。

**A 动作**



* 把 `my/best-answers.md` 切成按题知识块（`app/kb.py`），字段用「一句话 / 讲法 / 关键数字 / 会被追问 / 边界」

* 本地 IDF 召回：中文双字 + 英文词，主题词命中额外 +2.5，低于阈值 3.0 返回 None

* 召回结果只作「覆盖度对照」注入打分 prompt，不做答案

* 召回为空 → 退化成「原行为」，即**接检索之前的那条链路**：不往打分 prompt 里插任何知识块，其余（六维打分、代码重算、封顶、trace）一字不变。所以关检索与接检索两次跑，同一份答案的差异只可能来自那段知识块，对照才成立

* **源码**　[app/kb.py:140-166](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#L140-L166)（`retrieve` + `_retrieve`，+2.5 加权在第 158 行、阈值在第 161 行）、[app/kb.py:26-104](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#L26-L104)（字段解析）

**R 结果**　10 题召回 10/10，召回分 39–63；同一份错答案（Q1 CAS 版）3.90 → 2.40；关检索与接检索对比，差档平均 2.16 → 1.76（Q1 降幅最大 3.30 → 2.00）。

**踩过的两个静默 bug**　都是不报错，只表现为「prompt 里塞了乱码 / 少了整段」：



* 去 Markdown 项目符号的正则写成 `^[-*]\s*`，把 `**加粗**` 的首个星号吃掉，标签变 `*模型与分流**` → 改 `^[-*]\s+`

* 小标题带括注（`**边界（最重要）**`）时用原始串匹配字段名，整段静默漏掉 → 先剥括号再匹配

**理想方案**



* 先做「零额外调用的本地召回」，确认有净收益再上向量库；向量化的收益必须做 A/B 才能立项。

* 召回必须可被否证：trace 记 `hit / qid / score / points`，阈值调整有数据支撑。

* 用一个最小的题把「否证」和「对照」说清楚。假设题目是「怎么保证订单状态机不重复推进」，知识库里这题的要点三条：

    1. 幂等：用 `request_id` / 业务主键去重，不是靠重试
    2. 并发控制：状态推进用 CAS 或单飞，不是分布式锁
    3. 兜底：对账 + 补偿，处理已经推进错的那部分

  候选人的回答只有一句「加了重试和日志，基本不会重复」。同一份输入跑两次：

| 链路 | prompt 里有什么 | 模型的处境 | 实测结果 |
| --- | --- | --- | --- |
| 关检索（原行为） | 题目 + 回答 + 六维 rubric | 没有清单，只能凭感觉判断「讲得够不够」 | `covered=4` / `missing=5`，**总分 3.30** |
| 接检索 | 上面这些 +【参考要点】三条 | 三条逐条比对，一条都没对上 | `covered=2` / `missing=7`，**总分 2.00** |

  两份分数都来自 `eval/last-off-t0.json` 与 `eval/last-final5.json` 的真实运行，不是假设值。

* 同一份回答差 1.30 分，差别不在「模型变严了」，而在**它手上有了尺子**：原来只能说「好像还行」，现在能指出「幂等、并发、兜底，三条全没讲」。

* 「可被否证」= 这个结论允许你拿证据推翻它。三条具体用法：

    - 觉得 2.00 冤枉 → 打开 `eval/trace-*.jsonl` 看 `missing` 那几条要点，回读回答原文逐条核对：候选人真没说，2.00 站得住；说了却没进 `covered`，那是归一化的 bug，结论当场被推翻。
    - 觉得阈值定错 → 把 `kb.retrieve(min_score=3.0)` 提到 **60**：Q1 实测召回分 57.76（10 题区间 39–63），于是这题 `hit=false`、不注入要点，分数退回关检索的 3.30。阈值高低**跑一次就看到后果**，不用口头争论。
    - 关检索那次的 `covered=4` 不可比：它没有固定清单，那四条是模型临时想的，第二次跑可能变成三条。**只有带清单的那次才有可复现的对照**。

* 解析层加断言：**解析出 0 个字段直接报错**，别让错误漂到下游。

* 召回做对照不做答案。要点清单只出现在 prompt 的【参考要点（仅作覆盖度对照）】一段，并明确「只判断说到没说到，不要替候选人补话」。

    - 正确的用法：清单当尺子。候选人说「用分布式锁」，清单写「CAS / 单飞」→ `missing` 点名这一条，该扣就扣。
    - 错误的用法：清单当答案。模型会按「答案长什么样」给分，候选人说得含糊但撞到几个关键词也能拿高分，评分变成抄答案。
    - 一条能自检的红线：**结果里不该出现候选人口中没说过的话**。`evidence` 要求引用回答原文，某条 evidence 在回答里搜不到，就说明模型已经拿要点当答案用了——这是 bug，不是风格问题。



***

# 三、运营层：成本、观测、状态

## 7. 预算：所有调用从同一个口进

**S 场景**　一次评测 15 次调用、一场面试 11 次，跑飞一次就是真金白银，而且用户反复强调过这一点。

**T 任务**　任何路径都不能无限调用，超限要有明确失败而不是默默烧钱。

**A 动作**



* `BudgetedLLM` 包一层，超限抛 `BudgetExceeded`

* 口径写死：`ask` 1 次、`prep` 1 次、`chat` 8 次、`interview` 题数 + 1

* 本次补漏发现：`ask / prep / chat` 原本直接 new 裸 `RealLLM`，**完全没有护栏**

* **源码**　[app/budget.py:18-68](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py#L18-L68)（`BudgetedLLM` 与 `BudgetExceeded`）、[app/main.py:79-160](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py#L79-L160)（四条命令的预算口径）、[tests/test\_guards.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_guards.py)（绕过检查）

**R 结果**　三条路径补上包装层后，评测 15/15 次精确用满、不超；面试按题数线性可预算。

**理想方案**



* 一个进程一个 LLM factory，业务代码禁止直接实例化 client；CI 加一条 grep 检查绕过。具体是：全仓只有 `app/main.py` 的 `_make_real()` 和 `app/web.py` 的 `_make_llm()` 允许出现 `RealLLM(`，且必须写成 `BudgetedLLM(RealLLM(), max_calls=...)`同一行；其他任何模块自己 new 一个，就等于绕过预算、直接烧钱。这条约定不能只写在文档里 ——**已落成&#x20;**`tests/test_guards.py`**&#x20;的一条断言**：扫描 `app/*.py`，出现 `RealLLM(` 而同一行没有 `BudgetedLLM(` 就失败并点名文件行号。这条测试第一次跑就抓到了 `app/web.py`，确认它其实包了预算才算通过

* 预算分三层：单请求 / 单会话 / 单日；超限要有降级策略（降模型、少追问、跳过非关键步骤），而不是直接抛错给用户。

* 重试也计数 —— 重试同样是钱。把重试次数并入同一预算池。

* 预算用尽时输出「已完成的结论 + 未完成的原因」，别让用户只看到异常。

## 8. 可观测：trace 落在同一个收口

**S 场景**　最终分数是「模型判断 + 代码重算 + 封顶」三段拼出来的，只记最终分无法倒查，调参全靠玄学。

**T 任务**　一次问答能复现「为什么是这个分」。

**A 动作**



* `app/trace.py` 定义四类事件：`llm / tool / kb / score`

* 挂在四处收口：`BudgetedLLM.chat()`（唯一 LLM 出入口）、工具分发、`kb.retrieve()`、`score_answer()`

* 隐私处理：只落「角色 + 字符数 + 前 80 字」，不把简历原文整段写进日志

* 写失败自动 `enabled=False` 静默降级，观测绝不影响面试

* 默认 `my/trace.jsonl`（不入库）；`--trace` 改路径，`--no-trace` 关掉；评测写 `eval/trace-<tag>.jsonl`

* `request()` 上下文管理器：进入生成 `ask-3f9a2b1c` 这类 `request_id`，退出恢复上一个（可嵌套）；`emit()` 每行都带它

* 接线在四个入口：CLI 的 `chat / prep / ask`、整场面试的每题与每次追问、Web 的单次提交、评测的每个 case

* **源码**　[app/trace.py:27-185](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/trace.py#L27-L185)（`Tracer` + 四类事件 + `request()`/`configure`/`set_scene`）；四处收口 [app/budget.py:31-68](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py#L31-L68)、[app/agent.py:57-79](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py#L57-L79)、[app/kb.py:140](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#L140)、[app/scoring.py:134](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#L134)；测试 [tests/test\_trace.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_trace.py)

**R 结果**　真实单题实测三段齐全且同属一条父链（`request_id=ask-c46622ae`）：`kb` 命中 Q1 召回分 39.6 → `llm` 一次调用 → `score` total 0.9、`cap_applied=false`、`cap_reason=""`。三道 grep 就能把一次问答的全部事件取出来，不再靠 ts 猜先后。

**理想方案**



* 一条 trace 一个 `request_id`（**已做**）：每题 / 每次追问 / 每个评测 case 各一个窗口，`kb → llm → tool → score` 共用同一个 id。

* 全量落本地、按需上传；敏感字段用白名单而不是黑名单。

* 把 trace 聚合成指标：p50/p95 延迟、各工具失败率、封顶触发率、召回命中率，才是可运营的东西。

* 顺序原则：**先有 trace 再调优**。没有 trace 的 prompt 调整，成功与失败都无法归因。

## 9. 会话与状态

**S 场景**　模式 A 一问一答需要跨题记忆与整场报告，但每题又要能独立复现。

**T 任务**　状态落盘、可回放、不串题。

**A 动作**



* SQLite 两张表：`sessions` / `turns`，CLI 与 Web 共用同一个 store

* 报告从 DB 重新生成，不从内存状态拼

* 低分（<3.0）且题库带追问时自动追问，`--max-followups` 限次并计入预算

* **源码**　[app/session.py:12-90](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/session.py#L12-L90)（`SessionStore` 两张表）、[app/interview.py:67-85](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/interview.py#L67-L85)（追问判定与预算扣减）

**R 结果**　终端 10 题 + Web 1 题各成一场，整场报告 157 行、均分 3.01，可随时重生成。

**理想方案**



* `turns` 表补 `qid`（**已做**）：`PRAGMA table_info` 检查后 `ALTER TABLE ADD COLUMN`，老库 11 行照常可读、历史行 qid 为空串；写入侧由 `item["qid"]` 带上，`turns()` 一并返回。

* 状态 schema 带 version，升级要写迁移；单文件前端存档尤其容易踩这个坑。

* 报告、trace、DB 共享同一个 `request_id` —— **仍未做**：现在 qid 已进 DB 与 trace 的 `kb/score` 事件，但报告还是靠顺序与 id 关联，没有 request\_id。



***

# 四、产品层：评分口径

## 10. 诚实应当被奖励

**S 场景**　第一版 rubric 把「承认没做重排 / 没做超时幂等」判成深度不足，等于奖励吹牛 —— 而面试真正想筛的是「知道自己边界在哪」。

**T 任务**　让「讲清取舍 + 主动认边界」拿高分，同时不放过把没做的说成做了的人。

**A 动作**



* `SCORING_SYSTEM` 新增一节「承认未做 / 缺口的计分规则」：


  * 要点里写明的缺口，照实说算命中，不算漏

  * `depth` 只看是否讲清「做了什么、为什么这么取舍、缺口代价」，不因承认没做而扣

  * `boundary` 主动认边界给 4–5；把没做的说成做了降到 2 以下

  * 反向同样算事实不符（已做的说成没做，也是错）

* 这条口径由用户拍板，写进文档留痕，不靠模型自己领会

* **源码**　[app/prompts.py:10-40](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#L10-L40)（「承认未做 / 缺口」整节）、[app/scoring.py:23](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#L23)（封顶常量）

**R 结果**　Q8-good 4.10 → 4.50，排序不变；同时连带查出两个不干净的 mid 样本（Q3、Q8），修后 2.70 / 2.60。

**理想方案**



* rubric 每加一条，先在 golden set 上看「谁涨谁跌」；没有差异化效果就别加，否则只是自我安慰。

* 口径类规则必须由人拍板，模型只能提议；每次变更留「规则 → 影响样本 → 分数变化」三列记录。

* 评分要闭环到话术：报告给「这题下一步怎么改口」，只给分数对求职者没有价值（这也是 P5 要补的部分）。



***

# 五、成熟度自评：现状 vs 理想



| 维度 | 现状 | 理想 | 差距 | 可做性 |
| --- | --- | --- | --- | --- |
| 循环收敛 | max\_iter + 三条 `stop_reason`（answered / max\_iter / repeated\_tool\_call）+ 重复调用强制停 | 断点续跑（messages + 工具结果落盘） | 中 | 断点续跑 **中等**（要设计落盘格式与去重） |
| 工具治理 | 注解推导 schema + pydantic 执行前校验（类型 / 枚举 / 必填 / 多余字段）+ 防绕过测试 | 取值范围约束 + 单工具超时 + 幂等键 | 中 | 取值范围 **可做·低成本**（schema 补 minimum/maximum）；超时 **中等**；幂等键 **暂不做**（当前工具全只读） |
| 判断分层 | 代码重算总分 + 事实不符封顶 + `cap\_reason`（带封顶前分数） | 模型原话留痕 + 六维一致性检查 | 低 | 原话留痕 **中等**（涉及隐私裁剪）；一致性检查 **需样本** |
| 评测 | 5 题 × 3 档，每档 1 条 | 10 题 × 每档 2 条 + 多票取中位 | 高 | **可做但要预算**：扩样本约 30 次调用，多票再 ×3 |
| 检索 | 本地 IDF，零额外调用 | 同义扩展 + 阈值回归 + 向量 A/B | 低 | 阈值回归 **可做·低成本**（trace 已有数据）；同义扩展 **中等**；向量 A/B **需调用预算** |
| 预算 | 单一收口，四命令有上限 | 三层预算 + 超限降级策略 | 中 | 三层预算 **中等**；降级策略 **需先定产品口径**（降到哪个模型、少几道题） |
| 观测 | 四类事件 JSONL + `request\_id` 父链（CLI / Web / 面试每题 / 评测每 case 全覆盖） | 指标聚合（延迟 p50/p95、工具失败率、封顶率） | 中 | 指标聚合 **中等** |
| 状态 | SQLite 两表 + `qid` 列（老库自动 ALTER 迁移） | schema 版本号 + 报告与 trace 共用 request\_id | 低 | 版本号 **中等**；报告带 request\_id **可做·低成本**（多写一列） |
| 口径 | 诚实加分已落地并验证 | 规则变更与 golden 联动台帐 | 低 | **可做·低成本**（一张表 + 每次改口径补一行） |

**五项低成本已全部落地（2026-11-10，一次做完）**：补 qid → `stop\_reason` 枚举 + 重复调用检测 → `cap\_reason` → trace 加 `request\_id` 父链 → 工具参数校验。改动落在 `app/agent.py` / `session.py` / `scoring.py` / `trace.py` / `tools.py` / `interview.py` / `web.py` / `main.py` / `eval/run.py`，新增 `tests/test\_hardening.py` 14 条，全量 **42 条**测试通过。

**要先算预算再动的**：扩样本（10 题 × 每档 2 条）与多票取中位，两者都直接翻调用次数，按你反复强调的「控制调用频次」，得先确认额度再开跑。

**可以明确不做的**：幂等键——本项目四个工具全是只读或回显，没有写操作，加了也只是纸面治理。

这一时期还新增了三条原来没写的工程问题：停止语义与可恢复（条目 1）、工具 schema 类型与幂等治理（条目 2）、状态主键与 request\_id 对齐（条目 8、9）。
