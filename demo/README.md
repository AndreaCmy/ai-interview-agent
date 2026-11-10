# demo/ 演示素材

这个目录只放**可用于公开演示的虚构素材**，不含任何真实简历、真实 JD、真实场次数据。

| 文件 | 说明 |
| --- | --- |
| `jd-sample.txt` | 虚构的「医疗信息化 后端」JD（虚构公司“云岐健康科技”），覆盖状态机、幂等、号源、HL7 等考点 |
| `resume-sample.txt` | 虚构简历（李某某，7 年经验），三个项目自洽，联系方式是示例占位 |
| `bank-1q.json` / `bank-sample.md` | 单题/多题题库样例（`bank-sample.*` 由真实简历生成，已被 gitignore，公开前要先改写） |
| `report-1q.md` / `scorecard-sample.md` | 报告与评分卡样例 |
| `index.html` / `preview.png` / `walkthrough.gif` | 回放页与终端实录 |
| `make-demo-data.sh` | 一键把上面两份样例装成一套可跑的数据目录 |

## 用样例起一套干净的数据目录（不动你的真实 `my/`）

```bash
./demo/make-demo-data.sh /tmp/ai-interview-demo
AI_DATA_DIR=/tmp/ai-interview-demo ./practice.sh jd list
AI_DATA_DIR=/tmp/ai-interview-demo ./practice.sh prep 10 --jd medical-it   # 1 次 LLM 调用
AI_DATA_DIR=/tmp/ai-interview-demo ./practice.sh start --jd medical-it
```

`AI_DATA_DIR` 是唯一的数据目录开关，`app/kb.py`、`app/web.py`、`practice.sh`、Skill 的 `bin/interview` 都认它：

- 不设：用仓库里的 `my/`（个人练习区，已 gitignore）
- 设了：用指定目录，仓库里不留任何个人数据

## 公开版与个人版的边界

- **能公开**：代码、`eval/golden.jsonl`、`demo/` 里的虚构样例。
- **不能公开**：`my/`（真实 JD、简历、会话库、trace）、`demo/bank-sample.*`（由真实简历生成）。
- 公开演示请一律用 `AI_DATA_DIR` 指到临时目录，避免误把 `my/` 的内容截图或打包出去。
