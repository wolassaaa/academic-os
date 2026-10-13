"""Build reviewable public information pages without third-party dependencies."""
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "website" / "dist"
STYLE = """body{margin:0;background:#f6f7f9;color:#222;font:17px/1.8 system-ui,sans-serif}
main{max-width:780px;margin:40px auto;padding:28px;background:white;border-radius:12px}
nav{display:flex;gap:24px;border-bottom:1px solid #ddd;padding-bottom:16px}
a{color:#185abc}h1{font-size:32px}h2{font-size:23px;margin-top:32px}
footer{margin-top:36px;color:#666;font-size:14px}@media(max-width:600px){main{margin:0;padding:22px;border-radius:0}}"""


def render(text):
    text = text.split("\n---\n", 1)[0]
    blocks = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        if paragraph.startswith("## "):
            blocks.append("<h2>" + escape(paragraph[3:]) + "</h2>")
        elif paragraph.startswith("# "):
            blocks.append("<h1>" + escape(paragraph[2:]) + "</h1>")
        elif paragraph.startswith("- "):
            blocks.append("<ul>" + "".join("<li>" + escape(line[2:]) + "</li>" for line in paragraph.splitlines()) + "</ul>")
        else:
            blocks.append("<p>" + escape(paragraph) + "</p>")
    return "\n".join(blocks)


OUT.mkdir(parents=True, exist_ok=True)
for filename, source, title in [
    ("index.html", "APPLICATION-HOMEPAGE-DRAFT.md", "Academic OS 2.0"),
    ("privacy.html", "PRIVACY-POLICY-DRAFT.md", "Academic OS 隐私声明"),
]:
    body = render((ROOT / "docs" / source).read_text(encoding="utf-8"))
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{STYLE}</style></head><body><main>
<nav><a href="index.html">Academic OS</a><a href="privacy.html">隐私声明</a></nav>
{body}
<footer>独立的个人学业管理工具，与 Google 或 HKBU 无官方隶属关系。</footer>
</main></body></html>'''
    assert "发布准备说明" not in page
    (OUT / filename).write_text(page, encoding="utf-8")
print("Built index.html and privacy.html; not published.")
