"""One local coordinator, a read-only planner and a worktree-scoped Codex worker."""
from contextlib import contextmanager
import fcntl
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import io
from uuid import uuid4

import seed_stage as stage_rules

import seed_store as store
import seed_run

PROGRESS = ContextVar("seed_progress", default=None)

# 代码已归档；Git 操作、项目资料和 .seed/ 运行目录仍以仓库根目录为准。
ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / ".seed"


def run(args, cwd, timeout=120, stdin=None):
    result = subprocess.run(args, cwd=cwd, input=stdin, text=True, capture_output=True,
                            timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if result.returncode:
        raise RuntimeError((result.stderr + result.stdout)[-3500:] or f"{args[0]} failed")
    return result.stdout.strip()


def git(cwd, *args):
    return run(["git", *args], cwd)


def model(cwd, prompt, writable, output_schema=None):
    with tempfile.TemporaryDirectory() as tmp:
        answer = Path(tmp) / "answer"
        fields = ("status", "summary", "question", "checkpoint", "recommendation", "decision_json") if writable else ("plan",)
        properties = {key: {"type": "string"} for key in fields}
        if writable:
            properties["status"]["enum"] = ["question", "done"]
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(output_schema or {"type": "object", "properties": properties,
                                      "required": list(fields), "additionalProperties": False}))
        seed_run.execute(["codex", "exec", "--json", *(["--skip-git-repo-check"] if output_schema else []),
             "-s", "workspace-write" if writable else "read-only",
             "--output-schema", str(schema), "--output-last-message", str(answer), "-"], cwd, prompt, RUNTIME / "logs",
                         "Worker" if writable else "主 Agent",
                         timeout=float(os.environ.get("SEED_AGENT_TIMEOUT", "1200")),
                         on_progress=PROGRESS.get())
        return json.loads(answer.read_text())


def decision_data(result):
    try:
        value = json.loads(result.get('decision_json') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def branch(task):
    return f"seed/{task}"


def checkout(task, base):
    path = RUNTIME / "trees" / task
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        git(ROOT, "worktree", "add", "-b", branch(task), str(path), base)
    if git(path, "branch", "--show-current") != branch(task):
        raise RuntimeError("工作树分支与任务不匹配")
    return path


def verify(path, base):
    # The candidate must still satisfy the original test suite even if it edits tests.
    # 新归档和旧提交各用各自的目录；外部仓库仍按原来的根目录验收。
    relative = next((name for name in ("archive/local-server/seed", "seed") if (path / name / "seed.py").is_file()), None)
    home, scope = (path / relative, ["--", relative]) if relative else (path, [])
    with tempfile.TemporaryDirectory() as tmp:
        originals = [name for name in git(path, "ls-tree", "-r", "--name-only", base, *scope).splitlines()
                     if Path(name).name.startswith("test_") and name.endswith(".py")]
        if not originals:
            raise RuntimeError("基线没有可执行的验收测试")
        for name in originals:
            (Path(tmp) / Path(name).name).write_text(git(path, "show", f"{base}:{name}") + "\n")
        script = ("import sys,unittest; sys.path.insert(0,sys.argv[1]); "
                  "s=unittest.defaultTestLoader.discover(sys.argv[2]); "
                  "sys.exit(not unittest.TextTestRunner().run(s).wasSuccessful())")
        run([sys.executable, "-B", "-c", script, str(home), tmp], home)
    run([sys.executable, "-B", "-m", "unittest", "discover", "-v", *(["-s", "tests"] if home != path else [])], home)
    return f"基线 {len(originals)} 个测试文件及候选测试套件通过"


def advance(task, events, boot):
    last = events[-1]
    seq = last["seq"]
    data = {}
    for event in events:
        data.update(event["data"])

    def emit(state, **fields):
        nonlocal seq
        store.append(task, seq, state, fields)
        seq += 1

    progress_token = PROGRESS.set(lambda info: emit("working", progress=info))
    try:
        stage_context = {}
        if data.get("stage_id"):
            stage_context = stage_rules.context(task, store.journal(), store.stage_get())
        base = data.get("base") or boot
        for upstream in stage_context.get('upstream', []):
            git(ROOT, 'merge-base', '--is-ancestor', upstream['candidate'], base)
        emit("working", base=base)
        path = checkout(task, base)
        brief = json.dumps({"goal": data["goal"], "acceptance": data["acceptance"],
                            "decisions": [e["data"] for e in events if e["state"] == "answered"],
                            "checkpoint": data.get("checkpoint"),
                            "previous_error": data.get("error"),
                            "previous_run": data.get("progress"), **stage_context}, ensure_ascii=False)
        plan = data.get("plan")
        if not plan:
            result = model(path, "你是只读主 Agent。阅读仓库，针对一个独立需求给出 Worker 可执行的步骤和验收。"
                           '仅返回 JSON {"plan":"..."}。需求：' + brief, False)
            plan = result["plan"]
            if not isinstance(plan, str) or not plan.strip():
                raise ValueError("主 Agent 返回了空计划")
            emit("planned", plan=plan)
        result = model(path, "在当前工作树实现已授权的任务，补上直接验证验收条件的测试。保留已有修改。"
                       "不要切分支、提交、推送或修改其他工作树。需要产品判断时保留修改并退出。"
                       '最终仅返回 {"status":"done","summary":"..."} 或 '
                       '{"status":"question","question":"...","checkpoint":"...","recommendation":"..."}。'
                       + '需要裁定时 decision_json 是 JSON 字符串，包含 background、recommendation、options（2至4项，每项 id、label、impact、reply），说明推荐项和理由。完成时 decision_json 为空字符串。'
                       + brief + "\n计划：" + plan, True)
        if result["status"] == "question":
            emit("waiting", question=result["question"], checkpoint=result["checkpoint"],
                 recommendation=result["recommendation"], decision=decision_data(result))
            return
        if result["status"] != "done" or not result.get("summary"):
            raise ValueError("Worker 响应缺少完成说明")
        if git(path, "branch", "--show-current") != branch(task):
            raise RuntimeError("Worker 改变了工作分支")
        git(path, "add", "-A")
        if git(path, "diff", "--cached", "--name-only"):
            git(path, "-c", "user.name=Project Guide", "-c", "user.email=project-guide@localhost",
                "commit", "-m", "Implement seed task " + task)
        sha = git(path, "rev-parse", "HEAD")
        if sha == base:
            raise ValueError("Worker 没有生成代码改动")
        git(path, "merge-base", "--is-ancestor", base, sha)
        # A clean checkout ensures the reported result belongs to this exact SHA.
        with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
            candidate = Path(tmp) / "candidate"
            git(ROOT, "worktree", "add", "--detach", str(candidate), sha)
            try:
                evidence = verify(candidate, base)
            finally:
                git(ROOT, "worktree", "remove", "--force", str(candidate))
        emit("review", candidate=sha, summary=result["summary"], evidence=evidence,
             diff=git(path, "diff", "--stat", base, sha))
    except Exception as exc:
        checkpoint = {"worktree": str(RUNTIME / "trees" / task)}
        try:
            checkpoint["changes"] = git(Path(checkpoint["worktree"]), "status", "--short")[-2500:]
        except Exception:
            checkpoint["changes"] = "未能读取工作树状态"
        if isinstance(exc, seed_run.RunError):
            checkpoint["run"] = exc.record
        emit("failed", error=str(exc)[-3500:], checkpoint=json.dumps(checkpoint, ensure_ascii=False))
    finally:
        PROGRESS.reset(progress_token)


@contextmanager
def activation_lock(root=None):
    root = root or ROOT
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    with (common / "seed-activation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def activate(data):
    with activation_lock():
        activate_locked(data)


def activate_locked(data, root=None):
    root = root or ROOT
    if git(root, "branch", "--show-current") != "main" or git(root, "status", "--porcelain"):
        raise ValueError("请先确认 main 工作区干净")
    current = git(root, "rev-parse", "HEAD")
    if current == data["candidate"]:
        return  # Recover if Git succeeded immediately before a process crash.
    if current != data["base"]:
        raise ValueError("main 已前移，需要基于新版本重新派工并验证")
    git(root, "merge", "--ff-only", data["candidate"])


STAGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["tasks"],
    "properties": {"tasks": {"type": "array", "minItems": 2, "maxItems": 6,
        "items": {"type": "object", "additionalProperties": False,
            "required": ["id", "title", "goal", "acceptance", "blockedBy"],
            "properties": {**{key: {"type": "string"} for key in ("id", "title", "goal", "acceptance")},
                           "blockedBy": {"type": "array", "items": {"type": "string"}}}}}}}


def plan_stage(stage):
    """Only read an immutable source snapshot; never enqueue or call advance."""
    ident, version = stage['id'], stage['version']
    source = git(ROOT, 'rev-parse', 'HEAD')
    store.stage_update(ident, version, 'planning', {'source': source, 'error': ''}, ['pending'])
    version += 1
    progress = {}
    def report(info):
        progress.update(info)
        # Progress is bounded and does not change the draft version.
        store.sql(f"UPDATE seed_stage SET data=data || {store.payload({'progress': info})} "
                  f"WHERE id='{ident}' AND state='planning' AND version={version};")
    token = PROGRESS.set(report)
    result = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = subprocess.run(['git', 'archive', source], cwd=ROOT, capture_output=True, check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                # Repository symlinks are omitted so a snapshot cannot escape to the live tree.
                for member in bundle.getmembers():
                    target = Path(tmp) / member.name
                    if not target.resolve().is_relative_to(Path(tmp).resolve()):
                        raise ValueError('仓库快照含越界路径')
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(bundle.extractfile(member).read())
            result = model(Path(tmp), '你是只读主 Agent。阅读当前仓库快照，将阶段拆为 2 到 6 张任务草稿。'
                '仅规划，不执行或修改代码。每张 id 使用短标识，blockedBy 引用这些标识；related 不构成依赖。'
                '需求和验收必须具体、可独立验证。快照仅含该提交的普通文件，符号链接未展开。阶段：' + json.dumps(
                    {'goal': stage['goal'], 'acceptance': stage['acceptance'], 'source': source}, ensure_ascii=False),
                False, STAGE_SCHEMA)
        cards = result['tasks']
        if not isinstance(cards, list) or any(not isinstance(c, dict) for c in cards):
            raise ValueError('模型任务格式无效')
        labels = [c['id'] for c in cards]
        if any(not isinstance(x, str) or not x for x in labels) or len(set(labels)) != len(labels):
            raise ValueError('模型任务标识重复或为空')
        mapping = {label: str(uuid4()) for label in labels}
        converted = [{**c, 'id': mapping[c['id']],
                      'blockedBy': [mapping.get(dep, dep) for dep in c['blockedBy']]} for c in cards]
        stage_rules.validate(converted)
        store.stage_update(ident, version, 'draft', {'cards': converted, 'raw_plan': result,
                           'source': source, 'progress': progress}, ['planning'])
    except Exception as exc:
        store.stage_update(ident, version, 'failed', {'error': str(exc)[-3500:], 'raw_plan': result,
                           'progress': getattr(exc, 'record', progress)}, ['planning'])
    finally:
        PROGRESS.reset(token)
