# -*- coding: utf-8 -*-
"""请求微信 iLink Bot 登录二维码 -> 渲染 PNG -> 轮询状态 -> 保存凭证"""
import base64, json, os, random, sys, time, urllib.parse, urllib.request
from pathlib import Path

BASE = "https://ilinkai.weixin.qq.com"
ROOT = Path(__file__).resolve().parent
SECURE = ROOT / "secure"; STATE = ROOT / "state"; QR = ROOT / "qr"
for d in (SECURE, STATE, QR): d.mkdir(parents=True, exist_ok=True)

CH = os.environ.get("ILINK_CHANNEL_VERSION", "2.4.10")
def enc(v):
    try:
        a,b,c = (int(x) for x in v.split(".")[:3]); return str(((a&0xFF)<<16)|((b&0xFF)<<8)|(c&0xFF))
    except Exception: return "0"
def uin(): return base64.b64encode(str(random.SystemRandom().randrange(0,2**32)).encode()).decode()
def apph(): return {"iLink-App-Id":"bot","iLink-App-ClientVersion":enc(CH)}
def posth(tok=None):
    h={"Content-Type":"application/json","AuthorizationType":"ilink_bot_token","X-WECHAT-UIN":uin(),**apph()}
    if tok: h["Authorization"]="Bearer "+tok
    return h
def req(method,url,headers,body=None,timeout=35):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    r=urllib.request.Request(url,data=data,headers=headers,method=method)
    with urllib.request.urlopen(r,timeout=timeout) as resp: p=resp.read()
    return json.loads(p.decode()) if p else {}

def login():
    print("[1] 请求二维码 ...", flush=True)
    r = req("POST", BASE+"/ilink/bot/get_bot_qrcode?bot_type=3", posth(), {"local_token_list":[]}, 20)
    qrcode = r.get("qrcode"); content = r.get("qrcode_img_content")
    if not qrcode or not content: raise RuntimeError("二维码响应缺字段: %s" % sorted(r))
    import qrcode as ql
    img = ql.make(content)
    png = QR/"wechat-login.png"; img.save(str(png))
    print("[2] 二维码图片: %s" % png, flush=True)
    print("[2] 二维码内容: %s" % content, flush=True)
    try:
        os.startfile(str(png))
        print("[3] 已弹出图片，请用微信扫码（5 分钟内有效）", flush=True)
    except Exception as e:
        print("[3] 自动打开失败(%s)，请手动打开上面路径" % e, flush=True)

    deadline=time.time()+300; poll_base=BASE; verify=None; last=None
    while time.time()<deadline:
        params={"qrcode":qrcode}
        if verify: params["verify_code"]=verify
        try:
            s=req("GET", poll_base+"/ilink/bot/get_qrcode_status?"+urllib.parse.urlencode(params), apph(), None, 40)
        except Exception as e:
            print("  轮询异常(重试): %s" % e, flush=True); time.sleep(2); continue
        st=s.get("status")
        if st!=last:
            print("[状态] %s" % st, flush=True); last=st
        if st=="confirmed":
            tok=s.get("bot_token")
            if not tok: raise RuntimeError("confirmed 但无 bot_token")
            baseurl=s.get("baseurl") or BASE
            (SECURE/"token.json").write_text(json.dumps({
                "bot_token":tok,"baseurl":baseurl,
                "ilink_bot_id":s.get("ilink_bot_id"),"ilink_user_id":s.get("ilink_user_id")
            },ensure_ascii=False,indent=2),encoding="utf-8")
            if s.get("ilink_user_id"):
                (SECURE/"binding.json").write_text(json.dumps({
                    "bound_user_id":s.get("ilink_user_id"),
                    "bot_user_id":s.get("ilink_bot_id"),"source":"qr_login"
                },ensure_ascii=False,indent=2),encoding="utf-8")
            (STATE/"auth.json").write_text(json.dumps({"status":"ok","updated_at":int(time.time())}),encoding="utf-8")
            print("[OK] 登录成功！凭证已保存（token 不打印）", flush=True)
            print("[OK] bot_id=%s  user_id=%s" % (s.get("ilink_bot_id"), s.get("ilink_user_id")), flush=True)
            return 0
        if st=="expired":
            print("[X] 二维码已过期，请重跑", flush=True); return 2
        if st=="scaned_but_redirect" and s.get("redirect_host"):
            h=str(s["redirect_host"]).rstrip("/")
            poll_base = h if h.startswith("http") else "https://"+h
            print("[i] 节点跳转 -> %s" % poll_base, flush=True)
        if st=="need_verifycode":
            verify=input("微信需要验证码，请输入: ").strip() or None
        if st=="verify_code_blocked":
            print("[X] 验证码尝试被限制，请重新获取二维码", flush=True); return 2
        if st=="binded_redirect":
            print("[i] 该账号已绑定，继续等待确认", flush=True)
        time.sleep(2)
    print("[X] 5 分钟超时", flush=True); return 2

if __name__=="__main__":
    try: sys.exit(login())
    except Exception as e:
        print("[X] 失败: %s" % e, flush=True); sys.exit(1)
