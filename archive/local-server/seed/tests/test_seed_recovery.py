import unittest
import seed_recovery as recovery
class RecoveryTests(unittest.TestCase):
    def test_bounded_recovery_and_unsafe_exit_exclusion(self):
        rows=[{'seq':1,'state':'failed','data':{'error':'Ran 59 tests\nFAILED (errors=3)'}}]
        self.assertEqual(recovery.next_recovery({'t':rows},None)[2]['automatic_recovery'],1)
        for i in range(2):
            rows.append({'seq':len(rows)+1,'state':'answered','data':{'automatic_recovery':i+1}})
            rows.append({'seq':len(rows)+1,'state':'failed','data':{}})
        self.assertIsNone(recovery.next_recovery({'t':rows},None))
        rows=[{'seq':1,'state':'failed','data':{'error':'服务中断，旧 Worker 可能仍在执行'}}]
        self.assertIsNone(recovery.next_recovery({'t':rows},None))
    def test_active_or_owner_question_is_not_retried(self):
        for state in ('working','waiting','review','closed'):
            self.assertIsNone(recovery.next_recovery({'t':[{'seq':1,'state':state,'data':{'error':'Ran 1 tests FAILED (errors=1)'}}]},None))
