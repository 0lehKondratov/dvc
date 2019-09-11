"""Manages dvc lock file."""

from __future__ import unicode_literals

import os
import hashlib
import time
from datetime import timedelta

from funcy.py3 import lkeep

from dvc.exceptions import DvcException
from dvc.utils import makedirs
from dvc.utils.compat import is_py3


DEFAULT_TIMEOUT = 5


class LockError(DvcException):
    """Thrown when unable to acquire the lock for dvc repo."""


if is_py3:
    import flufl.lock

    class Lock(flufl.lock.Lock):
        """Class for dvc repo lock.

        Args:
            lockfile (str): the lock filename
                in.
            tmp_dir (str): a directory to store claim files.
        """

        def __init__(self, lockfile, tmp_dir=None):
            lifetime = timedelta(days=365)  # Lock for good by default
            self._tmp_dir = tmp_dir
            if self._tmp_dir is not None:
                makedirs(self._tmp_dir, exist_ok=True)

            super(Lock, self).__init__(lockfile, lifetime=lifetime)

        @property
        def lockfile(self):
            return self._lockfile

        @property
        def files(self):
            return lkeep([self._lockfile, self._tmp_dir])

        def lock(self):
            try:
                super(Lock, self).lock(timedelta(seconds=DEFAULT_TIMEOUT))
            except flufl.lock.TimeOutError:
                raise LockError(
                    "cannot perform the cmd since DVC is busy and "
                    "locked. Please retry the cmd later."
                )

        def _set_claimfile(self, pid=None):
            super(Lock, self)._set_claimfile(pid)

            if self._tmp_dir is not None:
                # Under Windows file path length is limited so we hash it
                filename = hashlib.md5(self._claimfile.encode()).hexdigest()
                self._claimfile = os.path.join(
                    self._tmp_dir, filename + ".lock"
                )


else:
    import zc.lockfile

    class Lock(object):
        """Class for dvc repo lock.

        Uses zc.lockfile as backend.
        """

        def __init__(self, lockfile, tmp_dir=None):
            self.lockfile = lockfile
            self._lock = None

        @property
        def files(self):
            return [self.lockfile]

        def _do_lock(self):
            try:
                self._lock = zc.lockfile.LockFile(self.lockfile)
            except zc.lockfile.LockError:
                raise LockError(
                    "cannot perform the cmd since DVC is busy and "
                    "locked. Please retry the cmd later."
                )

        def lock(self):
            try:
                self._do_lock()
                return
            except LockError:
                time.sleep(DEFAULT_TIMEOUT)

            self._do_lock()

        def unlock(self):
            self._lock.close()
            self._lock = None

        def __enter__(self):
            self.lock()

        def __exit__(self, typ, value, tbck):
            self.unlock()
