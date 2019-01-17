"""Manages dvc lock file."""

import os
import time
import zc.lockfile

from dvc.exceptions import DvcException


class LockError(DvcException):
    """Thrown when unable to acquire the lock for dvc project."""


class Lock(object):
    """Class for dvc project lock.

    Args:
        dvc_dir (str): path to the directory that the lock should be created
            in.
        name (str): name of the lock file.
    """
    LOCK_FILE = 'lock'
    TIMEOUT = 5

    def __init__(self, dvc_dir, name=LOCK_FILE):
        self.lock_file = os.path.join(dvc_dir, name)
        self._lock = None

    def _do_lock(self):
        try:
            self._lock = zc.lockfile.LockFile(self.lock_file)
        except zc.lockfile.LockError:
            raise LockError('cannot perform the cmd since DVC is busy and '
                            'locked. Please retry the cmd later.')

    def lock(self):
        """Acquire lock for dvc project."""
        try:
            self._do_lock()
            return
        except LockError:
            time.sleep(self.TIMEOUT)

        self._do_lock()

    def unlock(self):
        """Release lock for dvc project."""
        self._lock.close()
        self._lock = None

    def __enter__(self):
        self.lock()

    def __exit__(self, typ, value, tbck):
        self.unlock()
