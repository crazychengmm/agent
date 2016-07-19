#!/usr/bin/env python3
#
# Author: zhangjoto
# E-Mail: zhangjoto@gmail.com
#
# Create Date: 2016-04-16
#

import datetime


def attime(timetuple):
    """根据传入的时刻值(HHMMSS)计算下一符合条件的时间。"""
    hour, minute, sec = timetuple
    curr = datetime.datetime.now()
    at_time = curr.replace(hour=hour, minute=minute, second=sec)
    if curr > at_time:
        at_time += datetime.timedelta(1)
    return at_time.timestamp()


def timestamp():
    return datetime.datetime.now().strftime('%Y%m%d%H%M%S')
