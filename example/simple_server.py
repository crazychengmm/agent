#!/usr/bin/env python3
#
# Author: zhangjoto
# E-Mail: zhangjoto@gmail.com
#
# Create Date: 2016-07-19
#

import socketserver


class AgentRequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        pack = self.request.recv(65536)
        print('receive {} from {}'.format(pack, self.client_address))


if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(('127.0.0.1', 8001), AgentRequestHandler)
    srv.serve_forever()
