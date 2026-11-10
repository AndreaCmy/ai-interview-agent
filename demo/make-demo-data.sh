#!/usr/bin/env bash
# 用 demo/ 的虚构素材装一套可跑的数据目录（不碰仓库里的 my/）。
# 用法： ./demo/make-demo-data.sh [目标目录] [slug]
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-/tmp/ai-interview-demo}"
SLUG="${2:-medical-it}"
ROLE="医疗信息化 后端"
TITLE="云岐健康科技 · 后端研发（医疗信息化，虚构样例）"

mkdir -p "$TARGET/jd" "$TARGET/bank" "$TARGET/raw"
cp demo/jd-sample.txt "$TARGET/jd/$SLUG.txt"
cp demo/resume-sample.txt "$TARGET/resume.txt"
printf '%s\n' "$SLUG" > "$TARGET/jd/.active"

PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
"$PY" - "$TARGET/jd/$SLUG.json" "$SLUG" "$TITLE" "$ROLE" <<'PY'
import json, sys, datetime
path, slug, title, role = sys.argv[1:5]
json.dump({
    "slug": slug, "title": title, "role": role,
    "created": datetime.date.today().isoformat(), "source": "demo/jd-sample.txt（虚构）",
}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

echo "已创建演示数据目录：$TARGET"
echo "  JD      ：$TARGET/jd/$SLUG.txt（$(wc -c <"$TARGET/jd/$SLUG.txt" | tr -d ' ') 字节，虚构）"
echo "  简历    ：$TARGET/resume.txt（虚构，联系方式为占位）"
echo "  默认 JD ：$SLUG"
echo
echo "下一步（第一条会花 1 次 LLM 调用）："
echo "  AI_DATA_DIR=$TARGET ./practice.sh prep 10 --jd $SLUG"
echo "  AI_DATA_DIR=$TARGET ./practice.sh start --jd $SLUG"
echo "  AI_DATA_DIR=$TARGET ./practice.sh web 8000"
