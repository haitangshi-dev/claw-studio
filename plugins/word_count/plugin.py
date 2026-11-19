import re


def register(api):
    def handler(args, workdir, cfg):
        text = args.get("text", "")
        if text is None:
            text = ""
        text = str(text)

        # 字数：非空（非空白）字符总数
        characters = len(re.sub(r"\s", "", text))

        # 词数：英文/数字连续串按一个词 + 每个中文字符单独算一词
        latin_words = re.findall(r"[A-Za-z0-9]+", text)
        cjk_chars = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text)
        words = len(latin_words) + len(cjk_chars)

        return (
            "字数（非空字符）: {chars}\n"
            "词数（英文词 + 中文字）: {words}"
        ).format(chars=characters, words=words)

    spec = {
        "function": {
            "name": "word_count",
            "description": (
                "统计文本的字数和词数。参数 text 为待统计文本。"
                "字数按非空字符计；词数 = 英文/数字词数 + 中文字数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待统计的文本"}
                },
                "required": ["text"],
            },
        }
    }
    api.register_tool(spec, handler)
