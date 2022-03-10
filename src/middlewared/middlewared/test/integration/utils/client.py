# -*- coding=utf-8 -*-
import contextlib
import os

from middlewared.client import Client

__all__ = ["client"]


@contextlib.contextmanager
def client():
    if "NODE_A_IP" in os.environ:
        host = os.environ["NODE_A_IP"]
        password = os.environ["APIPASS"]
    else:
        host = os.environ["MIDDLEWARE_TEST_IP"]
        password = os.environ["MIDDLEWARE_TEST_PASSWORD"]

    with Client(f"ws://{host}/websocket", py_exceptions=True) as c:
        c.call("auth.login", "root", password)
        yield c
