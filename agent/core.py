#!/usr/bin/env python3
#
# Author: zhangjoto
# E-Mail: zhangjoto@gmail.com
#
# Create Date: 2016-04-16
#

"""通用代理插件基础类。

本模块尝试构造一个基本的代理插件，通过MixIn模式可以同时支持长短TCP连接、UDP报文
等向Server发送数据的方式；
同时能使用单线程支持接收并执行Server端发来的指令，还能将其他主动推送来的数据转
发到Server端。
支持多项任务可能会产生一定的延时，但应该在可控范围以内。
"""

import collections
import json
import logging
import os
import sched
import socket
import time

from . import util


class SimpleTimer:
    """简单的计时器类，集成到Agent类中作为定时器使用。

    本类的wait方法由Agent内的调度器调用，方法内部仅直接调用了time.sleep。
    """
    def wait(self, timeout):
        return (time.sleep(timeout), None)


class BaseAgent(object):
    """所有Agent类的基类。

    供调用者使用的方法：

    - __init__(ext_module, config_file, timer=SimpleTimer())
    - run_forever()

    可以被覆盖的方法：

    - load_conf(fname)
    - task_wrapper()
    - connection_init()
    - connection_close()
    - send_infor(pack)

    可以被覆盖的属性：

    - log
    - scher
    """
    def __init__(self, ext_module, config_file, timer=SimpleTimer()):
        """构造器，可以扩展。

        重要的参数及变量：

        - ext: 包含task代码的外部模块/包；
        - config_file: 包含task相关配置的文件，默认为./etc/agent.conf；
        - delayfunc: 调度器空闲时执行的函数，默认为time.sleep，可替换；
        - scher: 调度器，默认为sched.scheduler，可替换；
        """
        self.fname = config_file
        self.load_conf(self.fname)
        self.ext = ext_module
        self.connection_init()
        self.timer = timer
        self.scher = sched.scheduler(time.time, self.delayfunc)
        self.logger = logging.getLogger(__name__)

    def load_conf(self, fname):
        """读取配置文件，配置信息为OrderDict对象。"""
        with open(os.path.expandvars(fname)) as f:
            # 使用OrderedDict存取，是为了方便配置文件的管理、核对
            self.conf = json.load(f, object_pairs_hook=collections.OrderedDict)

    def one_task_reg(self, task):
        if task['monTrigger'] == 'interval':
            self.scher.enter(task['trigInter'], task['execPrio'],
                             self.one_task_reg, (task,))
        else:
            nexttime = util.attime(task['trigTime'])
            self.scher.enterabs(nexttime, task['execPrio'],
                                self.one_task_reg, (task,))
        return self.task_wrapper(task)

    def all_task_reg(self):
        """全部task注册到调度器。"""
        # 使用闭包包装监控函数，目的是捕捉除键盘中断以外的所有异常，避免监控函
        # 数代码质量导致agent退出
        # 捕捉到异常后的处理机制需要与监控Server端约定
        def task_catch_except(one_task):
            action = getattr(self.ext, one_task['execProg'])

            def func(*args):
                try:
                    return action(*args)
                except KeyboardInterrupt:
                    raise
                except Exception as err:
                    self.logger.error(err)
                    return {'error': str(err)}
            return func

        for task in self.conf['monItems']:
            task['execProg'] = task_catch_except(task)
            self.one_task_reg(task)

    def pack_infor(self, *infor):
        """为task返回的数据补充公共报文数据。"""
        dic = {}
        dic['type'], dic['detail'] = infor
        dic['count'] = len(dic['detail'])
        dic['ip'] = socket.gethostbyname(socket.gethostname())
        dic['nodId'] = self.conf['nodId']
        dic['timeStamp'] = util.timestamp()
        pack = json.dumps(dic).encode()
        header = len(pack).to_bytes(2, 'big')
        return header + pack

    def task_wrapper(self, task):
        """组合task执行及将数据发出的所有动作。"""
        self.send_infor(self.pack_infor(task['monType'],
                                        task['execProg'](task['execArgs'])))

    def delayfunc(self, timeout):
        ret_val, detail = self.timer.wait(timeout)
        if ret_val is None:
            return
        try:
            # 配置文件更新逻辑，如何实现还未确定
            if ret_val == 'update':
                pass
            else:
                mon_types = [i['monType'] for i in self.conf['monItems']]
                if ret_val in mon_types:
                    task = self.conf['monItems'][mon_types.index(ret_val)]
                    self.scher.enterabs(time.time(), task['execPrio'],
                                        self.taskwrapper, (task,))
                    self.timer.response(is_ok=True)
                else:
                    raise AssertionError('invalid cmd')
        except (AssertionError, OSError) as err:
            self.timer.response(is_ok=False, detail=str(err))

    def run_forever(self):
        try:
            self.all_task_reg()
            self.scher.run()
        except KeyboardInterrupt:
            self.logger.info('catch KeyboardInterrupt, agent close.')
        finally:
            self.connection_close()

    def send_infor(self, pack):
        """发送数据到Server。

        应由ShortTcpMixIn/LongTcpMixIn等MixIn类覆盖。
        """
        pass

    def connection_init(self):
        """初始化与Server端的连接。

        应由LongTcpMixIn等MixIn类覆盖。
        """
        pass

    def connection_close(self):
        """关闭与Server端的连接。

        应由LongTcpMixIn等MixIn类覆盖。
        """
        pass


class ShortTCPMixIn(object):
    """处理TCP短连接通信的MixIn类。"""
    def send_infor(self, pack):
        """发送数据到Server端。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        srvinfo = self.conf['srvInfo']
        try:
            sock.connect((srvinfo['srvAddr'], srvinfo['srvPort']))
            sock.send(pack)
            self.logger.debug('send pack success: %s', pack)
        except socket.error as err:
            self.logger.error(err)
        finally:
            sock.close()


class LongTCPMixIn(object):
    """处理TCP长连接通信的MixIn类。"""
    def connection_init(self):
        """建立TCP长连接。

        每次调用只尝试一次，以免日志量突增。
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        srvinfo = self.conf['srvInfo']
        try:
            self.sock.connect((srvinfo['srvAddr'], srvinfo['srvPort']))
        except socket.error as err:
            self.logger.error(err)

    def connection_close(self):
        self.sock.close()

    def send_infor(self, pack):
        """发送数据到Server端。

        发送失败时会且仅会尝试一次重新建链。
        """
        try:
            self.sock.send(pack)
            self.logger.debug('send pack success: %s', pack)
        except socket.error as err:
            self.logger.error(err)
            self.connection_close()
            self.connection_init()


class UDPMixIn(object):
    """处理UDP通信的MixIn类。
    
    由于数据包被分片会增大报文丢失的可能，UDP方式不允许传送长度超过1400的报文。
    """
    def connection_init(self):
        """创建UDP socket。"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.srvinfo = (self.conf['srvInfo']['srvAddr'],
                        self.conf['srvInfo']['srvPort'])

    def connection_close(self):
        self.sock.close()

    def send_infor(self, pack):
        """发送数据到Server端。"""
        try:
            assert len(pack) < 1400, 'UDP pack should not longer than MTU.'
            self.sock.sendto(pack, self.srvinfo)
            self.logger.debug('send pack success: %s', pack)
        except socket.error as err:
            self.logger.error(err)
            self.connection_close()
            self.connection_init()


class AgentShortTCP(ShortTCPMixIn, BaseAgent):
    pass


class AgentLongTCP(LongTCPMixIn, BaseAgent):
    pass


class AgentUDP(UDPMixIn, BaseAgent):
    pass


