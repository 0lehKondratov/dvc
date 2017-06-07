import base64
import hashlib
import os

from multiprocessing.pool import ThreadPool

from boto.s3.connection import S3Connection
from google.cloud import storage as gc

from dvc.command.base import CmdBase, DvcLock
from dvc.logger import Logger
from dvc.exceptions import DvcException
from dvc.runtime import Runtime
from dvc.system import System
from dvc.data_cloud import DataCloud

#FIXME add cmdline argument and config option for POOL_SIZE
POOL_SIZE = 4

class DataSyncError(DvcException):
    def __init__(self, msg):
        super(DataSyncError, self).__init__('Data sync error: {}'.format(msg))

class CmdDataSync(CmdBase):
    def __init__(self, settings):
        super(CmdDataSync, self).__init__(settings)

    def define_args(self, parser):
        parser.add_argument('targets',
                            metavar='',
                            help='File or directory to sync.',
                            nargs='*')

    def run(self):
        with DvcLock(self.is_locker, self.git):
            cloud = DataCloud(self.settings)
            pool = ThreadPool(processes=POOL_SIZE)
            targets = []

            for target in self.parsed_args.targets:
                if System.islink(target):
                    targets.append(target)
                elif os.path.isdir(target):
                    for root, dirs, files in os.walk(target):
                        for f in files:
                            targets.append(os.path.join(root, f))
                else:
                    raise DataSyncError('File "{}" does not exit'.format(target)) 

            pool.map(cloud.sync, targets)
        pass

if __name__ == '__main__':
    Runtime.run(CmdDataSync)
