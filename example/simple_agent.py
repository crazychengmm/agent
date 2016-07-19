#!/usr/bin/env python
#
# Author: zhangjoto
# E-Mail: zhangjoto@gmail.com
#
# Create Date: 2016-07-19
#

import logging
import os
import sys


import agent.core


class InfoCollector:

    def onecheck(self, arg):
        files = list(os.listdir('.'))
        logging.info(files)
        return list(files)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG
    )
    config_fname = os.path.join(os.path.dirname(__file__), 'agent.conf.json')
    prog = agent.core.AgentShortTCP(InfoCollector(), config_fname)
    prog.run_forever()
