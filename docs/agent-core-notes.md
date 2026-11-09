# Agent 工程实践笔记（STAR 版）

按「场景 → 任务 → 动作 → 结果 → 理想方案」组织。每条都落在本仓库的真实实现与真实返工上，理想方案给的是成熟做法与升级时机，不是空话。

源码链接指向 GitHub `main` 分支，行号对应本文件所在提交；同时附仓库内路径，离线也能按行定位。

项目基线：18 个模块 1734 行 Python，手写 ReAct loop（`app/agent.py` 83 行）+ 工具注册表 + 预算层 + 本地检索 + 六维打分 + SQLite 会话 + Web 界面。所有结论可用 git log 与 `eval/README.md` 复核。



***

# 一、执行层：循环、工具、上下文

## 1. 核心循环：停止条件要能被证明

**S 场景**　Agent 最大的工程风险不是答得不好，是不收敛 —— 模型反复调工具、空转、最后什么都不返回。

**T 任务**　循环必须有硬边界：任何输入都能退出，且退出时能说清为什么停。

**A 动作**



* 手写 `run_agent(llm, system, user_msg, schemas, impls, max_iter=8)`

* 每轮先追加 assistant 消息（含 `tool_calls` 原文），再判断：**没有 tool\_calls 就返回 final**

* 有 tool\_calls 则逐个执行，结果以 `role=tool` 回喂；工具抛异常时转成 `error: ...` 回喂，不中断循环

* 到 `max_iter` 返回 `iterations=max_iter, final=None`
* **源码**　[`app/agent.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py#LL15-L83)　仓库内 `app/agent.py:15-83`；[`tests/test_offline.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_offline.py)（循环与工具分发测试）

**R 结果**　stub 演示 3 轮内收敛；真实单题 2 次调用完成；离线测试 20 条覆盖循环与工具分发。

**理想方案**



* 把停止原因做成枚举：`answered / no_tool_call / max_iter / budget_exceeded / error`。现在统一返回 `None`，让上层猜，这是可观测性的第一处漏。

* 迭代上限和预算上限是两个维度（调用次数 vs 时间 /token），两道都要有，不能互相替代。

* 加「重复调用检测」：同一工具同一参数连续两次，注入提示或强制停，比单纯加 max\_iter 有效。

* 长任务要可恢复：每轮把 messages + 已完成工具结果落盘，断点重跑不重复付费。

## 2. 工具注册与 schema：别让模型猜参数类型

**S 场景**　工具一多，绝大多数失败不是模型不会做，是参数不合法。

**T 任务**　schema 稳定、与实现同源、失败可诊断可回喂。

**A 动作**



* `@tool` 装饰器注册进 `TOOLS` 字典，重名直接抛错（防止静默覆盖）

* `tool_schemas()` 用 `inspect.signature` 生成 OpenAI function schema，docstring 第一行当 description

* 业务工具两个：`next_question`（题库轮换）、`save_feedback`
* **源码**　[`app/tools.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py#LL22-L56)（`@tool` 注册 + schema 生成）、[`app/tools.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py#LL57-L68)（两个业务工具）　仓库内 `app/tools.py:22-68`

**R 结果**　demo 全流程工具调度跑通，schema 与实现零漂移。

**已知局限（照实写）**　所有参数一律生成 `{"type": "string"}`，类型没从注解推导，模型只能靠描述去猜 int / 枚举 / 可选。

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
* **源码**　[`app/main.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py#LL29-L33)（人设 `SYSTEM`）、[`app/prompts.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#LL10)（`SCORING_SYSTEM`）与第 41 行 `REPORT_SYSTEM`　仓库内 `app/main.py:29-33`、`app/prompts.py:10`

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
* **源码**　[`app/scoring.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#LL74-L105)（`score_answer` + 重算 + 封顶；第 23 行是常量）、[`app/prompts.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#LL10-L40)（`fact_mismatch` 字段来源）　仓库内 `app/scoring.py:74-105`

**R 结果**　Q10 编造「丢 3 万单、回补两小时」的回答先 2.40，prompt 加「事实不符不加分」无效；加「correctness ≤ 2」只降到 1.80（engineering 仍有保底 3）；代码封顶后稳定压在 2.00 以下，排序恢复正确。

**理想方案**



* 分工写死：**模型管语义（命中 / 未命中、事实是否相符），代码管算术与边界**。

* 凡「绝不允许超过 X」的规则，prompt 只做引导，代码必须再夹一遍。

* 封顶要带原因码 `cap_reason`，不是一个 bool；争议样本能直接回放模型原话（trace 里存 raw）。

* 六维分之间加一致性检查：correctness 低而 engineering 高的组合要么是真的工程强，要么是模型在圆场，需要二次判定。

## 5. 评测集：先保证样本对，再谈打分器

**S 场景**　排序倒挂时，第一反应总是改 prompt；五次里有五次，错在样本。

**T 任务**　判断「错在样本还是打分器」要有可执行顺序，不能靠感觉。

**A 动作**



* 15 条 = 5 题 × good/mid/bad，`eval/build_golden.py` 的 `ANSWERS` 为唯一真源，改完必须重生成 `eval/golden.jsonl`

* 分档按「信息完整度 + 机制正确性」，不按长度；bad 档刻意写成「比 mid 更专业」的对抗样本

* 倒挂时先读 `covered / missing`：模型把关键点列进 missing，说明样本根本没答到题

* 每个数字指回事实源（`/tmp/deck_src.txt` 的演示稿原文）
* **源码**　[`eval/build_golden.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/build_golden.py#LL20)（`ANSWERS` 唯一真源）、[`eval/golden.jsonl`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/golden.jsonl)（15 条产物）、[`eval/run.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/run.py#LL74-L95)（跑法与排序表）　仓库内 `eval/build_golden.py:20`

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

* 召回为空 → 退化成原行为，链路与没有检索时完全一致
* **源码**　[`app/kb.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#LL140-L166)（`retrieve` 外层 + `_retrieve` IDF 打分；第 158 行 +2.5 加权、第 161 行阈值 3.0）、[`app/kb.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#LL26-L104)（字段解析，两个静默 bug 的修复处）　仓库内 `app/kb.py:140-166`

**R 结果**　10 题召回 10/10，召回分 39–63；同一份错答案（Q1 CAS 版）3.90 → 2.40；关检索与接检索对比，差档平均 2.16 → 1.76（Q1 降幅最大 3.30 → 2.00）。

**踩过的两个静默 bug**　都是不报错，只表现为「prompt 里塞了乱码 / 少了整段」：



* 去 Markdown 项目符号的正则写成 `^[-*]\s*`，把 `**加粗**` 的首个星号吃掉，标签变 `*模型与分流**` → 改 `^[-*]\s+`

* 小标题带括注（`**边界（最重要）**`）时用原始串匹配字段名，整段静默漏掉 → 先剥括号再匹配

**理想方案**



* 先做「零额外调用的本地召回」，确认有净收益再上向量库；向量化的收益必须做 A/B 才能立项。

* 召回必须可被否证：trace 记 `hit / qid / score / points`，阈值调整有数据支撑。

* 解析层加断言：**解析出 0 个字段直接报错**，别让错误漂到下游。

* 召回做对照不做答案；否则模型会拿要点冒充候选人，评分变成抄答案。



***

# 三、运营层：成本、观测、状态

## 7. 预算：所有调用从同一个口进

**S 场景**　一次评测 15 次调用、一场面试 11 次，跑飞一次就是真金白银，而且用户反复强调过这一点。

**T 任务**　任何路径都不能无限调用，超限要有明确失败而不是默默烧钱。

**A 动作**



* `BudgetedLLM` 包一层，超限抛 `BudgetExceeded`

* 口径写死：`ask` 1 次、`prep` 1 次、`chat` 8 次、`interview` 题数 + 1

* 本次补漏发现：`ask / prep / chat` 原本直接 new 裸 `RealLLM`，**完全没有护栏**
* **源码**　[`app/budget.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py#LL18-L68)（`BudgetedLLM` 与 `BudgetExceeded`）、[`app/main.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py#LL79-L160)（四条命令的预算口径）　仓库内 `app/budget.py:18-68`

**R 结果**　三条路径补上包装层后，评测 15/15 次精确用满、不超；面试按题数线性可预算。

**理想方案**



* 一个进程一个 LLM factory，业务代码禁止直接实例化 client；CI 加一条 grep 检查绕过。

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
* **源码**　[`app/trace.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/trace.py#LL27-L172)（`Tracer` + 四类事件 + `configure` / `set_scene`），四处收口：[`budget.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py#LL31-L68)、[`agent.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py#LL57-L79)、[`kb.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py#LL140)、[`scoring.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#LL134)　测试：[`tests/test_trace.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_trace.py)　仓库内 `app/trace.py:27-172`

**R 结果**　真实单题实测三段齐全：`kb` 命中 Q1 召回分 20.15/7 条要点 → `llm` deepseek-chat temp=0、prompt 2131 字符、3096ms、成功 → `score` total 2.5、cap=False、covered 2 /missing 6。Web、CLI、评测三条路径自动覆盖。

**理想方案**



* 一条 trace 一个 `request_id`，追问时带父链，能把「这题 + 追问」串成一条会话视图。

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
* **源码**　[`app/session.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/session.py#LL12-L90)（`SessionStore` 两张表）、[`app/interview.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/interview.py#LL67-L85)（追问判定与预算扣减）　仓库内 `app/session.py:12-90`

**R 结果**　终端 10 题 + Web 1 题各成一场，整场报告 157 行、均分 3.01，可随时重生成。

**理想方案**



* `turns` 表补 `qid` 主键 —— 现在没有，等于报告只能按顺序关联，题目一改就对不上。

* 状态 schema 带 version，升级要写迁移；单文件前端存档尤其容易踩这个坑。

* 报告、trace、DB 共享同一个 `request_id`，三处能对齐。



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
* **源码**　[`app/prompts.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py#LL10-L40)（「承认未做/缺口」整节）、[`scoring.py`](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py#LL23)（封顶常量）　仓库内 `app/prompts.py:10-40`

**R 结果**　Q8-good 4.10 → 4.50，排序不变；同时连带查出两个不干净的 mid 样本（Q3、Q8），修后 2.70 / 2.60。

**理想方案**



* rubric 每加一条，先在 golden set 上看「谁涨谁跌」；没有差异化效果就别加，否则只是自我安慰。

* 口径类规则必须由人拍板，模型只能提议；每次变更留「规则 → 影响样本 → 分数变化」三列记录。

* 评分要闭环到话术：报告给「这题下一步怎么改口」，只给分数对求职者没有价值（这也是 P5 要补的部分）。



***

# 五、成熟度自评：现状 vs 理想



| 维度   | 现状                          | 理想                             | 差距 |
| ---- | --------------------------- | ------------------------------ | -- |
| 循环收敛 | max\_iter + 无 tool\_call 停止 | stop\_reason 枚举 + 重复调用检测 + 可恢复 | 中  |
| 工具治理 | schema 同源，但参数全 string       | 注解推导类型 + 超时 + 幂等键              | 高  |
| 判断分层 | 代码重算总分 + 事实不符封顶             | 加 cap\_reason + 模型原话留痕         | 低  |
| 评测   | 5 题 × 3 档，每档 1 条            | 10 题 × 每档 2 条 + 多票取中位          | 高  |
| 检索   | 本地 IDF，零额外调用                | 同义扩展 + 阈值回归 + 向量 A/B           | 低  |
| 预算   | 单一收口，四命令有上限                 | 三层预算 + 超限降级策略                  | 中  |
| 观测   | 四类事件 JSONL                  | request\_id 父链 + 指标聚合          | 中  |
| 状态   | SQLite 两表，可回放               | 补 qid + schema 版本迁移            | 低  |
| 口径   | 诚实加分已落地并验证                  | 规则变更与 golden 联动台帐              | 低  |



***

# 六、与原 13 条踩坑的对应



| 原条目          | 现在的位置       |
| ------------ | ----------- |
| 1 总分不能让模型给   | 条目 4        |
| 2 约束要落代码     | 条目 4        |
| 3 先怀疑样本      | 条目 5        |
| 4 伪具体性       | 条目 4 + 条目 5 |
| 5 good 要答到题面 | 条目 5        |
| 6 两个数混说      | 条目 5        |
| 7 温度不是全部抖动源  | 条目 5        |
| 8 关键词召回零调用   | 条目 6        |
| 9 解析静默失败     | 条目 6        |
| 10 预算单一收口    | 条目 7        |
| 11 观测同一收口    | 条目 8        |
| 12 承认缺口该加分   | 条目 10       |
| 13 先验证能分好坏   | 条目 5        |

新增了三条原来没写的工程问题：停止语义与可恢复（条目 1）、工具 schema 类型与幂等治理（条目 2）、状态主键与 request\_id 对齐（条目 8、9）。