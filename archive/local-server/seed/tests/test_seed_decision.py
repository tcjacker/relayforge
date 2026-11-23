import unittest
import seed_decision as d
class DecisionTests(unittest.TestCase):
    def test_choices_are_validated_and_other_requires_text(self):
        data={'question':'缺少浏览器权限','recommendation':'补充验收步骤'}
        self.assertIn('补充',d.answer(data,'clarify'))
        self.assertEqual(d.answer(data,'other',' 我的意见 '),'我的意见')
        for choice,other in [('forged',''),('other','')]:
            with self.assertRaises(ValueError): d.answer(data,choice,other)
    def test_saved_option_reply_is_used(self):
        data={'decision':{'background':'背景','options':[{'id':str(i),'label':'选项','impact':'影响','reply':'已存指令'+str(i)} for i in range(2)]}}
        self.assertEqual(d.answer(data,'1'),'已存指令1')
