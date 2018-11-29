from mock import patch

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

import colorama
import logging

from dvc.command.base import CmdBase
from dvc.config import Config
from dvc.logger import Logger
from dvc.main import main

from tests.basic_env import TestDvc


class StringContaining(str):
    def __eq__(self, other):
        return self in other


class TestLogger(TestDvc):
    def test_config(self):
        Logger('debug')
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.DEBUG)

        Logger('error')
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.ERROR)

    def test_set_level(self):
        Logger.set_level('debug')
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.DEBUG)

    def test_be_quiet(self):
        Logger.be_quiet()
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.CRITICAL)

    def test_be_verbose(self):
        Logger.be_verbose()
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.DEBUG)

    def test_colorize(self):
        for name, color in Logger.COLOR_MAP.items():
            msg = Logger.colorize(name, name)
            # This is not a tty, so it should not colorize anything
            self.assertEqual(msg, name)

    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_stderr(self, mock_stdout, mock_stderr):
        error_message = 'error msg'
        Logger.init()
        Logger.error(error_message)
        self.assertEqual('', mock_stdout.getvalue())
        self.assertEqual('Error: {}\n\nHaving any troubles? '
                                   'Hit us up at dvc.org/support, we '
                                   'are always happy to help!\n'.format(error_message),
                                   mock_stderr.getvalue())

    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_stdout(self, mock_stdout, mock_stderr):
        non_error_message = 'non-error message'
        Logger.init()
        Logger.info(non_error_message)
        self.assertEqual('', mock_stderr.getvalue())
        self.assertEqual('{}\n'.format(non_error_message),
                                   mock_stdout.getvalue())


class TestLoggerQuiet(TestDvc):
    def setUp(self):
        super(TestLoggerQuiet, self).setUp()
        class A(object):
            quiet = True
            verbose = False
        CmdBase._set_loglevel(A())

    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_progress(self, mock_stdout, mock_stderr):
        from dvc.progress import progress
        progress.update_target('target', 50, 100)
        self.assertEqual('', mock_stdout.getvalue())
        self.assertEqual('', mock_stderr.getvalue())

    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_box(self, mock_stdout, mock_stderr):
        Logger.box('message')
        self.assertEqual('', mock_stdout.getvalue())
        self.assertEqual('', mock_stderr.getvalue())


class TestLoggerCLI(TestDvc):
    def test(self):
        class A(object):
            quiet = True
            verbose = False

        args = A()
        CmdBase._set_loglevel(args)
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.CRITICAL)

        args.quiet = False
        args.verbose = True
        CmdBase._set_loglevel(args)
        self.assertEqual(Logger.logger().getEffectiveLevel(), logging.DEBUG)
