from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import seed_engine as engine


class SeedTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = patch.multiple(engine, ROOT=self.root, RUNTIME=self.root / ".seed")
        patches.start()
        self.addCleanup(patches.stop)
        engine.git(self.root, "init", "-b", "main")
        (self.root / ".gitignore").write_text(".seed/\n__pycache__/\n")
        (self.root / "feature.py").write_text("value = 1\n")
        (self.root / "test_old.py").write_text(
            "import unittest, feature\nclass Old(unittest.TestCase):\n"
            "    def test_positive(self): self.assertGreater(feature.value, 0)\n")
        engine.git(self.root, "add", ".")
        engine.git(self.root, "-c", "user.name=Test", "-c", "user.email=test@local",
                   "commit", "-m", "base")
        self.base = engine.git(self.root, "rev-parse", "HEAD")
        self.task = str(uuid4())
        self.events = [{"seq": 1, "state": "queued", "data": {"goal": "increase", "acceptance": "2"}}]

    def record(self, task, previous, state, data):
        self.assertEqual(task, self.task)
        self.assertEqual(previous, self.events[-1]["seq"])
        self.events.append(dict(seq=previous + 1, state=state, data=data))

    def advance(self, worker):
        def model(path, prompt, writable):
            if not writable:
                return {"plan": "edit and test"}
            return worker(path, prompt)
        with patch.object(engine, "model", side_effect=model), patch.object(engine.store, "append", self.record):
            engine.advance(self.task, list(self.events), self.base)

    def test_checkpoint_then_verified_activation(self):
        def stop(path, prompt):
            (path / "feature.py").write_text("value = 2\n")
            return dict(status="question", question="keep 2?", recommendation="yes", checkpoint="edited")
        self.advance(stop)
        self.assertEqual(self.events[-1]["state"], "waiting")
        self.record(self.task, self.events[-1]["seq"], "answered", {"reply": "keep 2"})
        def finish(path, prompt):
            self.assertIn("keep 2", prompt)
            self.assertEqual((path / "feature.py").read_text(), "value = 2\n")
            return dict(status="done", summary="edited")
        self.advance(finish)
        self.assertEqual(self.events[-1]["state"], "review", self.events[-1])
        data = dict(base=self.base, **self.events[-1]["data"])
        self.assertEqual(engine.git(self.root, "rev-parse", "HEAD"), self.base)
        engine.activate(data)
        engine.activate(data)
        self.assertEqual(engine.git(self.root, "rev-parse", "HEAD"), data["candidate"])

    def test_deleting_baseline_test_cannot_hide_regression(self):
        def regress(path, prompt):
            (path / "feature.py").write_text("value = -1\n")
            (path / "test_old.py").unlink()
            return dict(status="done", summary="claimed")
        self.advance(regress)
        self.assertEqual(self.events[-1]["state"], "failed")
        self.assertIn("AssertionError", self.events[-1]["data"]["error"])
        self.assertEqual(engine.git(self.root, "rev-parse", "HEAD"), self.base)

    def test_retry_receives_actual_interruption_and_worktree(self):
        def interrupted(path, prompt):
            (path / "feature.py").write_text("value = 2\n")
            raise RuntimeError("execution interrupted")
        self.advance(interrupted)
        last = self.events[-1]
        self.assertEqual(last["state"], "failed")
        self.assertIn("feature.py", last["data"]["checkpoint"])
        self.record(self.task, last["seq"], "answered", {"reply": "continue"})
        def resume(path, prompt):
            self.assertIn("execution interrupted", prompt)
            self.assertIn(str(path), prompt)
            self.assertEqual((path / "feature.py").read_text(), "value = 2\n")
            return dict(status="done", summary="resumed")
        self.advance(resume)
        self.assertEqual(self.events[-1]["state"], "review", self.events[-1])
