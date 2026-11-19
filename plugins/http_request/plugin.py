# -*- coding: utf-8 -*-
def register(api):
    import httpx
    def http_request(args, workdir, cfg):
        method = str(args.get('method') or 'GET').upper()
        url = str(args.get('url') or '').strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        headers = args.get('headers') if isinstance(args.get('headers'), dict) else None
        body = args.get('body')
        kwargs = {'timeout': 30, 'follow_redirects': True}
        if headers:
            kwargs['headers'] = headers
        if body is not None and method in ('POST', 'PUT', 'PATCH'):
            kwargs['json'] = body
        r = httpx.request(method, url, **kwargs)
        return f'HTTP {r.status_code}\n--- 响应前 4000 字 ---\n' + r.text[:4000]
    api.register_tool(
        {'type': 'function', 'function': {
            'name': 'http_request',
            'description': '发起任意 HTTP 请求（GET/POST/PUT…），返回状态码与响应正文前 4000 字。',
            'parameters': {'type': 'object', 'properties': {
                'url': {'type': 'string', 'description': '完整 URL'},
                'method': {'type': 'string', 'description': 'GET/POST/PUT/PATCH，默认 GET'},
                'headers': {'type': 'object', 'description': '请求头（可选）'},
                'body': {'type': 'object', 'description': 'JSON 请求体（POST/PUT/PATCH 用，可选）'},
            }, 'required': ['url']}},
        },
        http_request,
    )
