"""测试环境的准备步骤：什么时候要执行、执行（带超时、整组停掉）、执行前要你在应用里同意（按命令和文件的指纹记住），以及服务启动命令的同意。

测试环境的存储在 envs.py。分层见 tests/test_layers.py。
"""
import copy
import datetime as dt
import hashlib
import subprocess
import time

from ..core import procs
from ..core.common import main_checkout
from .values import env_for_run, redact, withheld_note
from .store import find_env, local_state, PREP_TIMEOUT, read_local, TAIL
from ..core.trust import require_app, scrubbed_environ
from pathlib import Path

__all__ = ['run_prep', 'prep_view', 'env_approve', 'env_ai_secrets', 'env_prep_run']


def cmd_print(step):
    """The fingerprint the user's consent is kept under: the command text and the directory it runs in."""
    return hashlib.sha256(f'{step.get("dir") or ""}\n{step.get("command") or ""}'.encode('utf-8')).hexdigest()[:24]


def files_print(place, watch):
    """A fingerprint of the files a step watches (globs relative to the code root); missing files count too."""
    h = hashlib.sha256()
    root = Path(place)
    for pattern in watch or []:
        h.update(f'#{pattern}\n'.encode())
        found = sorted(p for p in root.glob(pattern) if p.is_file())[:500]
        for f in found:
            h.update(str(f.relative_to(root)).encode('utf-8', 'replace'))
            try:  # 分块读：被监视的文件可能很大（锁文件、打包产物），不一次读进内存；算出来的和以前一样，以前的同意照样算数
                one = hashlib.sha256()
                with open(f, 'rb') as handle:
                    for block in iter(lambda: handle.read(1 << 20), b''):
                        one.update(block)
                h.update(one.digest())
            except OSError:
                h.update(b'?')
    return h.hexdigest()[:24]


def _needed(step, place, rec):
    if step.get('when') != 'changed':
        return True, '每次运行前都执行'
    prev = ((rec or {}).get('places') or {}).get(str(place))
    files = '、'.join(step.get('watch') or [])
    if not prev:
        return True, '在这个代码目录里还没成功执行过'
    if prev.get('cmd') != cmd_print(step):
        return True, '命令改过了'
    if prev.get('files') != files_print(place, step.get('watch')):
        return True, f'{files} 变了'
    return False, f'{files} 没变，不用再执行'


def _step_words(step, n):
    return f'准备步骤 {n}「{step.get("note") or step["command"]}」'


def _step_view(home, env, step, n, loc, place):
    rec = ((loc.get('prep_runs') or {}).get(env['id']) or {}).get(step['id']) or {}
    due, why = _needed(step, place, rec)
    where = Path(place) / step['dir'] if step.get('dir') else Path(place)
    return dict(step, n=n, approved=cmd_print(step) in (loc.get('approved') or {}), due=due, due_why=why, abs_dir=str(where), last=copy.deepcopy(rec.get('last')))


def prep_view(home, env, loc=None, place=None):
    loc = loc if loc is not None else read_local(home)
    place = Path(place or home)
    return [_step_view(home, env, s, i, loc, place) for i, s in enumerate(env.get('prep') or [], 1)]


def _exec(step, place, values, secrets):
    """Run one step in its directory with the environment's variables. (ok, code, tail, ms)."""
    cwd = Path(place) / step['dir'] if step.get('dir') else Path(place)
    if not cwd.is_dir():
        return False, None, f'执行目录 {cwd} 不存在。', 0
    started = time.perf_counter()
    try:  # 超时、rf.py 被叫停时连同整棵树一起停（procs.run）：步骤里自己开了新会话的进程也收得到
        code, out, _ = procs.run(step['command'], shell=True, cwd=str(cwd), env=scrubbed_environ(values),
                                 stderr=subprocess.STDOUT, timeout=step.get('timeout') or PREP_TIMEOUT)
    except OSError as error:
        return False, None, f'没能启动：{error}', 0
    timed = code is None
    text = (out or b'').decode('utf-8', errors='replace').rstrip()
    tail = redact(text[-TAIL:], secrets)
    if timed:
        tail = (tail + '\n' if tail else '') + f'（超过 {step.get("timeout") or PREP_TIMEOUT} 秒没有执行完，停掉了）'
    ms = round((time.perf_counter() - started) * 1000)
    return (code == 0 and not timed), (None if timed else code), tail, ms


def run_prep(home, envinfo, place, only=None, force=False, dry=False):
    """Before a test run (or by hand): run the environment's preparation steps that are due, in order, in `place` (the code root).
    A step runs only once the user has agreed to its command (kept on this machine by the command's fingerprint); the first one
    not agreed to, or the first that fails, stops the rest and gives `error`. With `dry`, nothing runs: `blocked` lists every
    due step not agreed to yet (the app asks before running). Results and the tail of the output are kept on this machine only.
    {ran: [...], skipped: [...], blocked: [...], error: str|None}"""
    home = main_checkout(home)
    env = envinfo['env']
    steps = env.get('prep') or []
    want = None
    if only:
        want = {str(x).upper() for x in only}
        missing = want - {s['id'] for s in steps}
        if missing:
            raise ValueError(f'{env["name"]} 没有准备步骤 {"、".join(sorted(missing))}。')
    place = Path(place).resolve()
    loc = read_local(home)
    out = {'env': {'id': env['id'], 'name': env['name']}, 'place': str(place), 'ran': [], 'skipped': [], 'blocked': [], 'error': None, 'withheld': envinfo.get('withheld') or []}
    for n, step in enumerate(steps, 1):
        if want is not None and step['id'] not in want:
            continue
        rec = (loc['prep_runs'].get(env['id']) or {}).get(step['id']) or {}
        due, why = (True, '手动执行') if force else _needed(step, place, rec)
        if not due:
            out['skipped'].append({'id': step['id'], 'n': n, 'note': step['note'], 'why': why})
            continue
        if cmd_print(step) not in loc['approved']:
            out['blocked'].append(_step_view(home, env, step, n, loc, place))
            if dry:
                continue
            out['error'] = (f'{_step_words(step, n)}还没经你同意，没有执行：第一次执行某条命令、或命令改了以后，要你在应用里看过命令和执行目录、点同意。'
                            f'\n命令：{step["command"]}\n执行目录：{Path(place) / step["dir"] if step.get("dir") else place}')
            break
        if dry:
            continue
        ok, code, tail, ms = _exec(step, place, envinfo.get('vars'), envinfo.get('secrets'))
        at = dt.datetime.now().isoformat(timespec='seconds')
        with local_state(home) as l2:
            r = l2['prep_runs'].setdefault(env['id'], {}).setdefault(step['id'], {})
            r['last'] = {'at': at, 'place': str(place), 'ok': ok, 'code': code, 'ms': ms, 'tail': tail, 'command': step['command'], 'dir': step.get('dir') or '',
                         'withheld': envinfo.get('withheld') or []}
            places = r.setdefault('places', {})
            if ok:
                places[str(place)] = {'cmd': cmd_print(step), 'files': files_print(place, step.get('watch')), 'at': at}
            else:
                places.pop(str(place), None)
        out['ran'].append({'id': step['id'], 'n': n, 'note': step['note'], 'ok': ok, 'code': code, 'ms': ms, 'tail': tail})
        if not ok:
            out['error'] = (f'{_step_words(step, n)}失败了（' + (f'退出码 {code}' if code is not None else '没有正常结束') + f'）。\n命令：{step["command"]}\n'
                            f'执行目录：{Path(place) / step["dir"] if step.get("dir") else place}\n输出的最后部分：\n{tail or "（没有输出）"}')
            if envinfo.get('withheld'):
                out['error'] += '\n' + withheld_note(envinfo['withheld'])
            break
    return out


def env_prep_run(root, ref=None, only=(), force=True, place=None, trusted=None):
    """rf.py env prep-run: run the steps by hand (all, or the ones named), whether due or not. Only steps the user agreed to run."""
    home = main_checkout(root)
    envinfo = env_for_run(home, ref, trusted=trusted)
    if not envinfo:
        raise ValueError('现在没有在用的测试环境；要说执行哪一套的准备步骤。')
    if not envinfo['env'].get('prep'):
        raise ValueError(f'{envinfo["name"]} 还没有准备步骤。')
    return run_prep(home, envinfo, Path(place or home), only or None, force=force)


def service_print(service):
    """The fingerprint the user's consent to a start command is kept under: the command and the directory it runs in.
    Kept apart from the preparation steps' (a step and a service with the same command are agreed to separately)."""
    return 'svc:' + hashlib.sha256(f'service\n{service.get("dir") or ""}\n{service.get("command") or ""}'.encode('utf-8')).hexdigest()[:24]


def env_approve(root, ref, ids=(), services=()):
    """The user agrees to run these steps' commands (all of the environment's when neither steps nor services are named), and
    these services' start commands (人工验收). Only from the app."""
    require_app('同意执行准备步骤和启动命令')
    home = main_checkout(root)
    env = find_env(home, ref)
    steps = env.get('prep') or []
    want = {str(x).upper() for x in ids} if ids else (set() if services else {s['id'] for s in steps})
    hit = [s for s in steps if s['id'] in want]
    svcs = [s for s in env.get('services') or [] if s['name'] in set(services)]
    if services and len(svcs) != len(set(services)):
        raise ValueError(f'{env["name"]} 没有服务 {"、".join(sorted(set(services) - {s["name"] for s in svcs}))}。')
    if not hit and not svcs:
        raise ValueError(f'{env["name"]} 没有这几条准备步骤：{"、".join(sorted(want)) or "（没有准备步骤）"}。')
    if [s for s in svcs if not s.get('command')]:
        raise ValueError('没有启动命令的服务不用同意。')
    at = dt.datetime.now().isoformat(timespec='seconds')
    with local_state(home) as loc:
        for s in hit:
            loc['approved'][cmd_print(s)] = {'command': s['command'], 'dir': s.get('dir') or '', 'at': at, 'env': env['id'], 'step': s['id']}
        for s in svcs:
            loc['approved'][service_print(s)] = {'command': s['command'], 'dir': s.get('dir') or '', 'at': at, 'env': env['id'], 'service': s['name']}
    return {'id': env['id'], 'name': env['name'], 'approved': [s['id'] for s in hit], 'services': [s['name'] for s in svcs], 'prep': prep_view(home, env)}


def env_ai_secrets(root, ref, on):
    """The user's choice for this environment on this machine: may AI sessions' test runs and preparation steps get the secret values."""
    require_app('允许 AI 的会话带上保密值')
    home = main_checkout(root)
    env = find_env(home, ref)
    with local_state(home) as loc:
        if on:
            loc['ai_secrets'][env['id']] = True
        else:
            loc['ai_secrets'].pop(env['id'], None)
    return {'id': env['id'], 'name': env['name'], 'ai_secrets': bool(on)}
