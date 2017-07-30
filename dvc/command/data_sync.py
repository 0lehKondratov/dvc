import os
from multiprocessing.pool import ThreadPool

from dvc.command.base import CmdBase, DvcLock
from dvc.exceptions import DvcException
from dvc.system import System
from dvc.data_cloud import DataCloud
from dvc.utils import map_progress


class DataSyncError(DvcException):
    def __init__(self, msg):
        super(DataSyncError, self).__init__('Data sync error: {}'.format(msg))


class CmdDataSync(CmdBase):
    def __init__(self, settings):
        super(CmdDataSync, self).__init__(settings)

    def run(self):
        with DvcLock(self.is_locker, self.git):
            cloud = DataCloud(self.settings)
            targets = []

            if len(self.parsed_args.targets) == 0:
                raise DataSyncError('Sync target is not specified')

            for target in self.parsed_args.targets:
                if System.islink(target):
                    targets.append(target)
                elif os.path.isdir(target):
                    for root, dirs, files in os.walk(target):
                        for f in files:
                            targets.append(os.path.join(root, f))
                else:
                    raise DataSyncError('File "{}" does not exit'.format(target)) 

            map_progress(cloud.sync, targets, self.parsed_args.jobs)
        pass
