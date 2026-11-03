# -*- coding: utf-8 -*-
"""刷新失败可见化：连续失败计数与「上次成功」时长文案。

刷新持续失败时悬浮窗会一直显示旧值，用户可能误以为数据是新的。
本模块提供可单测的纯逻辑（不含 Qt 依赖），由 qt/main_widget 在
_apply_refresh 中调用：
- FailStreak：失败轮次计数，连续第 N 轮失败提醒一次（成功即清零，
  同一个失败会话内不重复提醒）
- stale_suffix：「上次成功刷新于 N 分钟前」文案，供失败轮拼接进
  错误行展示，成功后随 errors 清空自然消失
"""

FAIL_REMIND_STREAK = 3   # 连续失败达到该轮数时提醒一次


class FailStreak:
    """连续刷新失败计数器。

    record(ok) 每轮刷新后调用一次：成功清零并返回 False；
    失败递增，仅在计数恰好达到 remind_after 时返回 True（提醒一次），
    之后的持续失败不再返回 True，直到下一次成功重置。"""

    def __init__(self, remind_after=FAIL_REMIND_STREAK):
        self.remind_after = max(1, int(remind_after))
        self.count = 0

    def record(self, ok):
        if ok:
            self.count = 0
            return False
        self.count += 1
        return self.count == self.remind_after


def stale_suffix(last_ok_time, now):
    """距上次成功刷新的时长文案；从未成功过或刚成功（不足 1 分钟）
    返回空串（不展示，避免失败轮与上一轮成功贴得太近时误导）。

    分钟数向下取整但至少为 1：超过 60 秒即显示「1 分钟前」。"""
    if last_ok_time is None:
        return ""
    secs = (now - last_ok_time).total_seconds()
    if secs < 60:
        return ""
    return "（上次成功刷新于 %d 分钟前）" % int(secs // 60)
