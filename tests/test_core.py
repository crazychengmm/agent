#!/usr/bin/env python
#
# Author: zhangjoto
# E-Mail: zhangjoto@gmail.com
#
# Create Date: 2016-04-28
#

import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock

from agent import core


TEST_PACK = b'\x00\x1e{"type": "test", "length": 10}'


END_PACK = b'\x00\x10{"cmd": "close"}'


def convert_head(head):
    return int.from_bytes(head, 'big')


class NullLog:
    def __init__(self):
        self.called = 0

    def error(self, *args):
        self.called += 1

    def info(self, *args):
        self.called += 1

    def debug(self, *args):
        self.called += 1


class ExtTestMock:
    def __init__(self, task, container):
        gen = self.one_gen(container)
        setattr(self, task['execProg'], gen.send)
        gen.send(None)

    def one_gen(self, container):
        sche = yield None
        # 取出调度器内部任务队列供检查
        container.append(sche.queue)
        yield ('0011', [('test',)])
        container.append(sche.queue)
        # 只能以KeyboardInterrupt中止run_forever
        raise KeyboardInterrupt

    def raise_exception(self, args):
        raise Exception

    def raise_keyboard_interrupt(self, args):
        raise KeyboardInterrupt


class TestBaseAgent(unittest.TestCase):
    def setUp(self):
        fd, self.fname = tempfile.mkstemp(text=True)
        shutil.copyfile(os.path.join('example', 'agent.conf.json'), self.fname)
        with open(fd) as fp:
            self.init_conf = json.load(fp)

    def tearDown(self):
        if os.path.exists(self.fname):
            os.remove(self.fname)

    def make_agent(self, agtcls, ext_module):
        class MyAgent(agtcls):
            def send_infor(self, pack):
                return pack

        return MyAgent(ext_module, self.fname)

    def test_load_conf_success(self):
        inst = self.make_agent(core.BaseAgent, None)
        self.assertEqual(inst.conf.keys(), {'nodId', 'srvInfo', 'monItems'})
        self.assertEqual(inst.conf['srvInfo'].keys(),
                         {'linkType', 'srvPort', 'sndPacket', 'srvAddr'})
        for i in inst.conf['monItems']:
            self.assertEqual(i.keys(), {'execProg', 'monType', 'monTrigger',
                                        'execArgs', 'execPrio', 'trigInter'})
        self.assertEqual(self.init_conf, inst.conf)

    def test_load_conf_file_not_exist(self):
        os.remove(self.fname)
        with self.assertRaises(FileNotFoundError):
            self.make_agent(core.BaseAgent, None)

    def test_delayfunc_wait_time(self):
        inst = self.make_agent(core.BaseAgent, None)
        start = time.time()
        inst.delayfunc(0.2)
        self.assertLess(abs(time.time() - start - 0.2), 0.001)

    def test_received_cmd_is_update_config(self):
        pass

    def test_received_cmd_is_function_in_ext_module(self):
        inst = self.make_agent(core.BaseAgent, None)
        with unittest.mock.patch.object(inst.timer, 'wait',
                                        return_value=('0011', None)):
            inst.timer.response = unittest.mock.Mock()
            que1 = inst.scher.queue[:]
            inst.delayfunc(5)
            self.assertEqual(len(inst.scher.queue) - len(que1), 1)
            task = inst.scher.queue[-1]
            self.assertEqual(task.argument[0]['monType'], '0011')

    def test_received_cmd_is_invalid(self):
        inst = self.make_agent(core.BaseAgent, None)
        with unittest.mock.patch.object(inst.timer, 'wait',
                                        return_value=('invalid', None)):
            with unittest.mock.patch.object(inst.timer, 'response') as mock:
                inst.delayfunc(5)
                mock.assert_called_with(is_ok=False, detail='invalid cmd')

    def test_received_cmd_is_None(self):
        inst = self.make_agent(core.BaseAgent, None)
        inst.scher.enterabs = unittest.mock.Mock()
        with unittest.mock.patch.object(inst.timer, 'wait',
                                        return_value=(None, None)):
            inst.delayfunc(5)
            inst.scher.enterabs.assert_not_called()

    def test_pack_infor_return_format(self):
        inst = self.make_agent(core.BaseAgent, None)
        infor = ('0011', [(1,), (2,)])
        packet = inst.pack_infor(*infor)
        ret_head = packet[:2]
        ret_dict = json.loads(packet[2:].decode())
        self.assertEqual(convert_head(ret_head), len(packet) - 2)
        self.assertEqual(ret_dict['count'], len(infor[1]))
        self.assertEqual(ret_dict['nodId'], self.init_conf['nodId'])

    def test_all_task_reg_keyboard_interrupt_should_raise_out(self):
        ext = ExtTestMock(self.init_conf['monItems'][0], None)
        inst = self.make_agent(core.BaseAgent, ext)
        inst.conf['monItems'][0]['execProg'] = 'raise_keyboard_interrupt'
        with self.assertRaises(KeyboardInterrupt):
            inst.all_task_reg()

    def test_all_task_reg_other_exceptions_should_be_catched(self):
        ext = ExtTestMock(self.init_conf['monItems'][0], None)
        inst = self.make_agent(core.BaseAgent, ext)
        inst.conf['monItems'][0]['execProg'] = 'raise_exception'
        inst.all_task_reg()

    def test_run_forever_with_interval_task(self):
        reg_hist = []
        test_ext = ExtTestMock(self.init_conf['monItems'][0], reg_hist)
        inst = self.make_agent(core.BaseAgent, test_ext)
        inst.conf['monItems'] = inst.conf['monItems'][:1]
        inst.conf['monItems'][0]['execArgs'] = inst.scher
        inst.conf['monItems'][0]['trigInter'] = interval = 0.2
        try:
            inst.run_forever()
        except KeyboardInterrupt:
            pass
        times = [i[0].time for i in reg_hist]
        # 暂时允许调度时间误差1毫秒
        self.assertLess(abs(times[1] - times[0] - interval), 0.001)


class TestShortTCPMixIn(unittest.TestCase):
    def setUp(self):
        self.mix_in = core.ShortTCPMixIn()
        self.mix_in.conf = {}
        self.mix_in.conf['srvInfo'] = {'srvAddr': '127.0.0.1', 'srvPort': 8001}
        self.mix_in.logger = NullLog()
        self.server_address = ('127.0.0.1', 8001)
        self.recv_count = 0

    def make_server(self, server, test_pack):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(server)
        sock.listen(1)
        try:
            while True:
                conn, client = sock.accept()
                length = convert_head(conn.recv(2))
                buf = conn.recv(length)
                conn.close()
                if buf == b'{"cmd": "close"}':
                    break
                self.recv_count += 1
                self.assertEqual(buf, test_pack[2:])
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, TEST_PACK))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.1)

    def tearDown(self):
        pass

    def test_send_infor_send_only_once_when_server_invalid(self):
        self.mix_in.send_infor(TEST_PACK)
        self.assertEqual(self.mix_in.logger.called, 1)

    def test_send_infor_reconnect_when_socket_error(self):
        self.mix_in.send_infor(TEST_PACK)
        self.run_server()
        self.mix_in.send_infor(TEST_PACK)
        self.mix_in.send_infor(END_PACK)
        self.server.join()
        self.assertEqual(self.recv_count, 1)


class TestLongTCPMixIn(unittest.TestCase):
    def setUp(self):
        self.mix_in = core.LongTCPMixIn()
        self.mix_in.conf = {}
        self.mix_in.conf['srvInfo'] = {'srvAddr': '127.0.0.1', 'srvPort': 8001}
        self.mix_in.logger = NullLog()
        self.server_address = ('127.0.0.1', 8001)
        self.mix_in.connection_init()
        self.mix_in.connection_close()
        self.recv_count = 0

    def tearDown(self):
        self.mix_in.connection_close()

    def make_server(self, server, test_pack):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(server)
        sock.listen(1)
        try:
            conn, client = sock.accept()
            try:
                while True:
                    length = convert_head(conn.recv(2))
                    buf = conn.recv(length)
                    if buf == b'{"cmd": "close"}':
                        break
                    self.recv_count += 1
                    self.assertEqual(buf, test_pack[2:])
            finally:
                conn.close()
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, TEST_PACK))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.2)

    def test_send_infor_send_once_and_reconnect_when_connection_invalid(self):
        self.assertEqual(self.mix_in.logger.called, 1)
        self.mix_in.send_infor(TEST_PACK)
        self.assertEqual(self.mix_in.logger.called, 3)

    def test_send_infor_reconnect_when_socket_error(self):
        self.mix_in.send_infor(TEST_PACK)
        self.run_server()
        # fail and reconnect
        self.mix_in.send_infor(TEST_PACK)
        self.mix_in.send_infor(TEST_PACK)
        self.mix_in.send_infor(END_PACK)
        self.server.join()
        self.assertEqual(self.recv_count, 1)


class TestUDPMixIn(unittest.TestCase):
    def setUp(self):
        self.mix_in = core.UDPMixIn()
        self.mix_in.conf = {}
        self.mix_in.conf['srvInfo'] = {'srvAddr': '127.0.0.1', 'srvPort': 8001}
        self.mix_in.logger = NullLog()
        self.server_address = ('127.0.0.1', 8001)
        self.mix_in.connection_init()
        self.recv_count = 0

    def tearDown(self):
        self.mix_in.connection_close()

    def make_server(self, server, test_pack):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(server)
        try:
            while True:
                buf = sock.recv(1500)
                if buf == END_PACK:
                    break
                self.recv_count += 1
                self.assertEqual(buf, test_pack)
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, TEST_PACK))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.2)

    def test_send_infor_success_twice(self):
        self.run_server()
        self.mix_in.send_infor(TEST_PACK)
        self.mix_in.send_infor(TEST_PACK)
        self.mix_in.send_infor(END_PACK)
        self.server.join()
        self.assertEqual(self.recv_count, 2)

    def test_pack_longer_than_1400(self):
        self.run_server()
        with self.assertRaises(AssertionError):
            self.mix_in.send_infor(TEST_PACK * 100)
        self.mix_in.send_infor(END_PACK)
        self.server.join()


class TestNullLisenter(unittest.TestCase):
    def test_delayfunc_wait_time(self):
        pass
        # inst = self.make_agent(core.BaseAgent, None)
        # start = time.time()
        # inst.delayfunc(0.5)
        # self.assertLess(abs(time.time() - start - 0.5), 0.001)


if __name__ == '__main__':
    unittest.main()
