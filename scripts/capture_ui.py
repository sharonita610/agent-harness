#!/usr/bin/env python3
"""README 화면 캡처. 헤드리스 Chrome(CDP)으로 네 비서를 실제로 돌려 찍는다. 가짜 화면이 아니다.

사용:
  uv run --with websocket-client scripts/capture_ui.py            # docs/img/ui-*.jpg 갱신
  uv run --with websocket-client scripts/capture_ui.py --out /tmp/shots --home http://127.0.0.1:8790

전제: 총괄 비서(8790, HARNESS_WORKSPACE 에 하위 비서 3개)·하위 비서 3개·모델 서버가 켜져 있다.
Chrome 은 전용 프로필(임시 폴더)로 따로 띄우고 끝나면 그 프로세스만 끈다. 한 번 도는 데 3~5분.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

CHROME_CANDIDATES = [
    os.getenv("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
]


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def wait_for(self, expr: str, timeout: float, what: str):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.evaluate(expr):
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"{what}: {timeout}초 안에 되지 않음")

    def shot(self, path: Path, clip: dict | None = None, tall: bool = False):
        """tall 이면 작업지 스크롤을 풀어 페이지를 늘린 뒤 보이는 영역 너머까지 찍는다(긴 작업지 한 장 통째로)."""
        params = {"format": "jpeg", "quality": 82}
        if tall:
            h = self.evaluate(
                "(() => { const sh = document.querySelector('.shell'), d = document.querySelector('.desk'); sh.style.height = 'auto'; d.style.overflow = 'visible'; "
                "document.documentElement.style.height = 'auto'; document.body.style.height = 'auto'; "
                "const s = d.querySelector('.sheets'); return Math.ceil(s ? s.getBoundingClientRect().bottom : d.scrollHeight); })()"
            )
            clip = {
                **(clip or DESK),
                "height": min(int(h) + 24, 2400),
            }  # 레일(기록 20건)이 아니라 작업지 끝까지만(.sheets 가 없는 전면 화면은 .desk 끝까지)
            params["captureBeyondViewport"] = True
        if clip:
            params["clip"] = {**clip, "scale": 1}  # deviceScaleFactor 2 가 이미 적용돼 있어 여기서 또 키우면 4배가 된다
        data = self.send("Page.captureScreenshot", **params)["data"]
        if tall:
            self.evaluate(
                "(() => { const sh = document.querySelector('.shell'), d = document.querySelector('.desk'); sh.style.height = ''; d.style.overflow = ''; "
                "document.documentElement.style.height = ''; document.body.style.height = ''; return true; })()"
            )
        path.write_bytes(base64.b64decode(data))
        print(f"  저장 {path} ({path.stat().st_size // 1024} KB)")


def launch_chrome(port: int, profile: Path) -> subprocess.Popen:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
            sys.exit(
                f"포트 {port} 에 이미 다른 Chrome 이 있다. --port 로 다른 값을 주세요(남의 브라우저에 붙지 않는다)"
            )
    except SystemExit:
        raise
    except Exception:
        pass
    exe = next((c for c in CHROME_CANDIDATES if c and (os.path.exists(c) or shutil.which(c))), None)
    if not exe:
        sys.exit("Chrome 을 찾지 못함. CHROME=/path/to/chrome 으로 지정")
    args = [
        exe,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--window-size=1440,900",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--remote-allow-origins=*",
        "--force-color-profile=srgb",
        "--lang=ko-KR",
        "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as r:
                targets = json.load(r)
            page = next(t for t in targets if t.get("type") == "page")
            return proc, page["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    sys.exit("Chrome 디버그 포트가 열리지 않음")


JS_PICK = "(n => { const b = [...document.querySelectorAll('.agent')].find(b => b.textContent.includes(n)); if (!b) return false; b.click(); return true; })(%s)"
JS_PICKED = "(n => (document.querySelector('.deskhead h1')?.textContent || '').includes(n) && !!document.querySelector('.examples button'))(%s)"
JS_ASK = "(l => { const b = [...document.querySelectorAll('.examples button')].find(b => b.textContent.trim() === l); if (!b || b.disabled) return false; b.click(); return true; })(%s)"
JS_DONE = "(() => { const s = document.querySelector('.sheet'); return !!s && !!s.querySelector('.answer:not(.waiting)') && !s.querySelector('.caret') && /초/.test(s.querySelector('.process summary')?.textContent || ''); })()"
JS_SLIP = "!!document.querySelector('.sheet .slip .actions button.primary')"
JS_VIEW = "(n => { const b = [...document.querySelectorAll('.views .agent')].find(b => b.textContent.includes(n)); if (!b) return false; b.click(); return true; })(%s)"
FULL = {"x": 0, "y": 0, "width": 1440, "height": 900}
DESK = {"x": 224, "y": 0, "width": 904, "height": 720}
RAIL = {"x": 1128, "y": 0, "width": 312, "height": 400}
DRAWER = {"x": 680, "y": 0, "width": 760, "height": 900}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default="http://127.0.0.1:8790", help="총괄 비서 주소(이 화면에서 다른 비서로 전환한다)")
    ap.add_argument("--out", default="docs/img")
    ap.add_argument("--port", type=int, default=9377, help="CDP 포트. 이미 쓰는 중이면(다른 Chrome) 멈춘다")
    ap.add_argument(
        "--steps",
        default="1234dihq567",
        help="찍을 단계(기본 전부: 1~4 작업지, d 데이터 탭, i 결재함 탭, h 기록 탭, q 큐 보드, 5~7 결재·도장·드로어). 5~7 과 i 는 1·4 가 먼저 돌아야 한다",
    )
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="harness-capture-"))
    proc, ws_url = launch_chrome(a.port, profile)
    try:
        c = CDP(ws_url)
        c.send("Page.enable")
        c.send("Runtime.enable")
        c.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=2, mobile=False)
        c.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        c.send("Page.navigate", url=a.home.rstrip("/") + "/app/?theme=light")
        c.wait_for(
            "!!document.querySelector('.agent.on') && !!document.querySelector('.examples button')", 20, "화면 로드"
        )

        def pick(name: str):
            # 비서 목록(/workspace)은 화면보다 늦게 올 수 있다. 버튼이 생길 때까지 기다린 뒤 누른다.
            c.wait_for(
                "(n => [...document.querySelectorAll('.agent')].some(b => b.textContent.includes(n)))(%s)"
                % json.dumps(name),
                20,
                f"{name} 비서 버튼",
            )
            assert c.evaluate(JS_PICK % json.dumps(name)), f"비서 없음: {name}"
            c.wait_for(JS_PICKED % json.dumps(name), 15, f"{name} 전환")
            time.sleep(0.6)

        def ask(label: str, until: str = JS_DONE, timeout: float = 150):
            assert c.evaluate(JS_ASK % json.dumps(label)), f"예시 없음: {label}"
            print(f"  질문: {label}")
            c.wait_for(until, timeout, f"{label} 완료")
            time.sleep(0.8)

        steps = set(a.steps)
        if "1" in steps:
            print("1. 총괄 비서: 회사 + 회로")
            pick("총괄")
            ask("회사 + 회로")
            c.shot(out / "ui-supervisor.jpg", tall=True)
        if "2" in steps:
            print("2. 회사 비서: 조회 + 규정")
            pick("한빛")
            ask("재고 부족 지연과 책임")
            c.shot(out / "ui-query.jpg", tall=True)
        if "3" in steps:
            print("3. 회로 비서: 스윕 그래프")
            pick("회로")
            ask("저항 스윕 그래프", timeout=300)
            c.shot(out / "ui-spice-sweep.jpg", tall=True)
        if "4" in steps:
            print("4. 공정 비서: 티켓(결재 대기)")
            pick("공정")
            ask("티켓 만들기", until=JS_SLIP, timeout=120)
            c.shot(out / "ui-approval-slip.jpg", tall=True)

        def view(name: str, ready: str, timeout: float = 20):
            assert c.evaluate(JS_VIEW % json.dumps(name)), f"보기 없음: {name}"
            c.wait_for(ready, timeout, f"{name} 탭")
            time.sleep(0.8)

        if "d" in steps:
            print("d. 공정 비서 데이터 탭(비서 없이 원 데이터)")
            pick("공정")
            view(
                "데이터",
                "document.querySelectorAll('.panels .panel').length >= 5 && !!document.querySelector('.chart svg')",
            )
            c.shot(out / "ui-data-factory.jpg", FULL, tall=True)
            view("작업지", "!!document.querySelector('.compose')")
        if "i" in steps:
            print("i. 결재함 탭(모든 비서의 대기 전표 + 처리 내역)")
            pick("총괄")
            view("결재함", "!!document.querySelector('.slips .slip') && !!document.querySelector('table.data.wide')")
            c.shot(out / "ui-inbox.jpg", FULL)
            view("작업지", "!!document.querySelector('.compose')")
        if "h" in steps:
            print("h. 기록 탭")
            pick("공정")
            view("기록", "document.querySelectorAll('tr.rowbtn').length >= 3")
            c.shot(out / "ui-history.jpg", FULL)
            view("작업지", "!!document.querySelector('.compose')")
        if "q" in steps:
            print("q. 큐 보드: 회로 비서에게 스윕 두 개를 맡기고 진행 막대가 찰 때 찍는다")
            pick("회로")
            view("맡긴 작업", "!!document.querySelector('.compose textarea')")
            for qtext in (
                "rc_lowpass 샘플에서 R1 을 100Ω부터 10kΩ까지 로그 간격 9점으로 스윕해서 차단 주파수 변화를 표로 보여줘",
                "rc_lowpass 샘플에서 C1 을 10nF부터 1µF까지 로그 간격 7점으로 스윕해서 차단 주파수 변화를 표로 보여줘",
            ):
                c.evaluate(
                    "(t => { const ta = document.querySelector('.compose textarea'); const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; "
                    "set.call(ta, t); ta.dispatchEvent(new Event('input', { bubbles: true })); return true; })(%s)"
                    % json.dumps(qtext)
                )
                time.sleep(0.3)
                c.evaluate("document.querySelector('.compose button.primary').click(); true")
                time.sleep(1.0)
            c.wait_for(
                "!!document.querySelector('.qbar.running .qfill') && !!document.querySelector('.qbar.queued')",
                150,
                "진행 막대",
            )
            time.sleep(0.6)
            c.shot(out / "ui-queue.jpg", FULL)
            c.wait_for(
                "!document.querySelector('.qbar.running') && !document.querySelector('.qbar.queued')",
                300,
                "스윕 둘 다 끝남",
            )
            view("작업지", "!!document.querySelector('.compose')")
        if "5" in steps:
            print("5. 총괄 비서 화면에서 결재함에 공정 전표가 보이는 전체 화면")
            pick("총괄")
            c.wait_for("!!document.querySelector('.rail .slip .actions button.primary')", 15, "결재함 전표")
            time.sleep(0.5)
            c.shot(out / "ui-desk.jpg")
        if "6" in steps:
            print("6. 결재함에서 승인 → 도장")
            c.evaluate("document.querySelector('.rail .slip .actions button.primary').click(); true")
            c.wait_for("!!document.querySelector('.rail .slip .stamp')", 10, "도장")
            time.sleep(0.5)
            c.shot(out / "ui-approval-stamp.jpg", RAIL)
        if "7" in steps:
            print("7. 실행 상세 드로어")
            c.evaluate("document.querySelector('.sheet .top button').click(); true")
            c.wait_for("!!document.querySelector('.drawer .trace .r')", 20, "드로어")
            time.sleep(0.5)
            c.shot(out / "ui-trace.jpg", DRAWER)
        print("완료")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
