"""Run from the project folder (the one containing web/ and services/), AFTER copying the 3 new files in.
Makes two small edits, safely and only once:
  web/__init__.py                 registers the new compare_import blueprint
  web/static/js/tabs/compare.js   "Paste / notes" now uses compare_import.js
"""
import re
from pathlib import Path

def edit(path, fn):
    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = fn(old)
    if new == old:
        print(f"  {path}: already done, left alone")
        return
    p.write_text(new, encoding="utf-8", newline="")
    print(f"  {path}: updated")

def web_init(t):
    if "compare_import" in t:
        return t
    a = "from .api import compare, entries, people, scoring, system"
    b = "for module in (compare, entries, people, scoring, system):"
    assert a in t and b in t, "web/__init__.py doesn't look like the expected file - edit it by hand"
    return t.replace(a, "from .api import compare, compare_import, entries, people, scoring, system").replace(
        b, "for module in (compare, compare_import, entries, people, scoring, system):")

def compare_js(t):
    if "paintImportTab" in t:
        return t
    imp = "import { $, $$, el, empty, html, render } from '../dom.js';"
    assert imp in t, "compare.js doesn't look like the expected file - edit it by hand"
    t = t.replace(imp, imp + "\nimport { paintImportTab } from './compare_import.js';", 1)
    t, n = re.subn(r"  function paintImport\(\) \{.*?\n  \}\n(?=\r?\n  return \{ activate: load \};)",
                   "  function paintImport() { paintImportTab(body, data, load); }\n", t, count=1, flags=re.S)
    assert n == 1, "couldn't find paintImport() in compare.js - edit it by hand"
    return t

edit("web/__init__.py", web_init)
edit("web/static/js/tabs/compare.js", compare_js)
print("done")
