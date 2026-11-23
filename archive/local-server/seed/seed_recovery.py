"""Bounded recovery for completed verification failures, never orphaned processes."""
import seed_stage as rules

LIMIT = 2

def next_recovery(jobs, stage):
    for task, rows in jobs.items():
        data = rules.merged(rows)
        error = data.get('error', '')
        # Only completed test runs qualify. Unknown exits / service interruption
        # need a separate process-liveness check before they may be retried.
        if (rows[-1]['state'] != 'failed' or rules.blockers(task, jobs, stage)
                or not ('FAILED (' in error and 'Ran ' in error)):
            continue
        attempts = sum(bool(r['data'].get('automatic_recovery')) for r in rows)
        if attempts >= LIMIT:
            continue
        return task, rows[-1]['seq'], {
            'automatic_recovery': attempts + 1,
            'reply': f'自动故障恢复 {attempts + 1}/{LIMIT}：请根据上次测试失败日志诊断根因，修复保留的工作树并重新验证。不得跳过失败测试、删除约束或降低验收标准。测试必须使用隔离数据，不能改变正式项目数据库绑定。若需要扩大产品范围、权限或预算，提出结构化裁定卡后停下。',
        }
    return None
