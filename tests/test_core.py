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


TEST_JSON = {
              'type': 'test',
              'length': 10,
            }


END_JSON = {'cmd': 'close'}


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

    def raise_base_exception(self, args):
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

    def test_pack_infor_return_format(self):
        inst = self.make_agent(core.BaseAgent, None)
        infor = ('0011', [(1,), (2,)])
        packet = inst.pack_infor(*infor)
        ret_head = packet[:2]
        ret_dict = json.loads(packet[2:].decode())
        self.assertEqual(int.from_bytes(ret_head, 'big'), len(packet) - 2)
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
        inst.conf['monItems'][0]['execProg'] = 'raise_base_exception'
        inst.all_task_reg()

    def test_run_forever_with_interval_task(self):
        reg_hist = []
        test_ext = ExtTestMock(self.init_conf['monItems'][0], reg_hist)
        inst = self.make_agent(core.BaseAgent, test_ext)
        inst.conf['monItems'] = inst.conf['monItems'][:1]
        inst.conf['monItems'][0]['execArgs'] = inst.scher
        inst.conf['monItems'][0]['trigInter'] = interval = 0.5
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
        self.pack = json.dumps(TEST_JSON).encode()

    def make_server(self, server, test_pack):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(server)
        sock.listen(1)
        try:
            while True:
                conn, client = sock.accept()
                buf = conn.recv(2048)
                conn.close()
                if buf == b'{"cmd": "close"}':
                    break
                self.recv_count += 1
                self.assertEqual(buf, test_pack)
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, self.pack))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.1)

    def tearDown(self):
        pass

    def test_send_infor_send_only_once_when_server_invalid(self):
        self.mix_in.send_infor(self.pack)
        self.assertEqual(self.mix_in.logger.called, 1)

    def test_send_infor_reconnect_when_socket_error(self):
        self.mix_in.send_infor(self.pack)
        self.run_server()
        self.mix_in.send_infor(self.pack)
        self.mix_in.send_infor(json.dumps(END_JSON).encode())
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
        self.pack = json.dumps(TEST_JSON).encode()

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
                    buf = conn.recv(2048)
                    if buf == b'{"cmd": "close"}':
                        break
                    self.recv_count += 1
                    self.assertEqual(buf, test_pack)
            finally:
                conn.close()
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, self.pack))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.2)

    def test_send_infor_send_once_and_reconnect_when_connection_invalid(self):
        self.assertEqual(self.mix_in.logger.called, 1)
        self.mix_in.send_infor(self.pack)
        self.assertEqual(self.mix_in.logger.called, 3)

    def test_send_infor_reconnect_when_socket_error(self):
        self.mix_in.send_infor(self.pack)
        self.run_server()
        # fail and reconnect
        self.mix_in.send_infor(self.pack)
        self.mix_in.send_infor(self.pack)
        self.mix_in.send_infor(json.dumps(END_JSON).encode())
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
        self.pack = json.dumps(TEST_JSON).encode()

    def tearDown(self):
        self.mix_in.connection_close()

    def make_server(self, server, test_pack):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(server)
        try:
            while True:
                buf = sock.recv(2048)
                if buf == b'{"cmd": "close"}':
                    break
                self.recv_count += 1
                self.assertEqual(buf, test_pack)
        finally:
            sock.close()

    def run_server(self):
        self.server = threading.Thread(target=self.make_server,
                                       args=(self.server_address, self.pack))
        self.daemon = True
        self.server.start()
        # 保证server启动完成
        time.sleep(0.1)

    def test_send_infor_success_twice(self):
        self.run_server()
        self.mix_in.send_infor(self.pack)
        self.mix_in.send_infor(self.pack)
        self.mix_in.send_infor(json.dumps(END_JSON).encode())
        self.server.join()
        self.assertEqual(self.recv_count, 2)


if __name__ == '__main__':
    unittest.main()
