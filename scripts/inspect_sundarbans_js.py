from pathlib import Path
import re

t = Path("data/raw/sundarbans/StudyView.js").read_text(encoding="utf-8")
print("subject count", len(re.findall(r'subject:"', t)))
print("code count", len(re.findall(r'code:"', t)))
print("notes:", len(re.findall(r"notes:", t)))
print("pyq", len(re.findall(r"pyq:", t)))
idx = t.find('subject:"')
print("--- sample ---")
print(t[idx : idx + 1200])
# try locate top-level keys
for key in ("foundation", "diploma", "bs"):
    for pat in (f"{key}:[", f"{key}: [", f'"{key}":['):
        i = t.find(pat)
        if i != -1:
            print(key, "found via", pat, "at", i)
            print(t[i : i + 200])
            break
