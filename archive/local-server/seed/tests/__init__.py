import os, sys

# 测试之间直接 import 彼此（如 import test_seed），所以把本目录放进搜索路径。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
