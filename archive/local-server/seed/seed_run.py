"""Bounded public activity records and durable logs for a single CLI invocation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from uuid import uuid4


class RunError(RuntimeError):
    def __init__(self, message, record):
        super().__init__(message)
        self.record = record


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stop(process):
    # A descendant may still hold stdout after the original CLI exits.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def activity(line):
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    item = event.get("item") or {}
    if not isinstance(item, dict):
        return None
    # Never copy command contents, tool arguments, model reasoning or messages into the UI.
    names = {"command_execution": "命令执行", "file_change": "文件修改",
             "mcp_tool_call": "工具调用", "web_search": "检索", "agent_message": "完成消息"}
    if kind in ("item.started", "item.completed") and item.get("type") in names:
        return names[item["type"]] + ("开始" if kind == "item.started" else "结束")
    return {"thread.started": "会话已建立", "turn.started": "模型开始处理",
            "turn.completed": "本轮模型调用完成", "turn.failed": "本轮模型调用失败"}.get(kind)


def execute(args, cwd, prompt, log_dir, phase, timeout=1200, on_progress=None):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    record = {"run_id": run_id, "phase": phase, "started_at": utc(),
              "last_activity_at": None, "activity": "等待模型活动", "worktree": str(cwd),
              "log_path": str(log_dir / (run_id + ".jsonl"))}
    start = time.monotonic()
    last_publish = start

    def publish():
        record["elapsed_seconds"] = int(time.monotonic() - start)
        if on_progress:
            on_progress(dict(record))

    publish()
    published_activity = (record["activity"], record["last_activity_at"])
    process = None
    try:
        with open(record["log_path"], "xb", opener=lambda p, f: os.open(p, f, 0o600)) as log:
            process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                process.stdin.write(prompt.encode())
                process.stdin.close()
            except BrokenPipeError:
                pass
            pending = b""
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() - start >= timeout:
                        raise TimeoutError(f"Agent 超过 {timeout:g} 秒，已中止本次运行")
                    for key, _ in selector.select(timeout=min(.2, max(.01, timeout))):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        log.write(chunk)
                        log.flush()
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            summary = activity(line)
                            if summary:
                                record.update(activity=summary, last_activity_at=utc())
                        # Discard unusually large partial lines from memory. The log keeps full bytes.
                        if len(pending) > 262144:
                            pending = b""
                    now = time.monotonic()
                    current_activity = (record["activity"], record["last_activity_at"])
                    if now - last_publish >= 2 and current_activity != published_activity:
                        publish()
                        published_activity = current_activity
                        last_publish = now
            remaining = max(.01, timeout - (time.monotonic() - start))
            code = process.wait(timeout=remaining)
            if code:
                raise RuntimeError(f"Agent 退出码 {code}，请检查本次运行日志")
        record["outcome"] = "completed"
        publish()
        return record
    except Exception as exc:
        record.update(outcome="interrupted", error=str(exc))
        publish()
        raise RunError(str(exc), dict(record)) from exc
    finally:
        if process:
            stop(process)
            process.stdout.close()
