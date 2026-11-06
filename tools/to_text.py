#!/usr/bin/env python3
"""把常见文档转成纯文本，供 prep 使用。

支持：.txt .md .pdf .docx
图片：需要 OCR，本脚本不处理（交给助手转写，或在预览里手动复制文字）
"""
import re
import sys
import zipfile
from pathlib import Path

IMAGE_EXT = {"png", "jpg", "jpeg", "heic", "webp", "gif", "tiff", "bmp"}


def pdf_text(path):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise RuntimeError("PDF 加密且无法解密，请先另存为未加密版本")
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n").replace("<w:br/>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    return (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))


def main():
    if len(sys.argv) != 3:
        print("用法: to_text.py <源文件> <目标.txt>", file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    ext = src.suffix.lower().lstrip(".")

    if ext in IMAGE_EXT:
        print(f"IMAGE\t{src}", file=sys.stderr)
        return 3
    if ext in ("txt", "md", "markdown"):
        text = src.read_text(encoding="utf-8", errors="ignore")
    elif ext == "pdf":
        try:
            text = pdf_text(src)
        except Exception as exc:  # 抽取失败要能说清原因
            print(f"FAILED\t{src}\t{exc}", file=sys.stderr)
            return 5
    elif ext == "docx":
        text = docx_text(src)
    else:
        print(f"UNSUPPORTED\t{ext}", file=sys.stderr)
        return 4

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # 纯文本再短也是有效内容；PDF/docx 抽出来太短则多半是扫描件
    min_len = 1 if ext in ("txt", "md", "markdown") else 40
    if len(text) < min_len:
        print(f"EMPTY\t{src}\t{len(text)} 字（可能是扫描版 PDF，需要 OCR）", file=sys.stderr)
        return 5
    dst.write_text(text + "\n", encoding="utf-8")
    print(f"OK\t{dst}\t{len(text)} 字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
