"""Local web console for the project-guide self-development seed."""
import argparse
import fcntl
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
from urllib.parse import parse_qs, urlsplit, urlencode
from uuid import UUID

import seed_engine as engine
import seed_store as store
import seed_stage as stage_rules
import seed_stage_web as stage_web
import seed_project as ctx

LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
WAKE = threading.Event()
LABEL = {"queued": "等待执行", "working": "正在执行", "planned": "已规划，Worker 执行中",
         "waiting": "等待你裁定", "answered": "已回复，等待接手", "failed": "执行中断",
         "review": "等待验收", "activated": "已启用", "closed": "已关闭"}
FIELDS = {"goal": "目标", "acceptance": "验收条件", "plan": "主 Agent 计划", "base": "基线提交",
          "checkpoint": "交接记录", "question": "需要裁定", "recommendation": "建议",
          "reply": "你的回复", "error": "中断原因", "candidate": "候选提交", "diff": "改动",
          "summary": "Worker 交付说明", "evidence": "集成验证"}


def coordinate_once(boot):
    with LOCK:
        jobs = store.journal()
        stage = store.stage_get()
        todo = stage_rules.select_task(jobs, stage)
        allowed = engine.git(engine.ROOT, "rev-parse", "HEAD") == boot
    if not allowed:
        return False
    if stage and stage["state"] == "pending":
        engine.plan_stage(stage)
        return True
    if todo:
        engine.advance(*todo, boot)
        return True
    import seed_recovery
    recovery = seed_recovery.next_recovery(jobs, stage)
    if recovery:
        with LOCK:
            store.append(recovery[0], recovery[1], 'answered', recovery[2])
        return True
    return False


def restart_if_idle(server):
    """Re-exec only between coordinator runs and after mutating HTTP responses finish."""
    with LOCK:
        if getattr(server, "pending_posts", 0):
            return False
        if engine.git(engine.ROOT, "rev-parse", "HEAD") == server.boot:
            return False
        print("已启用新版本，正在自动重启工作台。", flush=True)
        # exec closes the old listener and file lock before main reacquires them.
        # PostgreSQL, environment, CLI arguments and the current PID are preserved.
        os.execv(sys.executable, [sys.executable, "-B", os.path.abspath(__file__), *sys.argv[1:]])
    return True


def coordinate(boot, server=None):
    while True:
        try:
            if coordinate_once(boot):
                continue
            if server is not None and restart_if_idle(server):
                return
        except Exception as exc:
            print(f"调度失败：{exc}", flush=True)
        WAKE.wait(4)
        WAKE.clear()


def esc(value):
    return html.escape(str(value), quote=True)


def form(action, label, fields="", task="", seq=0):
    hidden = {"token": TOKEN, "action": action, "task": task, "seq": seq}
    return ('<form method="post" action="/action">' +
            ''.join(f'<input type="hidden" name="{k}" value="{esc(v)}">' for k, v in hidden.items())
            + fields + f'<button>{label}</button></form>')


def field(name, label):
    return f'<label>{label}<textarea required maxlength="12000" name="{name}"></textarea></label>'


def material_signature():
    # Older isolated service clients provide only the recovery domain contract.
    # Keep the web adapter outside startup/coordinator dependencies; a complete
    # domain always loads it, and import/domain failures there remain visible.
    if not hasattr(ctx, 'list_documents'):
        return None
    import seed_material_web
    return seed_material_web.signature(ctx.Project(root=engine.ROOT))


def revision(jobs, stage, current, boot):
    import seed_ui
    signature = material_signature()
    if signature is None:
        return seed_ui.revision(jobs, stage, current, boot)
    return seed_ui.revision(jobs, stage, current, boot, signature)


def page(jobs, boot, stage=None, params=None, error="", materials_signature=None):
    import seed_ui
    current_main = engine.git(engine.ROOT, "rev-parse", "refs/heads/main")
    return seed_ui.render(jobs, boot, current_main, stage, params, error,
                          form, field, FIELDS, materials_signature)


def action(params, boot):
    def value(k):
        v = params.get(k, [""])[0].strip()
        if not v or len(v) > 12000:
            raise ValueError("缺少必要内容或输入过长")
        return v

    kind = value("action")
    if kind.startswith('material_'):
        import seed_material_web as materials
        return materials.act(ctx.Project(root=engine.ROOT), params)
    if kind.startswith('stage_'):
        if kind == 'stage_create':
            store.stage_create(value('goal'), value('acceptance'))
            return
        ident, version = value('stage_id'), int(value('version'))
        if kind == 'stage_save':
            cards = stage_web.cards_from(params)
            stage_rules.validate(cards)
            current = store.stage_get()
            if not current or current['id'] != ident or current['version'] != version:
                raise ValueError('阶段版本已更新，请核对最新版本；输入已保留')
            if params.get('confirm') == ['yes'] and cards == current['cards']:
                store.stage_authorize(ident, version)
            else:
                store.stage_save(ident, version, cards)
        elif kind == 'stage_retry':
            store.stage_update(ident, version, 'pending', {'error': ''}, ['failed'])
        elif kind == 'stage_accept':
            store.stage_accept(ident, version, value('note'), value('passed') == 'yes')
        else:
            raise ValueError('未知阶段动作')
        return
    if kind == "create":
        store.create(value("goal"), value("acceptance"))
        return
    task = value("task")
    events = store.journal().get(task, [])
    if not events or events[-1]["seq"] != int(value("seq")):
        raise ValueError("任务已更新，请刷新")
    last = events[-1]
    state, seq = last["state"], last["seq"]
    if kind == 'decision' and state == 'waiting':
        import seed_decision
        data = stage_rules.merged(events)
        choice = value('choice')
        reply = seed_decision.answer(data, choice, params.get('other', [''])[0])
        store.append(task, seq, 'answered', {'reply': reply, 'decision_choice': choice})
    elif kind == "reply" and state in ("waiting", "failed", "review"):
        store.append(task, seq, "answered", {"reply": value("reply")})
    elif kind == "close" and state in ("waiting", "failed", "review", "queued"):
        store.append(task, seq, "closed", {})
    elif kind == "activate" and state == "review":
        data = {k: v for e in events for k, v in e["data"].items()}
        engine.activate(data)
        store.append(task, seq, "activated", {"candidate": data["candidate"]})
    else:
        raise ValueError("当前状态不允许该动作")


class Console(BaseHTTPRequestHandler):
    def local(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",
                                             f"localhost:{self.server.server_port}")

    def material_page(self, params=None, error='', notice='', edited=None, area='context'):
        import seed_ui
        import seed_material_web as materials
        project = ctx.Project(root=engine.ROOT)
        body = materials.render(project, TOKEN, params, error, notice, edited, area)
        return seed_ui.material_page(body, seed_ui.revision(store.journal(), store.stage_get(),
            engine.git(engine.ROOT, 'rev-parse', 'refs/heads/main'), self.server.boot, materials.signature(project)))

    def do_GET(self):
        if not self.local():
            return self.send_error(404)
        content_type = "text/html; charset=utf-8"
        try:
            if self.path == "/api/revision":
                signature = revision(store.journal(), store.stage_get(),
                    engine.git(engine.ROOT, "rev-parse", "refs/heads/main"), self.server.boot)
                data = json.dumps(signature).encode()
                content_type = "application/json; charset=utf-8"
            elif self.path.startswith("/diff/"):
                task = str(UUID(self.path.removeprefix("/diff/")))
                events = store.journal().get(task, [])
                if not events or events[-1]["state"] != "review":
                    return self.send_error(404)
                records = {k: v for event in events for k, v in event["data"].items()}
                diff = engine.git(engine.ROOT, "diff", "--no-ext-diff", "--no-textconv",
                                  records["base"], records["candidate"])
                data = ('<meta charset="utf-8"><pre>' + esc(diff) + '</pre>').encode()
            elif urlsplit(self.path).path == '/materials':
                query = parse_qs(urlsplit(self.path).query)
                area = query.get('area', ['context'])[0]
                if area not in ('context', 'agents'):
                    return self.send_error(404)
                notice = '草稿已保存，尚未生效' if query.get('saved') == ['1'] else ''
                data = self.material_page(area=area, notice=notice).encode()
            elif self.path == "/":
                data = page(store.journal(), self.server.boot, store.stage_get(),
                            materials_signature=material_signature()).encode()
            else:
                return self.send_error(404)
        except ValueError:
            return self.send_error(404)
        except RuntimeError as exc:
            return self.send_error(503, explain=str(exc))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        with LOCK:
            self.server.pending_posts = getattr(self.server, "pending_posts", 0) + 1
        try:
            self.handle_action()
        finally:
            with LOCK:
                self.server.pending_posts -= 1

    def handle_action(self):
        if not self.local() or self.path != "/action":
            return self.send_error(404)
        params = {}
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 260000:
                return self.send_error(413)
            params = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
            if not secrets.compare_digest(params.get("token", [""])[0], TOKEN):
                return self.send_error(403)
            with LOCK:
                result = action(params, self.server.boot)
            if result is not None and params.get('action', [''])[0].startswith('material_'):
                notice, edited = result
                data = self.material_page(params, notice=notice, edited=edited,
                                          area=params.get('area', ['context'])[0]).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            WAKE.set()
        except (ValueError, RuntimeError) as exc:
            if params.get('action', [''])[0].startswith('material_'):
                data = self.material_page(params, error=str(exc), area=params.get('area', ['context'])[0]).encode()
                self.send_response(409)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            if params.get('action', [''])[0].startswith('stage_'):
                data = page(store.journal(), self.server.boot, store.stage_get(), params, str(exc)).encode()
                self.send_response(409)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            return self.send_error(409, explain=str(exc))
        self.send_response(303)
        location = '/'
        if params.get('action', [''])[0].startswith('material_'):
            location = '/materials?' + urlencode({'area': params.get('area', ['context'])[0],
                'saved': '1' if params['action'] == ['material_save'] else '0'})
        self.send_header("Location", location)
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    engine.RUNTIME.mkdir(exist_ok=True)
    with (engine.RUNTIME / "server.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.init()
        boot = engine.git(engine.ROOT, "rev-parse", "HEAD")
        with ThreadingHTTPServer(("127.0.0.1", args.port), Console) as server:
            for task, rows in store.journal().items():
                if rows[-1]["state"] in ("working", "planned"):
                    store.append(task, rows[-1]["seq"], "failed",
                                 {"error": "服务中断。请确认旧 Worker 已退出，检查保留的工作树后重试。"})
            store.stage_recover()
            ctx.Project().recover_publications()
            boot = engine.git(engine.ROOT, "rev-parse", "HEAD")
            server.boot = boot
            threading.Thread(target=coordinate, args=(boot, server), daemon=True).start()
            print(f"Project Guide http://127.0.0.1:{args.port} · {boot[:9]}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
