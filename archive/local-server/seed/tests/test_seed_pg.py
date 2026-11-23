"""Run with SEED_TEST_PG=1 using an existing, dedicated local database."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import seed_store as store


@unittest.skipUnless(os.environ.get("SEED_TEST_PG") == "1", "requires local PostgreSQL")
class PostgresJournalTests(unittest.TestCase):
    def test_round_trip_and_stale_write(self):
        schema = "seed_test_" + uuid4().hex
        store.sql(f"CREATE SCHEMA {schema}")
        try:
            with patch.dict(os.environ, PGOPTIONS=f"-c search_path={schema}"):
                store.init()
                goal = "验证换行\n以及 ' 和反斜线 \\"
                task = store.create(goal, "保留原文")
                store.append(task, 1, "working", {"base": "example"})
                with self.assertRaises(ValueError):
                    store.append(task, 1, "closed", {})
                events = store.journal()[task]
                self.assertEqual(events[0]["data"]["goal"], goal)
                self.assertEqual([event["state"] for event in events], ["queued", "working"])
        finally:
            store.sql(f"DROP SCHEMA {schema} CASCADE")


@unittest.skipUnless(os.environ.get('SEED_TEST_PG') == '1', 'requires local PostgreSQL')
class PostgresStageTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'seed_test_' + uuid4().hex
        store.sql(f'CREATE SCHEMA {self.schema}')
        self.env = patch.dict(os.environ, PGOPTIONS=f'-c search_path={self.schema}')
        self.env.start()
        store.init()
        self.addCleanup(self.cleanup_schema)

    def cleanup_schema(self):
        self.env.stop()
        store.sql(f'DROP SCHEMA {self.schema} CASCADE')

    def draft(self):
        from test_seed_stage import cards
        ident = store.stage_create("阶段 ' 文本", '阶段验收')
        store.stage_update(ident, 0, 'planning', {'source': 'a'*40}, ['pending'])
        self.assertFalse(store.journal())
        store.stage_update(ident, 1, 'draft', {'cards': cards(), 'raw_plan': {'sample': True}}, ['planning'])
        return store.stage_get()

    def assert_batch(self, stage, authorized):
        jobs = store.journal()
        self.assertEqual(len(jobs), len(stage['cards']) if authorized else 0)
        count = int(store.sql('SELECT count(*) FROM seed_stage_authorizations'))
        self.assertEqual(count, int(authorized))
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_stage_tasks')), len(jobs))
        return jobs

    def race(self, *operations):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier = threading.Barrier(len(operations))
        def call(operation):
            barrier.wait(timeout=10)
            try:
                return True, operation()
            except (ValueError, RuntimeError) as exc:
                return False, str(exc)
        # Every sql() launches a separate psql connection. No application LOCK is used.
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(call, operations))

    def test_edit_authorize_repeat_restart_and_stage_assessment(self):
        stage = self.draft()
        self.assert_batch(stage, False)
        cards = stage['cards']
        cards[0]['goal'] = 'edited requirement'
        store.stage_save(stage['id'], 2, cards)
        stage = store.stage_get()
        self.assertEqual(stage['version'], 3)
        with self.assertRaises((RuntimeError, ValueError)):
            store.stage_authorize(stage['id'], 2)
        tasks = store.stage_authorize(stage['id'], 3)
        self.assertEqual(tasks, store.stage_authorize(stage['id'], 3))
        store.init()
        restored = store.stage_get()
        self.assertEqual(restored['tasks'], tasks)
        self.assertEqual(restored['cards'][0]['goal'], 'edited requirement')
        self.assertEqual(restored['source'], 'a'*40)
        jobs = self.assert_batch(stage, True)
        self.assertTrue(all(rows[0]['data']['draft_version'] == 3 for rows in jobs.values()))
        with self.assertRaisesRegex(RuntimeError, '人工启用'):
            store.stage_accept(stage['id'], 3, 'too early')
        for index, task in enumerate(tasks):
            store.append(task, 1, 'review', {'candidate': str(index)*40})
        with self.assertRaises(RuntimeError):
            store.stage_accept(stage['id'], 3, 'tests passed only')
        for index, task in enumerate(tasks):
            store.append(task, 2, 'activated', {'candidate': str(index)*40})
        self.assertEqual(store.stage_get()['state'], 'authorized')
        with self.assertRaises(RuntimeError):
            store.stage_accept(stage['id'], 2, 'stale')
        store.stage_accept(stage['id'], 3, 'needs manual follow-up', False)
        self.assertEqual(store.stage_get()['state'], 'authorized')
        store.stage_accept(stage['id'], 3, 'needs manual follow-up', False)
        self.assertEqual(store.stage_get()['version'], 4)
        with self.assertRaisesRegex(RuntimeError, '阶段版本已更新'):
            store.stage_accept(stage['id'], 3, 'stale form cannot approve')
        result = self.race(*[lambda: store.stage_accept(stage['id'], 4, 'accepted stage') for _ in range(2)])
        self.assertTrue(all(ok for ok, _ in result), result)
        store.stage_accept(stage['id'], 4, 'accepted stage')
        self.assertEqual(store.stage_authorize(stage['id'], 3), tasks)
        self.assertEqual(store.stage_get()['assessment'], 'accepted stage')
        assessments = store.sql("SELECT data FROM seed_stage_events WHERE kind='assessment'").splitlines()
        self.assertEqual(len(assessments), 2)
        import json
        self.assertEqual(set(json.loads(assessments[-1])['commits']), set(tasks))

    def test_concurrent_confirm_has_one_authorization_and_batch(self):
        stage = self.draft()
        result = self.race(*[lambda: store.stage_authorize(stage['id'], 2) for _ in range(4)])
        self.assertTrue(all(ok for ok, _ in result), result)
        self.assertTrue(all(value == result[0][1] for _, value in result), result)
        self.assert_batch(stage, True)
        self.assertEqual(int(store.sql("SELECT count(*) FROM seed_stage_events WHERE kind='authorized'")), 1)

    def test_save_confirm_race_and_stale_save(self):
        stage = self.draft()
        changed = stage['cards']
        changed[0]['acceptance'] = 'new acceptance'
        result = self.race(lambda: store.stage_authorize(stage['id'], 2),
                           lambda: store.stage_save(stage['id'], 2, changed))
        self.assertEqual(sum(ok for ok, _ in result), 1, result)
        current = store.stage_get()
        self.assert_batch(stage, current['state'] == 'authorized')
        if current['state'] == 'draft':
            self.assertEqual(current['version'], 3)
            self.assertEqual(current['cards'][0]['acceptance'], 'new acceptance')
        with self.assertRaises((RuntimeError, ValueError)):
            store.stage_save(stage['id'], 2, changed)

    def test_mid_batch_constraint_failure_rolls_back_everything(self):
        stage = self.draft()
        second = stage['cards'][1]['id']
        store.sql(f"ALTER TABLE seed_stage_tasks ADD CHECK(task != '{second}'::uuid)")
        with self.assertRaises(RuntimeError):
            store.stage_authorize(stage['id'], 2)
        self.assert_batch(stage, False)
        self.assertEqual(store.stage_get()['state'], 'draft')
        self.assertEqual(int(store.sql("SELECT count(*) FROM seed_stage_events WHERE kind='authorized'")), 0)

    def test_invalid_persisted_graph_and_closed_stage_do_not_pass(self):
        stage = self.draft()
        bad = stage['cards']
        bad[0]['blockedBy'] = [bad[1]['id']]
        store.stage_update(stage['id'], 2, 'draft', {'cards': bad}, ['draft'])
        with self.assertRaisesRegex(ValueError, '循环依赖'):
            store.stage_authorize(stage['id'], 3)
        self.assert_batch(stage, False)
        bad[0]['blockedBy'] = []
        store.stage_save(stage['id'], 3, bad)
        tasks = store.stage_authorize(stage['id'], 4)
        store.append(tasks[0], 1, 'closed', {})
        store.append(tasks[1], 1, 'activated', {'candidate': 'b'*40})
        with self.assertRaises(RuntimeError):
            store.stage_accept(stage['id'], 4, 'closed is not accepted')

    def test_interrupted_planning_requires_explicit_retry(self):
        ident = store.stage_create('goal', 'acceptance')
        store.stage_update(ident, 0, 'planning', {'source': 'c'*40}, ['pending'])
        store.stage_recover()
        current = store.stage_get()
        self.assertEqual(current['state'], 'failed')
        self.assertIn('中断', current['error'])
        self.assertFalse(store.journal())
        store.stage_recover()
        self.assertEqual(store.stage_get()['version'], current['version'])
        store.stage_update(ident, current['version'], 'pending', {}, ['failed'])
        self.assertEqual(store.stage_get()['state'], 'pending')
