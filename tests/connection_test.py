import unittest
import asyncio
import os

from aioredis.util import async_task
from ._testutil import BaseTest, run_until_complete, IS_REDIS_CLUSTER, SLOT_ZERO_KEY
from aioredis import (
    ConnectionClosedError,
    ProtocolError,
    RedisError,
    ReplyError,
    )


class ConnectionTest(BaseTest):

    @run_until_complete
    def test_connect_tcp(self):
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)
        self.assertEqual(conn.db, 0)
        self.assertEqual(str(conn), '<RedisConnection [db:0]>')

        conn = yield from self.create_connection(
            ['localhost', self.redis_port], loop=self.loop)
        self.assertEqual(conn.db, 0)
        self.assertEqual(str(conn), '<RedisConnection [db:0]>')

    @unittest.skipIf(not os.environ.get('REDIS_SOCKET'), "no redis socket")
    @run_until_complete
    def test_connect_unixsocket(self):
        conn = yield from self.create_connection(
            self.redis_socket, db=0, loop=self.loop)
        self.assertEqual(conn.db, 0)
        self.assertEqual(str(conn), '<RedisConnection [db:0]>')

    def test_global_loop(self):
        asyncio.set_event_loop(self.loop)

        conn = self.loop.run_until_complete(self.create_connection(
            ('localhost', self.redis_port), db=0))
        self.assertEqual(conn.db, 0)
        self.assertIs(conn._loop, self.loop)

    @unittest.skipIf(IS_REDIS_CLUSTER, 'SELECT not available on clusters')
    @run_until_complete
    def test_select_db(self):
        address = ('localhost', self.redis_port)
        conn = yield from self.create_connection(address, loop=self.loop)
        self.assertEqual(conn.db, 0)

        with self.assertRaises(ValueError):
            yield from self.create_connection(address, db=-1, loop=self.loop)
        with self.assertRaises(TypeError):
            yield from self.create_connection(address, db=1.0, loop=self.loop)
        with self.assertRaises(TypeError):
            yield from self.create_connection(
                address, db='bad value', loop=self.loop)
        with self.assertRaises(TypeError):
            conn = yield from self.create_connection(
                address, db=None, loop=self.loop)
            yield from conn.select(None)
        with self.assertRaises(ReplyError):
            yield from self.create_connection(
                address, db=100000, loop=self.loop)

        yield from conn.select(1)
        self.assertEqual(conn.db, 1)
        yield from conn.select(2)
        self.assertEqual(conn.db, 2)
        yield from conn.execute('select', 0)
        self.assertEqual(conn.db, 0)
        yield from conn.execute(b'select', 1)
        self.assertEqual(conn.db, 1)

    @run_until_complete
    def test_protocol_error(self):
        loop = self.loop
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=loop)

        reader = conn._reader

        with self.assertRaises(ProtocolError):
            reader.feed_data(b'not good redis protocol response')
            yield from conn.execute('PING')

        self.assertEqual(len(conn._waiters), 0)

    def test_close_connection__tcp(self):
        loop = self.loop
        conn = loop.run_until_complete(self.create_connection(
            ('localhost', self.redis_port), loop=loop))
        conn.close()
        with self.assertRaises(ConnectionClosedError):
            loop.run_until_complete(conn.execute('PING'))

        conn = loop.run_until_complete(self.create_connection(
            ('localhost', self.redis_port), loop=loop))
        with self.assertRaises(ConnectionClosedError):
            conn.close()
            fut = conn.execute('PING')
            loop.run_until_complete(fut)

        conn = loop.run_until_complete(self.create_connection(
            ('localhost', self.redis_port), loop=loop))
        conn.close()
        with self.assertRaises(ConnectionClosedError):
            conn.execute_pubsub('subscribe', 'channel:1')

    @unittest.skipIf(not os.environ.get('REDIS_SOCKET'), "no redis socket")
    @run_until_complete
    def test_close_connection__socket(self):
        conn = yield from self.create_connection(
            self.redis_socket, loop=self.loop)
        conn.close()
        with self.assertRaises(ConnectionClosedError):
            yield from conn.execute('PING')

        conn = yield from self.create_connection(
            self.redis_socket, loop=self.loop)
        conn.close()
        with self.assertRaises(ConnectionClosedError):
            yield from conn.execute_pubsub('subscribe', 'channel:1')

    @run_until_complete
    def test_closed_connection_with_none_reader(self):
        address = ('localhost', self.redis_port)
        conn = yield from self.create_connection(address, loop=self.loop)
        stored_reader = conn._reader
        conn._reader = None
        with self.assertRaises(ConnectionClosedError):
            yield from conn.execute('blpop', 'test', 0)
        conn._reader = stored_reader
        conn.close()

        conn = yield from self.create_connection(address, loop=self.loop)
        stored_reader = conn._reader
        conn._reader = None
        with self.assertRaises(ConnectionClosedError):
            yield from conn.execute_pubsub('subscribe', 'channel:1')
        conn._reader = stored_reader
        conn.close()

    @run_until_complete
    def test_wait_closed(self):
        address = ('localhost', self.redis_port)
        conn = yield from self.create_connection(address, loop=self.loop)
        reader_task = conn._reader_task
        conn.close()
        self.assertFalse(reader_task.done())
        yield from conn.wait_closed()
        self.assertTrue(reader_task.done())

    @run_until_complete
    def test_cancel_wait_closed(self):
        # Regression test: Don't throw error if wait_closed() is cancelled.
        address = ('localhost', self.redis_port)
        conn = yield from self.create_connection(address, loop=self.loop)
        reader_task = conn._reader_task
        conn.close()
        task = async_task(conn.wait_closed(), loop=self.loop)

        # Make sure the task is cancelled
        # after it has been started by the loop.
        self.loop.call_soon(task.cancel)

        yield from conn.wait_closed()
        self.assertTrue(reader_task.done())

    @run_until_complete
    def test_auth(self):
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)

        res = yield from conn.execute('CONFIG', 'SET', 'requirepass', 'pass')
        self.assertEqual(res, b'OK')

        conn2 = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)

        with self.assertRaises(ReplyError):
            yield from conn2.execute('PING')

        res = yield from conn2.auth('pass')
        self.assertEqual(res, True)
        res = yield from conn2.execute('PING')
        self.assertEqual(res, b'PONG')

        conn3 = yield from self.create_connection(
            ('localhost', self.redis_port), password='pass', loop=self.loop)

        res = yield from conn3.execute('PING')
        self.assertEqual(res, b'PONG')

        res = yield from conn2.execute('CONFIG', 'SET', 'requirepass', '')
        self.assertEqual(res, b'OK')

    @run_until_complete
    def test_decoding(self):
        key = SLOT_ZERO_KEY
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), encoding='utf-8', loop=self.loop)
        self.assertEqual(conn.encoding, 'utf-8',)
        res = yield from conn.execute('set', key, 'value')
        self.assertEqual(res, 'OK')
        res = yield from conn.execute('get', key)
        self.assertEqual(res, 'value')

        res = yield from conn.execute('set', key, b'bin-value')
        self.assertEqual(res, 'OK')
        res = yield from conn.execute('get', key)
        self.assertEqual(res, 'bin-value')

        res = yield from conn.execute('get', key, encoding='ascii')
        self.assertEqual(res, 'bin-value')
        res = yield from conn.execute('get', key, encoding=None)
        self.assertEqual(res, b'bin-value')

        with self.assertRaises(UnicodeDecodeError):
            yield from conn.execute('set', key, 'значение')
            yield from conn.execute('get', key, encoding='ascii')

        conn2 = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)
        res = yield from conn2.execute('get', key, encoding='utf-8')
        self.assertEqual(res, 'значение')

    @run_until_complete
    def test_execute_exceptions(self):
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)
        with self.assertRaises(TypeError):
            yield from conn.execute(None)
        with self.assertRaises(TypeError):
            yield from conn.execute("ECHO", None)
        with self.assertRaises(TypeError):
            yield from conn.execute("GET", ('a', 'b'))
        self.assertEqual(len(conn._waiters), 0)

    @run_until_complete
    def test_subscribe_unsubscribe(self):
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)

        self.assertEqual(conn.in_pubsub, 0)

        res = yield from conn.execute('subscribe', 'chan:1')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1]])

        self.assertEqual(conn.in_pubsub, 1)

        res = yield from conn.execute('unsubscribe', 'chan:1')
        self.assertEqual(res, [[b'unsubscribe', b'chan:1', 0]])
        self.assertEqual(conn.in_pubsub, 0)

        res = yield from conn.execute('subscribe', 'chan:1', 'chan:2')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1],
                               [b'subscribe', b'chan:2', 2],
                               ])
        self.assertEqual(conn.in_pubsub, 2)

        res = yield from conn.execute('unsubscribe', 'non-existent')
        self.assertEqual(res, [[b'unsubscribe', b'non-existent', 2]])
        self.assertEqual(conn.in_pubsub, 2)

        res = yield from conn.execute('unsubscribe', 'chan:1')
        self.assertEqual(res, [[b'unsubscribe', b'chan:1', 1]])
        self.assertEqual(conn.in_pubsub, 1)

    @run_until_complete
    def test_psubscribe_punsubscribe(self):
        conn = yield from self.create_connection(
            ('localhost', 6379), loop=self.loop)
        res = yield from conn.execute('psubscribe', 'chan:*')
        self.assertEqual(res, [[b'psubscribe', b'chan:*', 1]])
        self.assertEqual(conn.in_pubsub, 1)

    @run_until_complete
    def test_bad_command_in_pubsub(self):
        conn = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)

        res = yield from conn.execute('subscribe', 'chan:1')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1]])

        msg = "Connection in SUBSCRIBE mode"
        with self.assertRaisesRegex(RedisError, msg):
            yield from conn.execute('select', 1)
        with self.assertRaisesRegex(RedisError, msg):
            conn.execute('get')

    @run_until_complete
    def test_pubsub_messages(self):
        sub = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)
        pub = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)
        res = yield from sub.execute('subscribe', 'chan:1')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1]])

        self.assertIn(b'chan:1', sub.pubsub_channels)
        chan = sub.pubsub_channels[b'chan:1']
        self.assertEqual(
            str(chan), "<Channel name:b'chan:1', is_pattern:False, qsize:0>")
        self.assertEqual(chan.name, b'chan:1')
        self.assertTrue(chan.is_active)

        res = yield from pub.execute('publish', 'chan:1', 'Hello!')
        self.assertEqual(res, 1)
        msg = yield from chan.get()
        self.assertEqual(msg, b'Hello!')

        res = yield from sub.execute('psubscribe', 'chan:*')
        self.assertEqual(res, [[b'psubscribe', b'chan:*', 2]])
        self.assertIn(b'chan:*', sub.pubsub_patterns)
        chan2 = sub.pubsub_patterns[b'chan:*']
        self.assertEqual(chan2.name, b'chan:*')
        self.assertTrue(chan2.is_active)

        res = yield from pub.execute('publish', 'chan:1', 'Hello!')
        self.assertEqual(res, 2)

        msg = yield from chan.get()
        self.assertEqual(msg, b'Hello!')
        dest_chan, msg = yield from chan2.get()
        self.assertEqual(dest_chan, b'chan:1')
        self.assertEqual(msg, b'Hello!')

    @run_until_complete
    def test_multiple_subscribe_unsubscribe(self):
        sub = yield from self.create_connection(
            ('localhost', self.redis_port), loop=self.loop)

        res = yield from sub.execute('subscribe', 'chan:1')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1]])
        res = yield from sub.execute('subscribe', b'chan:1')
        self.assertEqual(res, [[b'subscribe', b'chan:1', 1]])

        res = yield from sub.execute('unsubscribe', 'chan:1')
        self.assertEqual(res, [[b'unsubscribe', b'chan:1', 0]])
        res = yield from sub.execute('unsubscribe', 'chan:1')
        self.assertEqual(res, [[b'unsubscribe', b'chan:1', 0]])

        res = yield from sub.execute('psubscribe', 'chan:*')
        self.assertEqual(res, [[b'psubscribe', b'chan:*', 1]])
        res = yield from sub.execute('psubscribe', 'chan:*')
        self.assertEqual(res, [[b'psubscribe', b'chan:*', 1]])

        res = yield from sub.execute('punsubscribe', 'chan:*')
        self.assertEqual(res, [[b'punsubscribe', b'chan:*', 0]])
        res = yield from sub.execute('punsubscribe', 'chan:*')
        self.assertEqual(res, [[b'punsubscribe', b'chan:*', 0]])
