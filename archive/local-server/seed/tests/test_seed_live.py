"""Opt-in live read-only model evidence, with HTTP and an isolated PostgreSQL schema.

SEED_TEST_PG=1 SEED_TEST_LIVE=1 python3 -B -m unittest -v test_seed_live
Only planning calls a live model; confirmation queues tasks without executing workers.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import seed
import seed_engine as engine
import seed_store as store
import seed_stage as rules
from test_seed_stage import params_for


@unittest.skipUnless(os.environ.get('SEED_TEST_LIVE') == '1' and os.environ.get('SEED_TEST_PG') == '1',
                     'requires explicit live model and PostgreSQL opt-in')
class LiveStagePlanning(unittest.TestCase):
    def test_http_real_readonly_planner_then_edit_authorize(self):
        schema = 'seed_test_' + uuid4().hex
        store.sql(f'CREATE SCHEMA {schema}')
        runtime = engine.RUNTIME / 'live-stage' / uuid4().hex
        try:
            with TemporaryDirectory() as tmp, patch.dict(os.environ, PGOPTIONS=f'-c search_path={schema}'):
                root = Path(tmp)
                engine.git(root, 'init', '-b', 'main')
                (root/'calculator.py').write_text('def add(a, b):\n    return a + b\n')
                engine.git(root, 'add', '.')
                engine.git(root, '-c', 'user.name=Test', '-c', 'user.email=t@local', 'commit', '-m', 'fixture')
                source = engine.git(root, 'rev-parse', 'HEAD')
                store.init()
                with patch.object(engine, 'ROOT', root), patch.object(engine, 'RUNTIME', runtime), \
                     patch.object(engine, 'advance') as advance, patch.object(engine, 'checkout') as checkout:
                    server = seed.ThreadingHTTPServer(('127.0.0.1', 0), seed.Console)
                    server.boot = source
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        base = f'http://127.0.0.1:{server.server_port}'
                        def post(params):
                            params = {**params, 'token': [seed.TOKEN]}
                            request = Request(base+'/action', urlencode(params, doseq=True).encode())
                            with urlopen(request, timeout=10) as response:
                                return response.read().decode()
                        page = post({'action': ['stage_create'], 'goal': ['给 calculator 增加乘法和命令行入口'],
                                     'acceptance': ['乘法有单元测试；命令行输入两个整数打印结果，非法输入返回非零状态。']})
                        self.assertIn('pending', page)
                        self.assertTrue(seed.coordinate_once(source))
                        stage = store.stage_get()
                        self.assertEqual(stage['state'], 'draft', stage.get('error'))
                        rules.validate(stage['cards'])
                        self.assertEqual(stage['source'], source)
                        self.assertFalse(store.journal())
                        advance.assert_not_called()
                        checkout.assert_not_called()
                        with urlopen(base, timeout=10) as response:
                            self.assertIn('确认此版本并授权执行', response.read().decode())
                        params = params_for(stage)
                        params['goal_'+stage['cards'][0]['id']] = [stage['cards'][0]['goal']+'；保留现有 add 行为']
                        post(params)
                        stage = store.stage_get()
                        params = params_for(stage)
                        params['confirm'] = ['yes']
                        post(params)
                        authorized = store.stage_get()
                        self.assertEqual(authorized['state'], 'authorized')
                        self.assertEqual(len(store.journal()), len(stage['cards']))
                        advance.assert_not_called()
                        evidence = {'source': source, 'run': stage['progress'], 'schema': schema,
                                    'cards': len(stage['cards']), 'authorized_version': stage['version'],
                                    'worker_calls': 0, 'live_model': True}
                        runtime.mkdir(parents=True, exist_ok=True)
                        (runtime/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
                        print('\nLIVE_STAGE_EVIDENCE '+json.dumps(evidence, ensure_ascii=False), flush=True)
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)
        finally:
            store.sql(f'DROP SCHEMA {schema} CASCADE')
