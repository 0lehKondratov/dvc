import re

from dvc.exceptions import DvcException


class DependencyError(DvcException):
    def __init__(self, path, msg):
        msg = 'Dependency \'{}\' error: {}'.format(path, msg)
        super(DependencyError, self).__init__(msg)


class DependencyDoesNotExistError(DependencyError):
    def __init__(self, path):
        msg = 'does not exist'
        super(DependencyDoesNotExistError, self).__init__(path, msg)


class DependencyIsNotFileOrDirError(DependencyError):
    def __init__(self, path):
        msg = 'not a file or directory'
        super(DependencyIsNotFileOrDirError, self).__init__(path, msg)


class DependencyBase(object):
    REGEX = None

    PARAM_PATH = 'path'

    def __init__(self, stage, path):
        self.stage = stage
        self.project = stage.project
        self.path = path

    def __str__(self):
        return self.path

    @classmethod
    def match(cls, url):
        return re.match(cls.REGEX, url)

    def group(self, name):
        match = self.match(self.path)
        if not match:
            return None
        return match.group(name)

    @classmethod
    def supported(cls, url):
        return cls.match(url) is not None

    @property
    def sep(self):
        return '/'

    @property
    def exists(self):
        return self.remote.exists([self.path_info])[0]

    def changed(self):
        raise NotImplementedError

    def status(self):
        if self.changed():
            # FIXME better msgs
            if self.path_info['scheme'] == 'local':
                p = self.rel_path
            else:
                p = self.path
            return {p: 'changed'}
        return {}

    def save(self):
        raise NotImplementedError

    def dumpd(self):
        return {self.PARAM_PATH: self.path}

    def download(self, to_info):
        self.remote.download([self.path_info], [to_info])
