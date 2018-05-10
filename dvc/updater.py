import os
import time
import json
import requests

from dvc import VERSION_BASE
from dvc.logger import Logger

class Updater(object):
    URL = 'https://4ki8820rsf.execute-api.us-east-2.amazonaws.com/prod/latest-version'
    UPDATER_FILE = 'updater'
    TIMEOUT = 7 * 24 * 60 * 60 #every week

    def __init__(self, dvc_dir):
        self.dvc_dir = dvc_dir
        self.updater_file = os.path.join(dvc_dir, self.UPDATER_FILE)

    @staticmethod
    def init(dvc_dir):
        return Updater(dvc_dir)

    def check(self):
        current = VERSION_BASE

        if os.getenv('CI'):
            return

        if os.path.isfile(self.updater_file):
            ctime = os.path.getctime(self.updater_file)
            if time.time() - ctime < self.TIMEOUT:
                msg = '{} is not old enough to check for updates'
                Logger.debug(msg.format(self.UPDATER_FILE))
                return

            os.unlink(self.updater_file)

        try:
            r = requests.get(self.URL)
            j = json.loads(r.content)
            latest = j['version']
            open(self.updater_file, 'w+').close()
        except Exception as exc:
            Logger.debug('Failed to obtain latest version: {}'.format(str(exc)))
            return

        l_major, l_minor, l_patch = latest.split('.')
        c_major, c_minor, c_patch = current.split('.')

        if l_major <= c_major and \
           l_minor <= c_minor and \
           l_patch <= c_patch:
               return

        msg = 'You are using dvc version {}, however version {} is available. Consider upgrading.'
        Logger.warn(msg.format(current, latest))
