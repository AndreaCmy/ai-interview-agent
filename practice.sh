#!/usr/bin/env bash
# 面试练习入口（多套 JD）：一份 JD 一套题库，互不覆盖。
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

# 数据目录：默认 ./my；设 AI_DATA_DIR 可指向仓库外（公开演示/多机共用都用这个）
DIR="${AI_DATA_DIR:-$PWD/my}"
case "$DIR" in /*) ;; *) DIR="$PWD/$DIR" ;; esac
RAW="$DIR/raw"
JDS="$DIR/jd"            # <slug>.txt 正文 / <slug>.json 元信息 / .active 默认
BANKS="$DIR/bank"        # <slug>.json 题库 / <slug>.md 人读版
CV="$DIR/resume.txt"
TOOL="tools/to_text.py"

mkdir -p "$RAW" "$JDS" "$BANKS"

c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'

need_key() {
  if [ ! -f .env ]; then
    echo "${c_warn}缺少 .env${c_off}：cp .env.example .env 后填入 DEEPSEEK_API_KEY" >&2
    exit 1
  fi
}

active_slug() { [ -f "$JDS/.active" ] && head -1 "$JDS/.active" | tr -d '[:space:]' || true; }

meta() {  # meta <slug> <字段>
  local f="$JDS/$1.json"
  [ -f "$f" ] || { echo ""; return; }
  "$PY" - "$f" "$2" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], "") or "")
PY
}

init_tpl() {
  mkdir -p "$RAW" "$JDS" "$BANKS"
  if [ ! -f "$RAW/说明.txt" ]; then
    cat > "$RAW/说明.txt" <<'EOF'
把原件直接丢进这个目录，文件名开头写 jd 或 resume：

  jd.pdf / jd.docx / jd.txt               招聘要求（txt/pdf/docx 能自动转文字）
  jd.png / jd-1.png / jd-2.png            截图：需要 OCR，由助手转写
  resume.pdf / resume.docx / resume.txt   简历

再加一份新 JD（不覆盖旧的）：
  ./practice.sh jd add <slug>            # 从 my/raw/ 里新放的 jd* 文件读
  ./practice.sh prep 10 --jd <slug>

图片流程：发图给助手 → 助手转写成 my/jd/<slug>.txt → prep --jd <slug>
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

# 把 my/raw/<base>.* 转成 <dst>（多张截图合并）；0=成功 1=没找到 2=没转成（需 OCR）
resolve_src() {
  local base="$1" dst="$2" f tmp rc found=0 failed=0 name
  tmp="$DIR/.tmp_$base.txt"
  : > "$tmp"
  for f in "$RAW"/*; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    case "$name" in
      "$base".*|"$base"-*) ;;
      *) continue ;;
    esac
    found=1
    echo "${c_dim}  读取 $f …${c_off}"
    set +e
    "$PY" "$TOOL" "$f" "$tmp.part"
    rc=$?
    set -e
    if [ $rc -eq 0 ]; then
      cat "$tmp.part" >> "$tmp"; printf '\n' >> "$tmp"
    else
      failed=1
      case $rc in
        3) echo "${c_warn}  $f 是图片，需要 OCR${c_off}" >&2
           echo "     → 把截图发给助手转写，写进 my/jd/<slug>.txt" >&2 ;;
        5) echo "${c_warn}  $f 抽不出文字（可能是扫描版 PDF）${c_off}" >&2 ;;
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
  rm -f "$tmp"; return 2
}

list_jd() {
  local act; act="$(active_slug)"; local n=0 slug t role bank
  printf '%-2s %-3s %-18s %-24s %s\n' "" "#" "slug" "方向" "题库"
  for f in "$JDS"/*.txt; do
    [ -e "$f" ] || continue
    n=$((n+1)); slug=$(basename "$f" .txt)
    role=$(meta "$slug" role); t=$(meta "$slug" title)
    if [ -f "$BANKS/$slug.json" ]; then
      bank="$("$PY" - "$BANKS/$slug.json" <<'PY'
import json,sys
print(len(json.load(open(sys.argv[1],encoding="utf-8")).get("questions",[])))
PY
) 题"
    else bank="—"; fi
    [ "$slug" = "$act" ] && m="★" || m=" "
    printf '%s  %-3s %-18s %-24s %s  %s\n' "$m" "$n" "$slug" "${role:-?}" "$bank" "$t"
  done
  if [ "$n" -eq 0 ]; then echo "  （还没有 JD：./practice.sh jd add <slug>）"; fi
  return 0
}

jd_add() {
  local slug="${1:-}"
  [ -n "$slug" ] || { echo "用法：./practice.sh jd add <slug>" >&2; exit 1; }
  [ -f "$JDS/$slug.txt" ] && { echo "${c_warn}已存在同名 JD：$slug${c_off}（不会覆盖）" >&2; exit 1; }
  resolve_src jd "$JDS/$slug.txt" || true
  if [ ! -s "$JDS/$slug.txt" ] || is_blank "$JDS/$slug.txt"; then
    echo "${c_warn}没从 my/raw/ 读到可用的 JD 文字${c_off}：把 jd.txt/pdf/docx 放进去，或让助手把图转写成 my/jd/$slug.txt" >&2
    rm -f "$JDS/$slug.txt"; exit 1
  fi
  printf '{\n  "slug": "%s",\n  "title": "%s",\n  "role": "%s",\n  "created": "%s",\n  "source": "my/raw/"\n}\n' \
    "$slug" "$slug" "后端" "$(date +%Y-%m-%d)" > "$JDS/$slug.json"
  echo "${c_ok}已新增 JD${c_off} $JDS/$slug.txt"
  echo "改岗位名与方向：直接编辑 $JDS/$slug.json 的 title / role"
  echo "然后：./practice.sh prep 10 --jd $slug"
}

help() {
  cat <<EOF
用法：./practice.sh <命令>

  init                    创建工作区目录（my/jd、my/bank、my/raw）
  jd list                 列出所有 JD（★=默认，标出题库有没有）
  jd add <slug>           从 my/raw/ 的 jd* 文件新增一份 JD（不覆盖已有）
  jd use <slug>           切默认 JD
  prep [题量] [--jd slug]  按某份 JD 生成题库（1 次调用，只覆盖同名题库）
  start [--jd slug]       开一场模拟面试（每题 1 次打分 + 结尾 1 次报告）
  web [端口] [--jd slug]  起网页版
  ask "题" "答"           只练一题，立刻出评分卡（1 次调用）
  stat                    本地产出概览

流程：JD 就位 → prep 10 --jd <slug> → start --jd <slug> → 读 my/report-*.md
EOF
}

# 解析 --jd：设置 JD_SLUG，其余参数留给子命令
JD_SLUG=""
ARGS=()
for a in "$@"; do
  if [ "$a" = "--jd" ]; then NEXT_IS_JD=1; continue; fi
  if [ "${NEXT_IS_JD:-0}" = "1" ]; then JD_SLUG="$a"; NEXT_IS_JD=0; continue; fi
  ARGS+=("$a")
done
set -- "${ARGS[@]}"

CMD="${1:-help}"; shift || true
[ -z "$JD_SLUG" ] && JD_SLUG="$(active_slug)"
[ -z "$JD_SLUG" ] && JD_SLUG="didi-food"

case "$CMD" in
  init)
    init_tpl
    echo "${c_ok}已创建${c_off} $JDS/ 、$BANKS/ 、$RAW/"
    echo "下一步：./practice.sh jd list"
    ;;

  jd)
    SUB="${1:-list}"; shift || true
    case "$SUB" in
      list) list_jd ;;
      add)  jd_add "${1:-}" ;;
      use)  [ -f "$JDS/${1:-}.txt" ] || { echo "没有这份 JD：${1:-}" >&2; exit 1; }
            printf '%s\n' "$1" > "$JDS/.active"; echo "默认 JD 已切到：$1" ;;
      *) list_jd ;;
    esac
    ;;

  prep)
    init_tpl; need_key
    [ -f "$JDS/$JD_SLUG.txt" ] || { echo "${c_warn}没有这份 JD：$JD_SLUG${c_off}（先 ./practice.sh jd add <slug>）" >&2; exit 1; }
    is_blank "$CV" && resolve_src resume "$CV" >/dev/null 2>&1 || true
    is_blank "$CV" && { echo "${c_warn}简历还没有可用文本${c_off}：填 $CV" >&2; exit 1; }
    N="${1:-10}"
    ROLE="$(meta "$JD_SLUG" role)"; [ -z "$ROLE" ] && ROLE="后端"
    echo "${c_dim}用 JD「$JD_SLUG」生成 $N 题（方向：${ROLE}，1 次 LLM 调用）…${c_off}"
    "$PY" -m app prep --jd "$JDS/$JD_SLUG.txt" --resume "$CV" --n "$N" --role "$ROLE" \
      -o "$BANKS/$JD_SLUG.md" --json "$BANKS/$JD_SLUG.json"
    printf '%s\n' "$JD_SLUG" > "$JDS/.active"
    echo "${c_ok}题库已写入${c_off} $BANKS/$JD_SLUG.md（人读）、$BANKS/$JD_SLUG.json（喂给面试）"
    ;;

  start)
    need_key
    BANK="$BANKS/$JD_SLUG.json"
    [ -f "$BANK" ] || { echo "${c_warn}「$JD_SLUG」还没有题库${c_off}，先 ./practice.sh prep 10 --jd $JD_SLUG" >&2; exit 1; }
    TS="$(date +%Y%m%d-%H%M)"
    echo "${c_dim}开始模拟面试（JD：$JD_SLUG），报告写到 $DIR/report-$TS.md${c_off}"
    "$PY" -m app interview --bank "$BANK" --db "$DIR/interview.db" \
      -o "$DIR/report-$TS.md" "$@"
    ;;

  web)
    need_key
    BANK="$BANKS/$JD_SLUG.json"
    [ -f "$BANK" ] || { echo "${c_warn}「$JD_SLUG」还没有题库${c_off}，先 prep" >&2; exit 1; }
    PORT="${1:-8000}"
    echo "${c_ok}浏览器打开${c_off} http://127.0.0.1:${PORT}"
    echo "${c_dim}题库：$BANK｜预算=题数+1｜Ctrl+C 停止${c_off}"
    WEB_DB="$DIR/web-interview.db" PORT="$PORT" WEB_BANK="$BANK" "$PY" -m app.web
    ;;

  ask)
    need_key
    [ $# -ge 2 ] || { echo "用法：./practice.sh ask \"面试题\" \"你的回答\"" >&2; exit 1; }
    "$PY" -m app ask --question "$1" --answer "$2"
    ;;

  stat)
    echo "工作区：${DIR}"
    echo "  JD（${JDS}）："; list_jd | sed 's/^/  /'
    echo "  题库（${BANKS}）："
    for f in "$BANKS"/*.json; do
      [ -e "$f" ] && printf '    %-18s %7s 字节\n' "$(basename "$f")" "$(wc -c <"$f" | tr -d ' ')"
    done || echo "    （还没有）"
    echo "  文本："
    for f in resume.txt interview.db; do
      [ -f "$DIR/$f" ] && printf '    %-14s %7s 字节\n' "$f" "$(wc -c <"$DIR/$f" | tr -d ' ')"
    done
    echo "  报告："
    ls -1 "$DIR"/report-*.md 2>/dev/null | sed 's/^/    /' || echo "    （还没有，跑 ./practice.sh start）"
    if [ -f "$DIR/interview.db" ]; then
      "$PY" - "$DIR/interview.db" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    print(f"    累计 sessions={c.execute('select count(*) from sessions').fetchone()[0]}"
          f"  turns={c.execute('select count(*) from turns').fetchone()[0]}")
except Exception as e:
    print("    （数据库还没建表）", e)
PY
    fi
    ;;

  help|*) help ;;
esac
