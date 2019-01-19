"""Launch `dvc daemon` command in a separate detached process."""

import os
import sys
from subprocess import Popen

import dvc.logger as logger
from dvc.utils import is_binary, fix_env


CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def _spawn_windows(cmd):
    from subprocess import STARTUPINFO, STARTF_USESHOWWINDOW

    creationflags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS

    startupinfo = STARTUPINFO()
    startupinfo.dwFlags |= STARTF_USESHOWWINDOW

    Popen(cmd,
          env=fix_env(),
          close_fds=True,
          shell=False,
          creationflags=creationflags,
          startupinfo=startupinfo).communicate()


def _spawn_posix(cmd):
    # NOTE: using os._exit instead of sys.exit, because dvc built
    # with PyInstaller has trouble with SystemExit exeption and throws
    # errors such as "[26338] Failed to execute script __main__"
    try:
        pid = os.fork()
        if pid > 0:
            return
    except OSError:
        logger.error("failed at first fork")
        os._exit(1)  # pylint: disable=protected-access

    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)  # pylint: disable=protected-access
    except OSError:
        logger.error("failed at second fork")
        os._exit(1)  # pylint: disable=protected-access

    sys.stdin.close()
    sys.stdout.close()
    sys.stderr.close()

    Popen(cmd, env=fix_env(), close_fds=True, shell=False).communicate()

    os._exit(0)  # pylint: disable=protected-access


def daemon(args):
    """Launch a `dvc daemon` command in a detached process.

    Args:
        args (list): list of arguments to append to `dvc daemon` command.
    """
    cmd = [sys.executable]
    if not is_binary():
        cmd += ['-m', 'dvc']
    cmd += ['daemon', '-q'] + args

    logger.debug("Trying to spawn '{}'".format(cmd))

    if os.name == 'nt':
        _spawn_windows(cmd)
    elif os.name == 'posix':
        _spawn_posix(cmd)
    else:
        raise NotImplementedError

    logger.debug("Spawned '{}'".format(cmd))
