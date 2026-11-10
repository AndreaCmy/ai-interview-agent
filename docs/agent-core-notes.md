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

* **源码**　[app/agent.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)[run\_agent()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)（`run_agent` 主循环）、[tests/test\_offline.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_offline.py)（循环与工具分发测试）

**R 结果**　stub 演示 3 轮内收敛；真实单题 2 次调用完成；单测覆盖三条出口（`answered` / `max_iter` / `repeated_tool_call`）与「重复调用只执行 1 次」，全过。

**理想方案**



* 停止原因做成枚举（**已做**）：`answered / max_iter / repeated_tool_call`；`budget_exceeded` 走异常、不混进返回值，`error` 类归到工具级而不是循环级 —— 一次工具失败不该等同于整轮失败。

* 迭代上限和预算上限是两个维度（调用次数 vs 时间 /token），两道都要有，不能互相替代。

* 重复调用检测（**已做**）：同一工具同一参数第二次出现就注入提示，累计 2 次强制停。只加 max\_iter 治不了原地打转 —— 模型会用同样的调用把 8 轮烧完。

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

* **源码**　[app/tools.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)[schema\_for()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)（类型推导 + 参数说明解析 + `schema_for`）、[app/tools.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)[tool()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/tools.py)（`@tool` 注册与两个业务工具）、[tests/test\_guards.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_guards.py)（schema 类型测试）

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

* **源码**　[app/main.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)[SYSTEM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)（人设 `SYSTEM`）、[app/prompts.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[SCORING\_SYSTEM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)（`SCORING_SYSTEM`；`REPORT_SYSTEM` 在同文件）

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

* **源码**　[app/scoring.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[score\_answer()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)（`score_answer()` + 重算 + 封顶；封顶常量 `FACT_MISMATCH_CAP` 在同文件）、[app/prompts.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[SCORING\_SYSTEM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)（`fact_mismatch` 字段的来源）

**R 结果**　Q10 编造「丢 3 万单、回补两小时」的回答先 2.40，prompt 加「事实不符不加分」无效；加「correctness ≤ 2」只降到 1.80（engineering 仍有保底 3）；代码封顶后稳定压在 2.00 以下，排序恢复正确。

**理想方案**



* 分工写死：**模型管语义（命中 / 未命中、事实是否相符），代码管算术与边界**。

* 凡「绝不允许超过 X」的规则，prompt 只做引导，代码必须再夹一遍。

* 封顶带原因码（**已做**）：`cap_reason = "fact_mismatch:4.10->2.0"`，同时写进打分结果与 trace 的 `score` 事件 —— 能回答「为什么这题只有 2.0」。用字符串不用 bool，是为以后加「回答为空 / 答非所问」也能区分。

* 争议样本能直接回放模型原话（trace 里存 raw）——**仍未做**。

* 六维分之间加一致性检查：correctness 低而 engineering 高的组合要么是真的工程强，要么是模型在圆场，需要二次判定。

## 5. 评测集：先保证样本对，再谈打分器

**S 场景**　排序倒挂时，第一反应总是改 prompt；五次里有五次，错在样本。

**T 任务**　判断「错在样本还是打分器」要有可执行顺序，不能靠感觉。

**A 动作**



* **60 条 = 10 题 × 每档 2 条**（2026-11-10 补满）：同题同档两条刻意写不同侧重（一条「链路 + 机制全覆盖」，一条「先划边界 / 先讲代价」），id 用后缀 `b` 区分。`eval/build_golden.py` 的 `ANSWERS` 为唯一真源，改完必须重生成 `eval/golden.jsonl`

* 分档按「信息完整度 + 机制正确性」，不按长度；bad 档刻意写成「比 mid 更专业」的对抗样本

* 倒挂时先读 `covered / missing`：模型把关键点列进 missing，说明样本根本没答到题

* 每个数字指回事实源（`/tmp/deck_src.txt` 的演示稿原文）

* **源码**　[eval/build\_golden.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/build_golden.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/build_golden.py)[ANSWERS](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/build_golden.py)（`ANSWERS` 唯一真源）、[eval/golden.jsonl](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/golden.jsonl)（60 条产物）、[eval/run.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/run.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/run.py)[main()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/eval/run.py)（跑法与排序表）

**R 结果（补满 60 条）**　新增 15 条（Q1/Q3/Q5/Q8/Q10 各 3 档第 2 条）先跑出 **4/5 排序正确**：Q1 倒挂（`Q1-midb` 1.60 < `Q1-badb` 2.00）。

按「倒挂先查样本」的顺序核对，根因是 `Q1-midb` 写得太空（只说 "按模板流转 + Redis 加锁"，没讲实例粒度与 `state+event→action`），属于**样本不够 mid**，不是打分器坏了；重写到「模板钉 SmID + 一履约单一个实例 + 悲观锁 + StFailed + 存储分层」后重跑，**3.50**，排序归位。改写只花 1 次调用。

修正后 10 题全部 good>mid>bad，b 档这组分数：Q1 4.80/3.50/2.00、Q3 4.80/2.00/1.20、Q5 4.80/2.00/1.30、Q8 4.50/1.80/1.30、Q10 5.00/1.70/1.60（`eval/last-p4b.json` + `eval/last-p4b2.json`）。

另一个信号：`Q1-badb` 明明说反了（CAS 乐观锁、每 Task 一个状态机、ES 当主存储），仍拿到 **2.00** —— 正好顶到「事实不符封顶」的天花板，说明封顶在起作用，但也说明**自信用力讲错的答案，稳定比真空的 mid 分高**，差档要写更刺眼才能压住。

**R 结果（扩到 45 条时）**　10 题全部排序正确（good>mid>bad），平均分差 2.76，调用 30/30，产物 `eval/last-p4a.json`。同档两条的分数差是这轮的新信息：多数在 0.1–0.4，Q4-good（5.00 vs 4.10）、Q4-bad（1.20 vs 2.00）、Q9-mid（2.50 vs 1.60）差到 0.8–0.9—— 同一档内写法的差异能吃到 0.9 分，说明**刻度还不是仪器，只能当排序用**。

**R 结果（早期 5 题）**　5 次倒挂全部定位到样本：Q10 根因写错（说成内存泄漏）、Q3 口径不符（删除缓存 vs 写前校验）、Q8-good 答非所问、Q8-mid 三处与事实相反（checkpointer / 权限靠 prompt / 做了重排）、Q8「4 节点 / 5 步」两个数混说。修完后 5/5 排序正确，平均分差 2.84。

**理想方案**



* 倒挂排查顺序固定：读 missing → 核事实源 → 修样本 → 再动 prompt → 最后动权重。顺序反了就是在给错误样本调参。

* 每题 ≥2 条独立写法才算稳定：60 条已做到 10 题 × 每档 2 条；再往上就是多票取中位（每条 ×3 调用）。

* 单独建「事实源」文件，样本里每个数字可指到具体一行，避免两处口径打架。

* 评测指标分开看：排序一致性（是否 good>mid>bad）与刻度稳定性（同档不同写法、同答案重复跑的抖动）。本项目排序 10/10 稳、刻度抖 0.1–0.9，所以报告里只写「分档经本人确认」和「排序正确」，不写「分数已校准」。

* 同档两条还有个副作用：能查出「打分器其实在量长度 / 术语密度」。Q6-mid（1.90）比 Q6-midb（2.00）短、机制更对却仍低 0.1—— 长度 / 术语密度的影响还在，但已被压到噪声范围；要彻底消掉得往 rubric 里加「长度不加分」。

## 6. 检索：便宜的召回也能有净收益，前提是零调用

**S 场景**　想让打分有固定参照，又不想为每道题多花一次调用。

**T 任务**　召回准确、可解释，且知识库缺失时不能拖垮主链路。

**A 动作**



* 把 `my/best-answers.md` 切成按题知识块（`app/kb.py`），字段用「一句话 / 讲法 / 关键数字 / 会被追问 / 边界」

* 本地 IDF 召回：中文双字 + 英文词，主题词命中额外 +2.5，低于阈值 `RECALL_MIN_SCORE` 返回 None（当前 **35.0**）

* 阈值不是拍的：`eval/threshold_scan.py` 从 trace 里现算 212 次召回的实际分布 —— 区间 **38.69–63.48**、中位 **48.46**、p10 **39.34**。早期写的 3.0 远低于整个分布，等于**永不弃权**：参数在，但一次也没否决过（212/212 全命中）。取 35.0 = 实测最低分再留 3.7 余量，行为不变但阈值回到有效区间；题库扩大或题干跑偏时它会真的说不

* 召回结果只作「覆盖度对照」注入打分 prompt，不做答案

* 召回为空 → 退化成「原行为」，即**接检索之前的那条链路**：不往打分 prompt 里插任何知识块，其余（六维打分、代码重算、封顶、trace）一字不变。所以关检索与接检索两次跑，同一份答案的差异只可能来自那段知识块，对照才成立

* **源码**　[app/kb.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[retrieve()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)（`retrieve()` + `_retrieve()`，主题词 +2.5 加权与阈值判断都在其中；阈值常量见 `RECALL_MIN_SCORE`）、[app/kb.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[\_parse\_section()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)（字段解析）

**R 结果**　10 题召回 10/10，召回分 39–63；同一份错答案（Q1 CAS 版）3.90 → 2.40；关检索与接检索对比，差档平均 2.16 → 1.76（Q1 降幅最大 3.30 → 2.00）。

**踩过的三个静默 bug**　都是不报错，只表现为「prompt 里塞了乱码 / 少了整段」，或者图解被当成要点参与召回：



* 去 Markdown 项目符号的正则写成 `^[-*]\s*`，把 `**加粗**` 的首个星号吃掉，标签变 `*模型与分流**` → 改 `^[-*]\s+`

* 小标题带括注（`**边界（最重要）**`）时用原始串匹配字段名，整段静默漏掉 → 先剥括号再匹配

* **文档结构本身就是接口：辅助内容（图解 / 自查笔记）只能放「分数」之后**（2026-11-10 加 Q8、Q6 图解时定下的规矩）。解析器只认 5 个字段，遇到不认识的 `**图解**` 标题不会报错，只会把后续正文**并进上一个字段**——如果上一个字段是 list（`讲法` / `会被追问`），图解就被当成要点参与召回，静默污染评分。放「分数」之后（此时 `cur` 是字符串型的 `边界`，多余行直接丢弃）最安全；改完回读校验，别靠肉眼：

```python
from app.kb import KnowledgeBase
kb = KnowledgeBase("my/best-answers.md")
assert len(kb.sections) == 10 and not kb.errors        # 段落数没变、无解析错误
sec = [x for x in kb.sections if x["qid"] == "Q6"][0]
assert not any("图解" in p["label"] for p in sec["points"])   # 图解没混进召回要点
```

  实测：Q6 加「算子 DAG + shuffle」两段图解后，`points` 仍 14、`followups` 3 → 5（新增的两条正是想让人问到的 MapReduce 对比与 shuffle），按题面召回仍命中（42.64）。**图解给的是"被追问时照这个讲"，不该改变"该说什么"的清单。**
* **文档改了措辞，解析器还认旧名字**（2026-11-10 合并两份文档时踩到）：合并后小标题从「讲法要点」改成「怎么说（可直接背）」，题目标题里的间隔号又打成了 `・`（U+30FB，不是 `·` U+00B7）—— 两个改动各自都不会报错，合起来让 10 段全部解析为空、`available=False`、打分静默退回无对照。解法是两头都松绑：标题正则同时接受 `·`/`・`，小标题走 `FIELD_ALIASES` 别名表；再加一条自检 ——**有题目标题却零解析**时记进 `errors` 并打 stderr，把静默失败变成看得见的问题（知识库坏了仍不阻断面试）。

**理想方案**



* 先做「零额外调用的本地召回」，确认有净收益再上向量库；向量化的收益必须做 A/B 才能立项。

* 召回必须可被否证：trace 记 `hit / qid / score / points`，阈值调整有数据支撑。

* 用一个最小的题把「否证」和「对照」说清楚。假设题目是「怎么保证订单状态机不重复推进」，知识库里这题的要点三条：

  候选人的回答只有一句「加了重试和日志，基本不会重复」。同一份输入跑两次：

1. 幂等：用 `request_id` / 业务主键去重，不是靠重试

2. 并发控制：状态推进用 CAS 或单飞，不是分布式锁

3. 兜底：对账 + 补偿，处理已经推进错的那部分



| 链路       | prompt 里有什么         | 模型的处境               | 实测结果                                  |
| -------- | ------------------- | ------------------- | ------------------------------------- |
| 关检索（原行为） | 题目 + 回答 + 六维 rubric | 没有清单，只能凭感觉判断「讲得够不够」 | `covered=4` / `missing=5`，**总分 3.30** |
| 接检索      | 上面这些 +【参考要点】三条      | 三条逐条比对，一条都没对上       | `covered=2` / `missing=7`，**总分 2.00** |

两份分数都来自 `eval/last-off-t0.json` 与 `eval/last-final5.json` 的真实运行，不是假设值。



* 同一份回答差 1.30 分，差别不在「模型变严了」，而在**它手上有了尺子**：原来只能说「好像还行」，现在能指出「幂等、并发、兜底，三条全没讲」。

* 「可被否证」= 这个结论允许你拿证据推翻它。三条具体用法：


  * 觉得 2.00 冤枉 → 打开 `eval/trace-*.jsonl` 看 `missing` 那几条要点，回读回答原文逐条核对：候选人真没说，2.00 站得住；说了却没进 `covered`，那是归一化的 bug，结论当场被推翻。

  * 觉得阈值定错 → 把 `RECALL_MIN_SCORE` 临时提到 **60**：Q1 实测召回分 57.76（10 题区间 39–63），于是这题 `hit=false`、不注入要点，分数退回关检索的 **3.30**—— 注意 3.30 是关检索那次的总分实测值（0–5 刻度），与阈值那个 3.0 只是数字相像，两个刻度没有关系。严格说抬阈值后应当重跑确认，温度 0 下重跑差异只来自 prompt 里有没有要点块。阈值高低**跑一次就看到后果**，不用口头争论。

  * 关检索那次的 `covered=4` 不可比：它没有固定清单，那四条是模型临时想的，第二次跑可能变成三条。**只有带清单的那次才有可复现的对照**。

* 解析层加断言：**解析出 0 个字段直接报错**，别让错误漂到下游。

* 召回做对照不做答案。要点清单只出现在 prompt 的【参考要点（仅作覆盖度对照）】一段，并明确「只判断说到没说到，不要替候选人补话」。


  * 正确的用法：清单当尺子。候选人说「用分布式锁」，清单写「CAS / 单飞」→ `missing` 点名这一条，该扣就扣。

  * 错误的用法：清单当答案。模型会按「答案长什么样」给分，候选人说得含糊但撞到几个关键词也能拿高分，评分变成抄答案。

  * 一条能自检的红线：**结果里不该出现候选人口中没说过的话**。`evidence` 要求引用回答原文，某条 evidence 在回答里搜不到，就说明模型已经拿要点当答案用了 —— 这是 bug，不是风格问题。



***

# 三、运营层：成本、观测、状态

## 7. 预算：所有调用从同一个口进

**S 场景**　一次评测 15 次调用、一场面试 11 次，跑飞一次就是真金白银，而且用户反复强调过这一点。

**T 任务**　任何路径都不能无限调用，超限要有明确失败而不是默默烧钱。

**A 动作**



* `BudgetedLLM` 包一层，超限抛 `BudgetExceeded`

* 口径写死：`ask` 1 次、`prep` 1 次、`chat` 8 次、`interview` 题数 + 1

* 本次补漏发现：`ask / prep / chat` 原本直接 new 裸 `RealLLM`，**完全没有护栏**

* **源码**　[app/budget.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[BudgetedLLM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)（`BudgetedLLM` 与 `BudgetExceeded`）、[app/main.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)[cmd\_ask()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/main.py)（四条命令的预算口径）、[tests/test\_guards.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_guards.py)（绕过检查）

**R 结果**　三条路径补上包装层后，评测 15/15 次精确用满、不超；面试按题数线性可预算。

**理想方案**



* 一个进程一个 LLM factory，业务代码禁止直接实例化 client；CI 加一条 grep 检查绕过。具体是：全仓只有 `app/main.py` 的 `_make_real()` 和 `app/web.py` 的 `_make_llm()` 允许出现 `RealLLM(`，且必须写成 `BudgetedLLM(RealLLM(), max_calls=...)`同一行；其他任何模块自己 new 一个，就等于绕过预算、直接烧钱。这条约定不能只写在文档里 ——**已落成&#x20;**`tests/test_guards.py`**&#x20;的一条断言**：扫描 `app/*.py`，出现 `RealLLM(` 而同一行没有 `BudgetedLLM(` 就失败并点名文件行号。这条测试第一次跑就抓到了 `app/web.py`，确认它其实包了预算才算通过

* 预算分三层：单请求 / 单会话 / 单日；超限要有降级策略（降模型、少追问、跳过非关键步骤），而不是直接抛错给用户。

* 重试也计数 —— 重试同样是钱。把重试次数并入同一预算池。

* 预算用尽时输出「已完成的结论 + 未完成的原因」，别让用户只看到异常。

* 重复请求不该各花一次预算（**已做**，2026-11-10 实战踩到）：一场 10 题面试预算 11 次，复盘接口被连点两次，两个请求并发进入 —— 前一个拿走最后 1 次调用（21 秒），后一个在 `calls >= max_calls` 处被拦下，落成一份「降级报告」。账没有超，但用户看到的是「预算不足」。排障只看 trace 就能定性：`call_no=11` 跨 12:43:27→12:43:49，两份产物分别写盘于 12:43:34 与 12:43:49，而全文件只有一个 `llm` 事件 ⇒ 并发重复，不是真超支。修法是**幂等 + 单飞**：`_report_lock` 包住整个 `make_report`，`state.report_md` 已存在就直接返回上一份（`cached=True`），前端在请求期间禁用按钮。原则是「预算是钱，重试和重复点击都不该各花一次」。


  * **源码**　[app/web.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/web.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/web.py)[make\_report()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/web.py)（`_report_lock` + 幂等分支）、[app/budget.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[chat()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)（拦截点 `calls >= max_calls`）、[web/index.html](https://github.com/AndreaCmy/ai-interview-agent/blob/main/web/index.html)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/web/index.html)[btnReport](https://github.com/AndreaCmy/ai-interview-agent/blob/main/web/index.html)（前端禁用按钮）

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

* **源码**　[app/trace.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/trace.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/trace.py)[Tracer](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/trace.py)（`Tracer` + 四类事件 + `request()`/`configure`/`set_scene`）；四处收口 [app/budget.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)[chat()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/budget.py)、[app/agent.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)[run\_agent()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/agent.py)、[app/kb.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)[retrieve()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/kb.py)、[app/scoring.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[score\_answer()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)；测试 [tests/test\_trace.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/tests/test_trace.py)

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

* **源码**　[app/session.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/session.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/session.py)[SessionStore](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/session.py)（`SessionStore` 两张表）、[app/interview.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/interview.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/interview.py)[run\_interview()](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/interview.py)（追问判定与预算扣减）

**R 结果**　终端 10 题 + Web 1 题各成一场，整场报告 157 行、均分 3.01，可随时重生成。

**理想方案**



* `turns` 表补 `qid`（**已做**）：`PRAGMA table_info` 检查后 `ALTER TABLE ADD COLUMN`，老库 11 行照常可读、历史行 qid 为空串；写入侧由 `item["qid"]` 带上，`turns()` 一并返回。

* 状态 schema 带 version，升级要写迁移；单文件前端存档尤其容易踩这个坑。

* 报告、trace、DB 共享同一个 `request_id` —— **已做**（2026-11-10）：`turns` 表加 `request_id` 列（老库自动 ALTER），面试每题 / 追问 / Web 单次提交都把当前窗口的 id 落库；报告「总览」表据此多一列 trace，照着 `grep <id>` 就能取回这题的四条事件。此前只有 trace 里有 id，报告只能靠 qid + 时间猜。



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

* **源码**　[app/prompts.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[SCORING\_SYSTEM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)（「承认未做 / 缺口」整节）、[app/scoring.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)[FACT\_MISMATCH\_CAP](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/scoring.py)（封顶常量）

**R 结果**　Q8-good 4.10 → 4.50，排序不变；同时连带查出两个不干净的 mid 样本（Q3、Q8），修后 2.70 / 2.60。

**理想方案**



* rubric 每加一条，先在 golden set 上看「谁涨谁跌」；没有差异化效果就别加，否则只是自我安慰。

* 口径类规则必须由人拍板，模型只能提议；每次变更留「规则 → 影响样本 → 分数变化」三列记录。

* 评分要闭环到话术：报告给「这题下一步怎么改口」，只给分数对求职者没有价值（这也是 P5 要补的部分）。

* 「可背诵」不等于「提纲化」（**已修**，2026-11-10）：第一版报告把改进话术写成「失败建模：StFailed 进状态机，可查、可交接…」这种标签加短语，信息全但没法背 —— 面试时人不会用冒号分号说话。改法是在 `REPORT_SYSTEM` 里把文体写成硬约束：第一人称、短句、每题 200 字以内、按「先给结论 → 讲机制 → 说取舍」排，禁止标签体，承认缺口要用「老实说这块没做」这种说法。同一份事实，改完是这样的：「并发这块我没用 CAS，用的是 Redis 按订单号加悲观锁…… 为什么不用乐观锁？因为 action 带副作用、撤不回来」。事实与数字仍只允许来自问答记录与参考要点 —— 口语化不许变成加细节。


  * **源码**　[app/prompts.py](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[ · ](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)[REPORT\_SYSTEM](https://github.com/AndreaCmy/ai-interview-agent/blob/main/app/prompts.py)（`REPORT_SYSTEM` 里的文体约束）

* 参考要点必须先过本人核一遍（**已做**，2026-11-10）：`best-answers.md` 里「每个 Task 一个独立状态机实例」是早期推断出来的措辞，本人核对后真实是「一个履约单对应一个状态机实例，Task 是实例内部流转的子任务」。这条错误顺着 `best-answers → eval/build_golden.py → golden.jsonl → 报告话术` 一路传下去，等于把推断当事实背到面试现场。规矩是：机制类描述进 best-answers 前由本人口述确认一次；已污染的要连源头（`ANSWERS` 字典）一起改，再重生成 `golden.jsonl`，只改报告等于没改。同一份 Q1 话术里还混进了 Q6 的「Flink 算子精简 60%」——**串题也是事实错误**，生成侧按题给事实边界（**已做**）：`REPORT_SYSTEM` 第 6 条禁止把整场其他题的机制、数字、系统名搬进本题，拿不准就不写 —— 宁可少讲，不能让候选人照着背出去。



***

## 11. 口径变更台帐：改规则就要留一行

**S 场景**　评分口径改过几次（诚实加分、串题禁止、口语化硬约束、阈值校准），每次都影响一批已有样本，但改动散在各节正文里，事后很难回答「这条规则是什么时候加的、当时哪些样本变了、分数涨了多少」。

**T 任务**　把「规则 → 影响样本 → 分数变化」固化成一张表，新增一条规则就补一行，不靠回忆。

**A 动作**　只维护这一张表；改任何影响打分的规则时，同一提交里补一行，并写清「旧值 → 新值」与「哪批样本需要重跑」。



| 日期         | 规则变更                                                       | 影响样本             | 分数变化                                                 | 是否重跑                         |
| ---------- | ---------------------------------------------------------- | ---------------- | ---------------------------------------------------- | ---------------------------- |
| 2026-11-09 | 诚实加分：承认没做 / 缺口不再按「扣分」处理，写进 `SCORING_SYSTEM`                | golden 全部 15 条   | Q8-good 4.10 → 4.50，排序不变                             | 已重跑（`eval/last-final5.json`） |
| 2026-11-10 | 禁串题：`REPORT_SYSTEM` 第 6 条，某题只讲本题事实                         | 报告话术 10 题（不影响打分） | 不打分，Q2 整段重写、Q1 删掉 Flink 60%                          | 不适用（生成侧）                     |
| 2026-11-10 | 口语化硬约束：第一人称短句、禁标签体、≤200 字                                  | 报告话术 10 题        | 话术文本分 2.88 → 4.50（文本上限，不是现场分）                        | 已重跑（`/tmp/oral_scores.json`） |
| 2026-11-10 | 召回阈值 `3.0` → `35.0`（仍低于实测最低分 38.69）                        | 10 题召回           | 当前行为不变（212/212 仍命中）；阈值进入有效区间                         | 未重跑（无需，行为等价）                 |
| 2026-11-10 | 评测样本补满：`Q1-midb` 重写（原稿太空，曾被 `Q1-badb` 反超）                  | Q1 三档样本          | midb 1.60 → **3.50**                                 | 已重跑（`eval/last-p4b2.json`）   |
| 2026-11-10 | **Q6 正文事实修正**：删掉 MapReduce 类比，成本归因改成「shuffle 网络序列化 + 算子状态」 | Q6 三档样本 + Q6 话术分 | good 4.40 → **4.50**；mid 1.70 → 1.90；bad 1.30 不变     | 已重跑（`eval/last-q6r3.json`）   |
| 2026-11-10 | 事实修正：Q1 实例粒度改为「一个履约单一个实例」                                  | Q1 三档 + Q1 报告话术  | Q1-good 文本已变，旧标定作废 → 重跑后 **4.90 / 2.50 / 2.00**，排序不变 | 已重跑（`eval/last-q1r2.json`）   |

**R 结果**　现在能一眼看出「哪条规则动过分数、哪批样本的旧结论已经失效」—— 比如 Q1 那行直接标着旧标定作废，不会再拿旧分当结论讲。

**理想方案**



* 台帐的「是否重跑」列是状态机而不是备注：`待重跑 → 已重跑（产物路径）`，未重跑的不许在结论里引用旧分。

* 更进一步：把台帐做成 `eval/baselines.json`（规则版本 → 分数快照），跑评测时自动比对上一版，缺一行就报错。

# 五、成熟度自评：现状 vs 理想



| 维度   | 现状                                                                                                                                        | 理想                      | 差距 | 可做性                                                          |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- | -- | ------------------------------------------------------------ |
| 循环收敛 | max\_iter + 三条 `stop_reason`（answered /max\_iter/repeated\_tool\_call）+ 重复调用强制停                                                           | 断点续跑（messages + 工具结果落盘） | 中  | 断点续跑 **中等**（要设计落盘格式与去重）                                      |
| 工具治理 | 注解推导 schema + pydantic 执行前校验（类型 / 枚举 / 必填 / 多余字段）+ **值域约束**（`PARAM_LIMITS` → `minimum/maximum`/`minLength/maxLength`，schema 与校验同源）+ 防绕过测试 | 单工具超时 + 幂等键             | 低  | 取值范围 **已做**（2026-11-10）；超时 **中等**；幂等键 **暂不做**（当前工具全只读）       |
| 判断分层 | 代码重算总分 + 事实不符封顶 + `cap\_reason`（带封顶前分数）                                                                                                   | 模型原话留痕 + 六维一致性检查        | 低  | 原话留痕 **中等**（涉及隐私裁剪）；一致性检查 **需样本**                            |
| 评测   | **10 题 × 每档 2 条 = 60 条**，排序 10/10 正确，同档抖动 0.1–0.9                                                                                         | 多票取中位 + 争议题人工仲裁         | 中  | 多票 **需预算**（每条 ×3）；长答案一致性可做                                   |
| 检索   | 本地 IDF，零额外调用；阈值 **35.0** 由 trace 实测分布定（`eval/threshold_scan.py`）                                                                          | 同义扩展 + 向量 A/B           | 低  | 阈值回归 **已做**（2026-11-10，212 次实测）；同义扩展 **中等**；向量 A/B **需调用预算** |
| 预算   | 单一收口，四命令有上限                                                                                                                               | 三层预算 + 超限降级策略           | 中  | 三层预算 **中等**；降级策略 **需先定产品口径**（降到哪个模型、少几道题）                    |
| 观测   | 四类事件 JSONL + `request\_id` 父链（CLI / Web / 面试每题 / 评测每 case 全覆盖）+ **聚合视图**（`eval/trace_metrics.py`：真实延迟 p50/p95、工具失败率、封顶率、召回命中率）            | 面板 / 趋势对比               | 低  | 指标聚合 **已做**（2026-11-10，零调用）；面板 **暂不做**（数据量不值）                |
| 状态   | SQLite 两表 + `qid` 列 + `request_id`**&#x20;列**（老库自动 ALTER 迁移）；报告总览表直接列出每题 trace id                                                         | schema 版本号              | 低  | 报告带 request\_id **已做**（2026-11-10）；版本号 **中等**                |
| 口径   | 诚实加分已落地并验证；**口径变更台帐**成表（规则 → 影响样本 → 分数变化）                                                                                                 | 台帐随每次改口径自动提醒补行          | 低  | 台帐 **已做**（2026-11-10，见第六节）；自动提醒 **暂不做**                      |

**第一批低成本已全部落地（2026-11-10 上午）**：补 qid → `stop\_reason` 枚举 + 重复调用检测 → `cap\_reason` → trace 加 `request\_id` 父链 → 工具参数校验。改动落在 `app/agent.py` / `session.py` / `scoring.py` / `trace.py` / `tools.py` / `interview.py` / `web.py` / `main.py` / `eval/run.py`，新增 `tests/test\_hardening.py` 14 条，全量 **42 条**测试通过。

**第二批低成本已全部落地（2026-11-10 晚）**：值域约束（`PARAM_LIMITS`）→ `request_id` 落库并进报告 → 阈值按实测分布校准（3.0 → 35.0）→ 口径变更台帐 → 指标聚合脚本（`eval/trace_metrics.py`）。同一批还修掉一个静默失败：合并文档后小标题措辞与标题间隔号变化，让知识库 10 段全部解析为空（见第六节「踩过的三个静默 bug」）。新增 `tests/test_limits.py` 8 条（值域 / 越界 /request\_id 落库 / 知识库解析自检），全量 **50 条**通过。

**要先算预算再动的**：扩样本（10 题 × 每档 2 条）与多票取中位，两者都直接翻调用次数，按你反复强调的「控制调用频次」，得先确认额度再开跑。

**可以明确不做的**：幂等键 —— 本项目四个工具全是只读或回显，没有写操作，加了也只是纸面治理。

这一时期还新增了三条原来没写的工程问题：停止语义与可恢复（条目 1）、工具 schema 类型与幂等治理（条目 2）、状态主键与 request\_id 对齐（条目 8、9）。