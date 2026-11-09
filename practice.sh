#!/usr/bin/env bash
# 面试练习入口：原件丢进 my/raw/，之后只用这一个脚本。
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

DIR="my"
RAW="$DIR/raw"
JD="$DIR/jd.txt"
CV="$DIR/resume.txt"
TOOL="tools/to_text.py"

c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'

need_key() {
  if [ ! -f .env ]; then
    echo "${c_warn}缺少 .env${c_off}：cp .env.example .env 后填入 DEEPSEEK_API_KEY" >&2
    exit 1
  fi
}

init_tpl() {
  mkdir -p "$RAW"
  if [ ! -f "$RAW/说明.txt" ]; then
    cat > "$RAW/说明.txt" <<'EOF'
把原件直接丢进这个目录，文件名开头写 jd 或 resume：

  jd.pdf / jd.png / jd.docx / jd.txt      招聘要求
  resume.pdf / resume.docx / resume.txt   简历

多张截图可以 jd-1.png、jd-2.png 这样命名。
准备跑之前执行： ./practice.sh prep

能自动转文字的：txt / md / pdf / docx
图片（截图、拍照）：需要 OCR —— 发给我转写，或手动复制进 my/jd.txt
EOF
  fi
  if [ ! -f "$JD" ]; then
    cat > "$JD" <<'EOF'
在这里粘贴 JD（招聘要求原文，越多越好）

例：
岗位：AI Agent 研发工程师
职责：负责 Agent 产品后端研发，手写 agent loop / 工具编排 / 记忆与上下文管理；
     参与 prompt 工程与评测体系搭建，保障线上稳定与成本可控。
要求：熟悉 Python；理解 function calling、ReAct、RAG；有工程落地能力。
EOF
  fi
  if [ ! -f "$CV" ]; then
    cat > "$CV" <<'EOF'
在这里粘贴简历（或你最想被问到的 2-3 个项目经历，写清背景/你的动作/量化结果）

例：
项目：对账 Agent（2024.06-2025.03，后端负责人）
- 背景：对账人工核销日均 3000 单，准确率依赖人工复核
- 动作：设计状态机 + 工具编排，接入 3 个内部系统；补 max_iter 与异常回喂兜底
- 结果：自动核销率 82%，人工复核量下降 60%
EOF
  fi
}

is_blank() { grep -q "在这里粘贴" "$1" 2>/dev/null; }

# 把 my/raw/<base>.* 全部转成 <dst>（多张截图会合并）；0=成功 1=没找到 2=没转成（需人工）
resolve_src() {
  local base="$1" dst="$2" f tmp rc found=0 failed=0
  [ -d "$RAW" ] || return 1
  tmp="$DIR/.tmp_$base.txt"
  : > "$tmp"
  for f in "$RAW"/*; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    case "$name" in
      "$base".*|"$base"-*) ;;      # jd.pdf / jd-1.png / resume.docx 都算
      *) continue ;;
    esac
    found=1
    echo "${c_dim}  读取 $f …${c_off}"
    set +e
    "$PY" "$TOOL" "$f" "$tmp.part"
    rc=$?
    set -e
    if [ $rc -eq 0 ]; then
      cat "$tmp.part" >> "$tmp"
      printf '\n' >> "$tmp"
    else
      failed=1
      case $rc in
        3) echo "${c_warn}  $f 是图片，需要 OCR${c_off}" >&2
           echo "     → 把截图发给我转写；或在「预览」里全选文字，粘进 $dst" >&2 ;;
        5) echo "${c_warn}  $f 抽不出文字（可能是扫描版 PDF）${c_off}" >&2
           echo "     → 同样走 OCR，或手动粘进 $dst" >&2 ;;
        *) echo "${c_warn}  $f 转换失败${c_off}" >&2 ;;
      esac
    fi
    rm -f "$tmp.part"
  done
  [ $found -eq 0 ] && { rm -f "$tmp"; return 1; }
  if [ -s "$tmp" ]; then
    mv "$tmp" "$dst"
    [ $failed -eq 1 ] && echo "${c_warn}  有文件没转成，已用能读的部分先跑${c_off}" >&2
    return 0
  fi
  rm -f "$tmp"
  return 2
}

help() {
  cat <<EOF
用法：./practice.sh <命令>

  init            创建 my/ 工作区（含 my/raw/，原件丢那里就行）
  prep [题量]     读 my/raw 里的 JD/简历（pdf·docx·txt）→ 生成题库（1 次调用）
  start [题量]    开一场模拟面试（每题 1 次打分 + 结尾 1 次报告）
  ask "题" "答"   只练一题，立刻出评分卡（1 次调用）
  stat            看看本地产出了什么、累计多少场

流程：原件丢 my/raw/ → ./practice.sh prep 10 → ./practice.sh start
      → 读 my/report-*.md，把改进话术抄进笔记
EOF
}

case "${1:-help}" in
  init)
    init_tpl
    echo "${c_ok}已创建${c_off} ${RAW}/ 、${JD}、${CV}"
    echo "把 JD 与简历原件拷进 $RAW/（文件名以 jd / resume 开头），再跑：./practice.sh prep"
    ;;

  prep)
    init_tpl
    need_key
    echo "${c_dim}准备素材…${c_off}"
    if is_blank "$JD"; then resolve_src jd "$JD" || true; fi
    if is_blank "$CV"; then resolve_src resume "$CV" || true; fi
    if is_blank "$JD" || is_blank "$CV"; then
      echo "${c_warn}JD 或简历还没有可用文本${c_off}：把原件放 $RAW/，或手动填 $JD / $CV" >&2
      exit 1
    fi
    N="${2:-10}"
    ROLE="${3:-}"
    [ -z "$ROLE" ] && ROLE="即时配送 / 交易履约 后端"
    echo "${c_dim}生成 $N 题题库（岗位方向：${ROLE}，1 次 LLM 调用）…${c_off}"
    "$PY" -m app prep --jd "$JD" --resume "$CV" --n "$N" --role "$ROLE" \
      -o "$DIR/bank.md" --json "$DIR/bank.json"
    echo "${c_ok}题库已写入${c_off} $DIR/bank.md（人读）、$DIR/bank.json（喂给面试）"
    echo "${c_dim}岗位方向可改：./practice.sh prep 10 岗位方向 例：AI Agent 后端${c_off}"
    ;;

  start)
    need_key
    if [ ! -f "$DIR/bank.json" ]; then
      echo "${c_warn}还没有题库${c_off}，先跑 ./practice.sh prep" >&2
      exit 1
    fi
    TS="$(date +%Y%m%d-%H%M)"
    echo "${c_dim}开始模拟面试，报告会写到 $DIR/report-$TS.md${c_off}"
    "$PY" -m app interview --bank "$DIR/bank.json" --db "$DIR/interview.db" \
      -o "$DIR/report-$TS.md" "${@:2}"
    ;;

  ask)
    shift
    need_key
    if [ $# -lt 2 ]; then
      echo "用法：./practice.sh ask \"面试题\" \"你的回答\"" >&2
      exit 1
    fi
    "$PY" -m app ask --question "$1" --answer "$2"
    ;;

  web)
    need_key
    if [ ! -f "$DIR/bank.json" ]; then
      echo "${c_warn}还没有题库${c_off}，先跑 ./practice.sh prep" >&2
      exit 1
    fi
    PORT="${2:-8000}"
    echo "${c_ok}浏览器打开${c_off} http://127.0.0.1:${PORT}"
    echo "${c_dim}预算=题数+1；Ctrl+C 停止${c_off}"
    WEB_DB="$DIR/web-interview.db" PORT="$PORT" "$PY" -m app.web
    ;;

  stat)
    echo "工作区：$DIR"    echo "  原件（${RAW}）："
    ls -1 "$RAW" 2>/dev/null | grep -v "说明.txt" | sed 's/^/    /' || echo "    （空）"
    echo "  文本："
    for f in jd.txt resume.txt bank.md bank.json interview.db; do
      [ -f "$DIR/$f" ] && printf "    %-14s %7s 字节\n" "$f" "$(wc -c <"$DIR/$f" | tr -d ' ')"
    done
    echo "  报告："
    ls -1 "$DIR"/report-*.md 2>/dev/null | sed 's/^/    /' || echo "    （还没有，跑 ./practice.sh start）"
    if [ -f "$DIR/interview.db" ]; then
      "$PY" - "$DIR/interview.db" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    s = c.execute("select count(*) from sessions").fetchone()[0]
    t = c.execute("select count(*) from turns").fetchone()[0]
    print(f"    累计 sessions={s}  turns={t}")
except Exception as e:
    print("    （数据库还没建表）", e)
PY
    fi
    ;;

  help|*)
    help
    ;;
esac
