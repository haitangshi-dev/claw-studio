import sys, json
from datetime import datetime

def main():
    try:
        raw = sys.stdin.buffer.read()
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
    except Exception as e:
        print("错误：无法解析 JSON 参数 -", e)
        return
    text = data.get("text", "")
    if not text:
        print("错误：缺少 text 参数")
        return
    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = text.split("\n")
    out_lines = [f"{date_str} {ln}\n" for ln in lines if ln != ""]
    with open("notes.md", "a", encoding="utf-8") as f:
        f.writelines(out_lines)
    print(f"已追加 {len(out_lines)} 行到 notes.md")

if __name__ == "__main__":
    main()
