# -*- coding: utf-8 -*-
"""
Outlook Account Batch Unlock Script
Uses BitBrowser + Playwright — reuses the same PX press-and-hold logic as registration.

Usage:
  python unlock_outlook.py --input outlook_accounts/accounts_xxx.txt
  python unlock_outlook.py --input emails_locked.txt --concurrency 2
  python unlock_outlook.py --input outlook_accounts/accounts_xxx.txt --proxy-file proxies.txt
  python unlock_outlook.py   (auto-finds latest locked file)

Input file format (---- separated, one per line):
  email----password
  email----password----any_extra_fields...

Output (unlock_results/):
  unlocked_*.txt          successfully unlocked
  needs_phone_*.txt       requires SMS — cannot auto-unlock
  failed_*.txt            failed / timeout
"""

import argparse, asyncio, os, random, re, sys, time
from datetime import datetime

# 顶部加载 .env（真实环境变量优先），保持仓库内无明文凭据
try:
    from config import EZCAPTCHA_API_KEY as _EZCAPTCHA_KEY, EZCAPTCHA_API_BASE as _EZCAPTCHA_BASE
except Exception:
    _EZCAPTCHA_KEY = os.environ.get("EZCAPTCHA_API_KEY", "")
    _EZCAPTCHA_BASE = os.environ.get("EZCAPTCHA_API_BASE", "https://api.ez-captcha.com")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

import requests
from playwright.async_api import async_playwright

# 拟人鼠标(WindMouse 轨迹 + OU 震颤)用于 PerimeterX 按住验证。与 register_outlook_standalone.py
# 共用同一实现，取代本脚本旧的贝塞尔逼近 + 正弦漂移(周期性运动，PerimeterX 行为模型秒判)。
# 保证脚本被 importlib 从任意路径加载时也能找到 common 包。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import human_mouse as _hm

# ── Config ───────────────────────────────────────────────────────────
BITBROWSER_API  = os.environ.get("BITBROWSER_API", "http://127.0.0.1:54345")
EZCAPTCHA_KEY   = _EZCAPTCHA_KEY
EZCAPTCHA_BASE  = _EZCAPTCHA_BASE
OUTPUT_DIR      = "unlock_results"
SCREENSHOT_DIR  = "screenshots_unlock"
UNLOCK_TIMEOUT  = 300   # seconds per account


def ensure_clash_proxy_env():
    """Use .env CLASH_PROXY for direct unlock runs, while local APIs stay direct.
    对齐 register_outlook_standalone.py：给 Python 侧(EZCaptcha PX API 等)设 HTTP(S)_PROXY，
    并把 127.0.0.1/localhost 放进 NO_PROXY，避免 BitBrowser 本地 API 也走代理。"""
    existing = (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        or ""
    ).strip()
    proxy = existing or os.environ.get("CLASH_PROXY", "").strip()
    if not proxy:
        return ""
    if not existing:
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = os.environ["https_proxy"] = proxy
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in no_proxy.split(",") if p.strip()]
    for item in ("127.0.0.1", "localhost", "::1"):
        if item not in parts:
            parts.append(item)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(parts)
    return proxy


def _load_default_proxies():
    """住宅代理账密池来自 .env 的 OUTLOOK_PROXIES(多个用换行或逗号分隔)，默认空。
    对齐 register_outlook_standalone.py —— 旧的硬编码 ipwo 池已欠费(402)作废。"""
    raw = os.environ.get("OUTLOOK_PROXIES", "")
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    return [p for p in parts if p and not p.startswith("#")]


DEFAULT_PROXIES = _load_default_proxies()


# ── BitBrowser ───────────────────────────────────────────────────────
_BROWSER_CLIENT = None


def _fingerprint_provider():
    return (
        os.environ.get("FINGERPRINT_BROWSER")
        or os.environ.get("BROWSER_PROVIDER")
        or "bitbrowser"
    ).strip().lower()


def _bb_post(path, data=None):
    global _BROWSER_CLIENT
    if _fingerprint_provider() in {"adspower", "ads_power", "ads"}:
        if _BROWSER_CLIENT is None:
            from bitbrowser import BitBrowser
            _BROWSER_CLIENT = BitBrowser()
        return _BROWSER_CLIENT._post(path, data or {})
    r = requests.post(f"{BITBROWSER_API}{path}", json=data or {}, timeout=120)
    r.raise_for_status()
    res = r.json()
    if not res.get("success"):
        raise Exception(f"BitBrowser: {res.get('msg', '?')}")
    return res

def _parse_proxy(s):
    if not s: return None
    pt = "http"
    for pfx in ["socks5://", "http://", "https://"]:
        if s.lower().startswith(pfx):
            pt = pfx.split("://")[0]; s = s[len(pfx):]
    s = s.replace(",", "@", 1) if "@" not in s and "," in s else s
    m = re.match(r'^(.+):(.+)@(.+):(\d+)$', s)
    if m:
        return {"type": pt, "username": m.group(1), "password": m.group(2),
                "host": m.group(3), "port": m.group(4)}
    m2 = re.match(r'^(.+):(\d+)$', s)
    if m2:
        return {"type": pt, "host": m2.group(1), "port": m2.group(2)}
    return None

def create_browser(name="unlock", proxy_str=None):
    data = {"name": name, "remark": "outlook unlock",
            "proxyMethod": 2, "browserFingerPrint": {"coreVersion": "130"}}
    p = _parse_proxy(proxy_str)
    if p:
        data.update({"proxyType": p.get("type", "http"),
                     "host": p["host"], "port": p["port"]})
        if p.get("username"): data["proxyUserName"] = p["username"]
        if p.get("password"): data["proxyPassword"] = p["password"]
    else:
        data["proxyType"] = "noproxy"
    return _bb_post("/browser/update", data)["data"]["id"]

def open_browser(pid):
    d = _bb_post("/browser/open", {"id": pid})["data"]
    return d.get("ws") or d.get("webdriver")

def close_browser(pid):
    try: _bb_post("/browser/close", {"id": pid})
    except Exception: pass

def delete_browser(pid):
    try: _bb_post("/browser/delete", {"id": pid})
    except Exception: pass

def cleanup_stale_browsers():
    """启动时清理所有残留的 unlock/scan profile"""
    try:
        page, cleaned = 0, 0
        while True:
            r = _bb_post("/browser/list", {"page": page, "pageSize": 100})
            items = r.get("data", {}).get("list", [])
            if not items: break
            for item in items:
                name = item.get("name", "")
                if any(name.startswith(p) for p in ["unlock_", "scan_", "quick_check"]):
                    close_browser(item["id"])
                    delete_browser(item["id"])
                    cleaned += 1
            total = r.get("data", {}).get("totalNum", 0)
            page += 1
            if page * 100 >= total: break
        if cleaned:
            print(f"[startup] cleaned {cleaned} stale browser profiles")
    except Exception as e:
        print(f"[startup] cleanup error: {e}")


# ── EZCaptcha PX ────────────────────────────────────────────────────
def solve_px(page_url, app_id="PXzC5j78di", max_wait=90):
    try:
        resp = requests.post(f"{EZCAPTCHA_BASE}/createTask", json={
            "clientKey": EZCAPTCHA_KEY,
            "task": {"type": "PerimeterX", "websiteURL": page_url, "websiteKey": app_id}
        }, timeout=30)
        d = resp.json()
        if d.get("errorId", 1) != 0:
            print(f"    [px] error: {d.get('errorDescription', d)}")
            return None
        tid = d["taskId"]
        print(f"    [px] task {tid}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            r2 = requests.post(f"{EZCAPTCHA_BASE}/getTaskResult",
                               json={"clientKey": EZCAPTCHA_KEY, "taskId": tid},
                               timeout=30).json()
            if r2.get("status") == "ready":
                sol = r2.get("solution", {})
                print(f"    [px] solved! keys={list(sol.keys())}")
                return sol
            if r2.get("status") == "failed":
                print("    [px] failed"); return None
        print("    [px] timeout"); return None
    except Exception as e:
        print(f"    [px] error: {e}"); return None


# ── Page state classifier ────────────────────────────────────────────
# 文本标记覆盖 en/zh/ja/fr/es/de/pt/it/ru 等常见 Outlook 出口 UI。但 Clash 节点
# 出口国不定(如日本节点 -> 日文 UI)，纯文本判定必漏 -> classify 返回 unknown 时，
# snap() 再用 DOM 结构(输入框/iframe，与语言无关)兜底，对齐 register 的按 ID 定位思路。
def classify(text, url):
    t, u = text.lower(), url.lower()
    if "account.microsoft.com" in u and "unlock" not in u: return "logged_in"
    if "account.live.com" in u and "proofs" in u:          return "logged_in"
    if "fido/create" in u or "fido/update" in u:           return "fido_setup"
    if any(x in t for x in ["setting up your passkey", "passkey", "clé d'accès",
                             "clave de acceso", "passschlüssel", "パスキー", "密钥", "통행"]):
        return "fido_setup"
    if any(x in t for x in ["your account has been locked", "we've locked",
                              "locked for your protection", "帐户已锁定", "帳戶已鎖定",
                              "アカウントがロックされ", "계정이 잠겼",
                              "compte a été verrouillé", "cuenta ha sido bloqueada",
                              "konto wurde gesperrt", "conta foi bloqueada",
                              "account è stato bloccato", "заблокирована"]):
        return "locked"
    if any(x in t for x in ["let's prove you're human", "press and hold", "按住",
                             "长按", "長按", "押し続け", "누르고"]):
        return "px_challenge"
    if any(x in t for x in ["enter the code", "we texted", "we sent", "verification code",
                              "验证码", "短信", "コード", "인증 코드", "code de vérification",
                              "código de verificación", "bestätigungscode"]):
        return "sms_verify"
    if any(x in t for x in ["verify your identity", "unusual activity", "异常活动",
                             "本人確認", "неполадки"]):
        return "verify_needed"
    if any(x in t for x in ["something went wrong", "出错了", "問題が発生"]): return "error_page"
    if "chrome-error://" in u:      return "net_error"
    if any(x in t for x in ["enter your password", "输入密码", "パスワードを入力",
                             "entrez votre mot de passe", "introduce tu contraseña",
                             "kennwort eingeben"]):
        return "login_form"
    if any(x in t for x in ["email or phone", "sign in", "enter your email",
                             "电子邮件或电话", "メールまたは電話", "サインイン",
                             "이메일 또는 전화", "e-mail ou téléphone", "correo o teléfono",
                             "e-mail oder telefon"]):
        return "email_form"
    return "unknown"


async def _dom_state(page):
    """语言无关的 DOM 结构判定(text classify=unknown 时兜底)。只看元素存在/可见，
    不看文案 —— 与 register 按 input[type=email] / #px-captcha 定位同思路。"""
    try:
        return await page.evaluate(r"""() => {
            const vis = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const q = sel => [...document.querySelectorAll(sel)].some(vis);
            // PX 按住验证：主文档 #px-captcha 或 hsprotect iframe
            if (q('#px-captcha')) return 'px_challenge';
            for (const f of document.querySelectorAll('iframe')) {
                const src = (f.getAttribute('src') || '').toLowerCase();
                if (src.includes('hsprotect.net') && vis(f)) return 'px_challenge';
            }
            // 一次性验证码输入(SMS/邮箱码)
            if (q('input[name="otc"], #otc, input[autocomplete="one-time-code"]')) return 'sms_verify';
            // 密码输入 = 登录第二步
            if (q('input[type="password"], input[name="passwd"], #passwordEntry')) return 'login_form';
            // 邮箱/账号输入 = 登录第一步
            if (q('input[type="email"], input[name="loginfmt"], #usernameEntry, #i0116')) return 'email_form';
            return 'unknown';
        }""")
    except Exception:
        return "unknown"


async def snap(page, tag, name):
    path = f"{SCREENSHOT_DIR}/{tag}_{name}.png"
    try: await page.screenshot(path=path)
    except Exception: pass
    url = page.url
    try: text = await page.evaluate("() => document.body.innerText")
    except Exception: text = ""
    state = classify(text, url)
    if state == "unknown":
        # 文本没命中(多为非 en/zh 出口 UI) -> DOM 结构兜底
        dom = await _dom_state(page)
        if dom != "unknown":
            state = dom
            print(f"    [{name}] {state}  {url[:60]}  (dom)")
            return state, text
    print(f"    [{name}] {state}  {url[:60]}")
    return state, text


# ── Skip passkey / FIDO setup ────────────────────────────────────────
async def skip_fido(page):
    for sel in ['button:has-text("Cancel")', 'button:has-text("取消")',
                'button:has-text("Skip")',   'button:has-text("Not now")',
                'button:has-text("Maybe later")', 'button:has-text("Do it later")']:
        try:
            btn = page.locator(sel).filter(
                has_not=page.locator('[aria-label="Close"],[data-testid="dismissIcon"]')
            ).first
            if await btn.count() > 0 and await btn.is_visible():
                txt = (await btn.text_content() or "").strip()
                print(f"    skip passkey: '{txt}'")
                await btn.click(timeout=5000)
                return True
        except Exception:
            pass
    try:
        await page.goto("https://account.microsoft.com/", timeout=20000,
                        wait_until="domcontentloaded")
        return True
    except Exception:
        pass
    return False


# ── Press-and-hold (same logic as register_outlook_standalone.py) ────
async def _captcha_visible(page):
    """页面上是否还有【可交互】的 PerimeterX 按住验证(按住按钮 / hsprotect iframe)。
    captcha 通过后会变成 Loading 转圈、这些元素消失 -> 返回 False。"""
    try:
        for sel in ['button:has-text("Press and hold")', 'button:has-text("Appuyer et maintenir")',
                    'button:has-text("按住")', 'button:has-text("长按")',
                    'button:has-text("Halten")', '#px-captcha']:
            el = page.locator(sel).first
            if await el.count() > 0:
                b = await el.bounding_box()
                if b and b['width'] > 30:
                    return True
        ifr = page.locator('iframe[src*="hsprotect.net"]')
        for hi in range(await ifr.count()):
            b = await ifr.nth(hi).bounding_box()
            if b and b['width'] > 50 and b['height'] > 30:
                return True
    except Exception:
        pass
    return False


async def _find_hold_target(page):
    """定位「按住」按钮，返回 (box, is_button)。

    诊断已确认：按钮是可见 hsprotect iframe 内的 #px-captcha 元素(高度约 42px)，
    page.locator 穿不进跨域 iframe，必须遍历 page.frames 在 frame 内取 #px-captcha
    的真实坐标。优先用它(is_button=True)，拿不到才退回整个 iframe 框按比例(is_button=False)。"""
    # 1) 主文档里的按住按钮 / #px-captcha(少见，但先试)
    for sel in ['button:has-text("Press and hold")', 'button:has-text("按住不放")',
                'button:has-text("长按")', '#px-captcha']:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                box = await btn.bounding_box()
                if box and box['width'] > 30 and box['height'] > 8:
                    return box, True
        except Exception:
            pass
    # 2) frame 内真按钮 #px-captcha(取可见的那个 frame：width>0)
    for f in page.frames:
        if f == page.main_frame or 'hsprotect.net' not in (f.url or ''):
            continue
        try:
            px = f.locator('#px-captcha').first
            if await px.count() > 0:
                box = await px.bounding_box()
                if box and box['width'] > 30 and box['height'] > 8:
                    return box, True
        except Exception:
            pass
    # 3) 退回整个可见 hsprotect iframe 框
    try:
        iframes = page.locator('iframe[src*="hsprotect.net"]')
        for i in range(await iframes.count()):
            box = await iframes.nth(i).bounding_box()
            if box and box['width'] > 50 and box['height'] > 30:
                return box, False
    except Exception:
        pass
    # 4) 其它 frame 里的通用按钮兜底
    for f in page.frames:
        if not f.url or f.url == "about:blank" or f == page.main_frame:
            continue
        try:
            for sel in ['#px-captcha', 'button[class*="hold"]', 'button']:
                btns = f.locator(sel)
                for bi in range(await btns.count()):
                    box = await btns.nth(bi).bounding_box()
                    if box and box['width'] > 30 and box['height'] > 20:
                        return box, False
        except Exception:
            pass
    return None, False


# ── Core unlock logic ─────────────────────────────────────────────────
async def unlock_account(page, context, email, password, tag):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # ── Step 1: Login ────────────────────────────────────────────────
    await page.goto("https://login.live.com/login.srf",
                    timeout=60000, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    net_err_count = 0
    email_stuck = 0   # 连续卡在 email_form 的轮数：微软静默拒绝的死号(填了邮箱点下一步仍回邮箱页)
    for i in range(20):
        state, _ = await snap(page, tag, f"L{i:02d}")
        if state == "logged_in":  return "already_ok"
        if state == "sms_verify": return "needs_phone"
        if state == "fido_setup":
            await skip_fido(page); await asyncio.sleep(4)
            return "unlocked"
        if state in ("locked", "px_challenge"): break
        if state == "net_error":
            net_err_count += 1
            if net_err_count >= 3: return "failed_net_error"
            await page.goto("https://login.live.com/login.srf",
                            timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3); continue
        # 死号早退：填了邮箱、点了下一步，却连续 6 轮(≈24s)仍停在 email_form 且没跳转 —
        # 说明微软静默拒绝这个号(常见于已封/Abuse 号)，不必空等 300s 超时。
        if state == "email_form":
            email_stuck += 1
            if email_stuck >= 6:
                print(f"    邮箱页卡满 {email_stuck} 轮、账号被静默拒绝，判死号跳过")
                return "dead_account"
        else:
            email_stuck = 0
        if state == "email_form":
            try:
                inp = page.locator('input[type="email"],input[name="loginfmt"]').first
                if await inp.count() > 0:
                    await inp.click(timeout=5000)  # focus input (触发 React SPA 状态)
                    await asyncio.sleep(0.3)
                    await inp.fill(email, timeout=15000)
                    await asyncio.sleep(0.5)
                    # 点击 Next/Submit 按钮（稳定 ID 优先，文本 fallback）
                    for sel in ['#idSIButton9', '#iNext', 'button[type="submit"]',
                                'input[type="submit"]']:
                        btn = page.locator(sel).first
                        if await btn.count() > 0 and await btn.is_visible():
                            await btn.click(timeout=5000)
                            break
                    else:
                        await page.keyboard.press("Enter")  # fallback
            except Exception as e:
                print(f"    fill(email) error: {e}")
            await asyncio.sleep(3); continue
        if state == "login_form":
            try:
                pwd = page.locator('input[type="password"]').first
                if await pwd.count() > 0 and not await pwd.input_value():
                    await pwd.click(timeout=5000)
                    await asyncio.sleep(0.3)
                    await pwd.fill(password, timeout=15000)
                    await asyncio.sleep(0.5)
                for sel in ['#idSIButton9', '#iNext', 'button[type="submit"]',
                            'input[type="submit"]']:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click(timeout=5000)
                        break
                else:
                    await page.keyboard.press("Enter")
            except Exception as e:
                print(f"    fill(pwd) error: {e}")
            await asyncio.sleep(3); continue
        for sel in ['#idSIButton9', 'button:has-text("Next")',
                    'input[type="submit"]', 'button[type="submit"]']:
            b = page.locator(sel).first
            if await b.count() > 0 and await b.is_visible():
                await b.click(timeout=8000); await asyncio.sleep(2); break
        else:
            await asyncio.sleep(2)

    state, _ = await snap(page, tag, "L_final")
    if state == "logged_in":  return "already_ok"
    if state == "sms_verify": return "needs_phone"
    if state == "fido_setup":
        await skip_fido(page); await asyncio.sleep(4)
        return "unlocked"

    # ── Step 2: PX press-and-hold + unlock flow ──────────────────────
    press_count   = 0
    max_press     = 5
    no_btn_rounds = 0
    px_api_tried  = False
    net_err_count = 0
    email_stuck   = 0
    last_state    = None
    oscillation   = 0   # locked ↔ error_page 振荡计数(Abuse 页死循环检测)

    for i in range(60):
        state, _ = await snap(page, tag, f"U{i:02d}")

        # Abuse 页 locked/error_page 振荡检测：连续 locked→error→locked→error 说明卡在不可解锁的 Abuse 页
        if state in ("locked", "error_page") and last_state in ("locked", "error_page") and state != last_state:
            oscillation += 1
            if oscillation >= 4:
                print(f"    locked/error_page 振荡 {oscillation} 次(Abuse 页不可解)，放弃")
                return "abuse_locked"
        else:
            oscillation = 0
        last_state = state

        if state == "logged_in":  return "unlocked"
        if state == "sms_verify": return "needs_phone"
        if state == "fido_setup":
            await skip_fido(page); await asyncio.sleep(4)
            return "unlocked"
        # 死号兜底：U 阶段也一直卡 email_form(登录没能推进) -> 判死号，别耗满 60 轮
        if state == "email_form":
            email_stuck += 1
            if email_stuck >= 8:
                print(f"    U 阶段邮箱页卡满 {email_stuck} 轮，判死号跳过")
                return "dead_account"
        else:
            email_stuck = 0
        if state == "error_page":
            tried = False
            for sel in ['button:has-text("Try again")', 'button:has-text("重试")',
                        'button:has-text("再试一次")', 'a:has-text("Try again")']:
                b = page.locator(sel).first
                if await b.count() > 0 and await b.is_visible():
                    try: await b.click(timeout=8000)
                    except Exception: pass
                    await asyncio.sleep(5); tried = True; break
            if not tried:
                await page.go_back(); await asyncio.sleep(3)
            continue
        if state == "net_error":
            net_err_count += 1
            if net_err_count >= 5: return "failed_net_error"
            await page.goto("https://login.live.com/login.srf",
                            timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3); continue

        if state == "locked":
            for sel in ['button[type="submit"]', 'button:has-text("Next")',
                        'button:has-text("下一步")', 'input[type="submit"]']:
                b = page.locator(sel).first
                if await b.count() > 0 and await b.is_visible():
                    try: await b.click(timeout=8000)
                    except Exception: pass
                    await asyncio.sleep(6); break
            continue

        if state == "px_challenge":
            if press_count < max_press:
                box, is_button = await _find_hold_target(page)
                if box:
                    press_count += 1
                    bx, by, bw, bh = box['x'], box['y'], box['width'], box['height']
                    if is_button:
                        # box 就是真按钮 #px-captcha：按其中心 + 小随机抖动
                        cx = bx + bw * random.uniform(0.40, 0.60)
                        cy = by + bh * random.uniform(0.40, 0.60)
                    else:
                        # 退回整个 iframe 框：按钮在中部窄带(实测 0.48-0.62 命中)
                        cx = bx + bw * random.uniform(0.42, 0.58)
                        cy = by + bh * random.uniform(0.48, 0.62)
                    print(f"    press #{press_count}: ({cx:.0f},{cy:.0f}){' [btn]' if is_button else ' [box]'}")

                    # 拟人按住(WindMouse 逼近 + OU 生理震颤)，取代旧的贝塞尔逼近 + 正弦漂移。
                    # is_done 复用 _captcha_visible 取反：进度条走满(按住按钮/iframe 消失)即松手。
                    async def _hold_done():
                        return not await _captcha_visible(page)

                    try:
                        held, passed_in_hold = await _hm.human_press_and_hold(
                            page, cx, cy, is_done=_hold_done,
                            max_hold=random.uniform(11.0, 15.0), min_hold=1.5,
                        )
                    except Exception as _he:
                        _msg = f"{type(_he).__name__}: {_he}"
                        print(f"    human_press_and_hold err: {_msg}")
                        if "closed" in _msg.lower() or "TargetClosed" in _msg:
                            held, passed_in_hold = 0.0, False
                        else:
                            try:
                                await page.mouse.down()
                                await asyncio.sleep(random.uniform(11.0, 14.0))
                                await page.mouse.up()
                            except Exception:
                                pass
                            held, passed_in_hold = 12.0, False
                    print(f"    held {held:.1f}s (#{press_count}){' (passed)' if passed_in_hold else ''}")
                    await asyncio.sleep(random.uniform(3, 6))
                    no_btn_rounds = 0
                else:
                    no_btn_rounds += 1
                    print(f"    no hold target (round {no_btn_rounds})")
                    await asyncio.sleep(3)
            elif not px_api_tried:
                px_api_tried = True
                print("    fallback: EZCaptcha PX API...")
                sol = solve_px(page_url=page.url)
                if sol:
                    for key in ['_pxCaptcha', '_px3', '_px2', '_pxhd', '_pxvid', '_pxde']:
                        if key in sol:
                            await context.add_cookies([{
                                "name": key, "value": str(sol[key]),
                                "domain": ".live.com", "path": "/"
                            }])
                    tok = sol.get("token") or sol.get("uuid")
                    if tok:
                        await page.evaluate(f"""() => {{
                            const h = document.querySelector('input[name="_pxCaptcha"]');
                            if (h) h.value = "{tok}";
                        }}""")
                    await page.reload(timeout=15000); await asyncio.sleep(5)
                else:
                    print("    PX API failed — giving up"); break
            else:
                print("    all PX attempts exhausted"); break
            continue

        # Generic next/submit for intermediate steps
        for sel in ['button[type="submit"]', 'button:has-text("Next")',
                    'button:has-text("下一步")', '#idSIButton9', 'input[type="submit"]']:
            b = page.locator(sel).first
            if await b.count() > 0 and await b.is_visible():
                await b.click(timeout=8000); await asyncio.sleep(4); break
        else:
            await asyncio.sleep(3)

    state, _ = await snap(page, tag, "U_final")
    if state == "logged_in":  return "unlocked"
    if state == "sms_verify": return "needs_phone"
    if state == "fido_setup":
        await skip_fido(page); await asyncio.sleep(4)
        return "unlocked"
    return f"failed_{state}"


# ── Worker ────────────────────────────────────────────────────────────
async def worker(accounts, proxy, worker_id, results, sem):
    async with sem:
        for email, password, raw_line in accounts:
            tag = f"w{worker_id}"
            pid = None
            print(f"\n[worker-{worker_id}] {email}")
            try:
                pid = create_browser(f"unlock_{worker_id}", proxy)
                ws  = open_browser(pid)
                if not ws:
                    raise Exception("no WS url from BitBrowser")

                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(ws)
                    ctx  = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = ctx.pages[0]       if ctx.pages       else await ctx.new_page()

                    outcome = await asyncio.wait_for(
                        unlock_account(page, ctx, email, password, tag),
                        timeout=UNLOCK_TIMEOUT
                    )

                print(f"[worker-{worker_id}] {email} => {outcome}")
                results.append((email, password, raw_line, outcome))

            except asyncio.TimeoutError:
                print(f"[worker-{worker_id}] {email} => timeout")
                results.append((email, password, raw_line, "timeout"))
            except Exception as e:
                print(f"[worker-{worker_id}] {email} => error: {e}")
                results.append((email, password, raw_line, f"error: {str(e)[:80]}"))
            finally:
                if pid:
                    close_browser(pid)
                    delete_browser(pid)


# ── File I/O ──────────────────────────────────────────────────────────
def load_accounts(path):
    accounts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split("----")
            if len(parts) >= 2:
                accounts.append((parts[0].strip(), parts[1].strip(), line))
            else:
                print(f"[warn] skip: {line[:60]}")
    return accounts

def scan_all_accounts():
    """Scan outlook_accounts/ for all registered accounts, skip those already unlocked."""
    reg_dir = "outlook_accounts"
    unlock_dir = "unlock_results"

    # Collect already-unlocked emails
    unlocked_emails = set()
    if os.path.isdir(unlock_dir):
        for uf in os.listdir(unlock_dir):
            if uf.startswith("unlocked_clean_") and uf.endswith(".txt"):
                with open(os.path.join(unlock_dir, uf), "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split("----")
                        if parts and parts[0]:
                            unlocked_emails.add(parts[0].lower())

    # Collect all registered accounts, deduplicate by email, skip unlocked
    seen = set()
    accounts = []
    if os.path.isdir(reg_dir):
        for af in sorted(os.listdir(reg_dir)):
            if af.startswith("accounts_") and af.endswith(".txt"):
                with open(os.path.join(reg_dir, af), "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"): continue
                        parts = line.split("----")
                        if len(parts) >= 2:
                            email_lc = parts[0].lower()
                            if email_lc not in seen and email_lc not in unlocked_emails:
                                accounts.append((parts[0].strip(), parts[1].strip(), line))
                                seen.add(email_lc)

    if unlocked_emails:
        print(f"[auto] Skipping {len(unlocked_emails)} already-unlocked accounts")
    print(f"[auto] {len(accounts)} new accounts to unlock from {reg_dir}/")
    return accounts

def _clash_browser_proxy():
    """把 CLASH_PROXY(http://127.0.0.1:7897) 转成 BitBrowser 能用的 host:port 代理串。
    这样浏览器出口走 Clash 当前节点，而不是宿主原始(常为 AWS 机房)IP —— 否则
    login.live.com 被 Cloudflare/PerimeterX 全页拦成空 body，解锁流程根本进不去登录表单。"""
    raw = (os.environ.get("CLASH_PROXY", "") or "").strip()
    if not raw:
        return None
    m = re.match(r'^(?:https?://)?(?:.*@)?([^:/@]+):(\d+)', raw)
    if not m:
        return None
    return f"http://{m.group(1)}:{m.group(2)}"


def load_proxies(path):
    # 显式指定代理文件优先
    if path:
        if not os.path.exists(path):
            return DEFAULT_PROXIES or [_clash_browser_proxy()] or [None]
        proxies = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
        return proxies or DEFAULT_PROXIES or [_clash_browser_proxy()] or [None]
    # 未指定：住宅池(OUTLOOK_PROXIES) > Clash 出口 > 无代理
    if DEFAULT_PROXIES:
        return DEFAULT_PROXIES
    clash = _clash_browser_proxy()
    if clash:
        print(f"[proxy] no residential pool, routing browser via Clash: {clash}")
        return [clash]
    print("[proxy] WARNING: no proxy — browser exits on host IP; "
          "login.live.com may be blocked (blank page). Set CLASH_PROXY or OUTLOOK_PROXIES.")
    return [None]

def save_results(results, ts):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    unlocked   = [r for r in results if r[3] in ("unlocked", "already_ok")]
    needs_ph   = [r for r in results if r[3] == "needs_phone"]
    dead       = [r for r in results if r[3] == "dead_account"]
    abuse      = [r for r in results if r[3] == "abuse_locked"]
    failed     = [r for r in results if r[3] not in ("unlocked", "already_ok", "needs_phone", "dead_account", "abuse_locked")]

    def write(name, rows):
        if not rows:
            return
        p = os.path.join(OUTPUT_DIR, f"{name}_{ts}.txt")
        with open(p, "w", encoding="utf-8") as f:
            for email, password, raw, outcome in rows:
                f.write(f"{raw}----{outcome}\n")
        print(f"  {name:<22s} {len(rows):4d}  -> {p}")

    print(f"\n{'='*55}")
    write("unlocked", unlocked)
    write("needs_phone", needs_ph)
    write("dead_account", dead)
    write("abuse_locked", abuse)
    write("failed", failed)
    print(f"{'─'*55}")
    print(f"  Total     : {len(results)}")
    print(f"  Unlocked  : {len(unlocked)}")
    print(f"  NeedsPhone: {len(needs_ph)}")
    print(f"  DeadAcct  : {len(dead)}")
    print(f"  AbuseLock : {len(abuse)}")
    print(f"  Failed    : {len(failed)}")
    print(f"{'='*55}")

    # Convenience: write just email----password for unlocked accounts
    ok_path = os.path.join(OUTPUT_DIR, f"unlocked_clean_{ts}.txt")
    with open(ok_path, "w", encoding="utf-8") as f:
        for email, password, _, _ in unlocked:
            f.write(f"{email}----{password}\n")
    if unlocked:
        print(f"\n  Clean unlocked list: {ok_path}")


# ── Main ──────────────────────────────────────────────────────────────
def find_latest_input():
    """Auto-find most recent accounts file to unlock."""
    for d, pat in [
        ("check_results",   "locked_for_unlock_"),
        ("outlook_accounts","accounts_"),
    ]:
        if not os.path.isdir(d): continue
        files = sorted(
            [f for f in os.listdir(d) if f.startswith(pat) and f.endswith(".txt")],
            reverse=True
        )
        if files:
            return os.path.join(d, files[0])
    return None

async def run(accounts_or_file, proxies, concurrency):
    if isinstance(accounts_or_file, str):
        accounts = load_accounts(accounts_or_file)
        label = accounts_or_file
    else:
        accounts = accounts_or_file
        label = f"(auto-scanned, {len(accounts)} accounts)"

    if not accounts:
        print("[error] no accounts found"); return

    print(f"Input     : {label}")
    print(f"Accounts  : {len(accounts)}")
    print(f"Concurrency: {concurrency}")
    print(f"Proxies   : {len(proxies)}")

    results = []
    sem     = asyncio.Semaphore(concurrency)
    chunks  = [[] for _ in range(concurrency)]
    for i, acc in enumerate(accounts):
        chunks[i % concurrency].append(acc)

    await asyncio.gather(*[
        worker(chunks[i], proxies[i % len(proxies)], i, results, sem)
        for i in range(concurrency)
        if chunks[i]
    ])

    save_results(results, datetime.now().strftime("%Y%m%d_%H%M%S"))

def main():
    parser = argparse.ArgumentParser(
        description="Batch Outlook Account Unlock",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python unlock_outlook.py --input outlook_accounts/accounts_20260414_124527.txt
  python unlock_outlook.py --input emails_locked.txt --concurrency 2
  python unlock_outlook.py --input emails.txt --proxy-file proxies.txt --concurrency 3
  python unlock_outlook.py                          (auto-scan all accounts, skip unlocked)
""")
    parser.add_argument("--input", "-i", default=None,
        help="Input file (email----password per line). "
             "Auto-scans outlook_accounts/ and skips already-unlocked if omitted.")
    parser.add_argument("--proxy-file", "-p", default=None,
        help="Proxy list file (one per line)")
    parser.add_argument("--concurrency", "-c", type=int, default=1,
        help="Parallel workers (default: 1)")
    args = parser.parse_args()

    if args.input:
        if not os.path.exists(args.input):
            print(f"[error] file not found: {args.input}")
            sys.exit(1)
        accounts_or_file = args.input
    else:
        # Auto-scan all accounts, skip already unlocked
        accounts_or_file = scan_all_accounts()
        if not accounts_or_file:
            print("[info] No new accounts to unlock.")
            sys.exit(0)

    proxy_env = ensure_clash_proxy_env()
    if proxy_env:
        print(f"  proxy env ready: {proxy_env}")

    cleanup_stale_browsers()
    proxies = load_proxies(args.proxy_file)
    asyncio.run(run(accounts_or_file, proxies, args.concurrency))

if __name__ == "__main__":
    main()
