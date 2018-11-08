"""
DVC config objects.
"""
import os
import configobj
from schema import Schema, Optional, And, Use, Regex

from dvc.exceptions import DvcException


class ConfigError(DvcException):
    """ DVC config exception """
    def __init__(self, msg, ex=None):
        super(ConfigError, self).__init__('Config file error: {}'.format(msg),
                                          ex)


def supported_url(url):
    from dvc.remote import supported_url as supported
    return supported(url)


def supported_cache_type(types):
    if isinstance(types, str):
        types = [t.strip() for t in types.split(',')]
    for t in types:
        if t not in ['reflink', 'hardlink', 'symlink', 'copy']:
            return False
    return True


def supported_loglevel(level):
    return level in ['info', 'debug', 'warning', 'error']


def supported_cloud(cloud):
    return cloud in ['aws', 'gcp', 'local', '']


def is_bool(val):
    return val.lower() in ['true', 'false']


def to_bool(val):
    return val.lower() == 'true'


def is_whole(val):
    return int(val) >= 0


def is_percent(val):
    return int(val) >= 0 and int(val) <= 100


class Config(object):
    CONFIG = 'config'
    CONFIG_LOCAL = 'config.local'

    SECTION_CORE = 'core'
    SECTION_CORE_LOGLEVEL = 'loglevel'
    SECTION_CORE_LOGLEVEL_SCHEMA = And(Use(str.lower), supported_loglevel)
    SECTION_CORE_REMOTE = 'remote'
    SECTION_CORE_INTERACTIVE_SCHEMA = And(str, is_bool, Use(to_bool))
    SECTION_CORE_INTERACTIVE = 'interactive'

    SECTION_CACHE = 'cache'
    SECTION_CACHE_DIR = 'dir'
    SECTION_CACHE_TYPE = 'type'
    SECTION_CACHE_TYPE_SCHEMA = supported_cache_type
    SECTION_CACHE_PROTECTED = 'protected'
    SECTION_CACHE_LOCAL = 'local'
    SECTION_CACHE_S3 = 's3'
    SECTION_CACHE_GS = 'gs'
    SECTION_CACHE_SSH = 'ssh'
    SECTION_CACHE_HDFS = 'hdfs'
    SECTION_CACHE_AZURE = 'azure'
    SECTION_CACHE_SCHEMA = {
        Optional(SECTION_CACHE_LOCAL): str,
        Optional(SECTION_CACHE_S3): str,
        Optional(SECTION_CACHE_GS): str,
        Optional(SECTION_CACHE_HDFS): str,
        Optional(SECTION_CACHE_SSH): str,
        Optional(SECTION_CACHE_AZURE): str,

        Optional(SECTION_CACHE_DIR, default='cache'): str,
        Optional(SECTION_CACHE_TYPE, default=None): SECTION_CACHE_TYPE_SCHEMA,
        Optional(SECTION_CACHE_PROTECTED,
                 default=False): And(str, is_bool, Use(to_bool)),
    }

    # backward compatibility
    SECTION_CORE_CLOUD = 'cloud'
    SECTION_CORE_CLOUD_SCHEMA = And(Use(str.lower), supported_cloud)
    SECTION_CORE_STORAGEPATH = 'storagepath'

    SECTION_CORE_SCHEMA = {
        Optional(SECTION_CORE_LOGLEVEL,
                 default='info'): And(str, Use(str.lower),
                                      SECTION_CORE_LOGLEVEL_SCHEMA),
        Optional(SECTION_CORE_REMOTE, default=''): And(str, Use(str.lower)),
        Optional(SECTION_CORE_INTERACTIVE,
                 default=False): SECTION_CORE_INTERACTIVE_SCHEMA,

        # backward compatibility
        Optional(SECTION_CORE_CLOUD, default=''): SECTION_CORE_CLOUD_SCHEMA,
        Optional(SECTION_CORE_STORAGEPATH, default=''): str,
    }

    # backward compatibility
    SECTION_AWS = 'aws'
    SECTION_AWS_STORAGEPATH = 'storagepath'
    SECTION_AWS_CREDENTIALPATH = 'credentialpath'
    SECTION_AWS_ENDPOINT_URL = 'endpointurl'
    SECTION_AWS_REGION = 'region'
    SECTION_AWS_PROFILE = 'profile'
    SECTION_AWS_SCHEMA = {
        SECTION_AWS_STORAGEPATH: str,
        Optional(SECTION_AWS_REGION): str,
        Optional(SECTION_AWS_PROFILE): str,
        Optional(SECTION_AWS_CREDENTIALPATH): str,
        Optional(SECTION_AWS_ENDPOINT_URL): str,
    }

    # backward compatibility
    SECTION_GCP = 'gcp'
    SECTION_GCP_STORAGEPATH = SECTION_AWS_STORAGEPATH
    SECTION_GCP_PROJECTNAME = 'projectname'
    SECTION_GCP_SCHEMA = {
        SECTION_GCP_STORAGEPATH: str,
        Optional(SECTION_GCP_PROJECTNAME): str,
    }

    # backward compatibility
    SECTION_LOCAL = 'local'
    SECTION_LOCAL_STORAGEPATH = SECTION_AWS_STORAGEPATH
    SECTION_LOCAL_SCHEMA = {
        SECTION_LOCAL_STORAGEPATH: str,
    }

    SECTION_REMOTE_REGEX = r'^\s*remote\s*"(?P<name>.*)"\s*$'
    SECTION_REMOTE_FMT = 'remote "{}"'
    SECTION_REMOTE_URL = 'url'
    SECTION_REMOTE_USER = 'user'
    SECTION_REMOTE_PORT = 'port'
    SECTION_REMOTE_KEY_FILE = 'keyfile'
    SECTION_REMOTE_TIMEOUT = 'timeout'
    SECTION_REMOTE_PASSWORD = 'password'
    SECTION_REMOTE_ASK_PASSWORD = 'ask_password'
    SECTION_REMOTE_SCHEMA = {
        SECTION_REMOTE_URL: And(supported_url, error="Unsupported URL"),
        Optional(SECTION_AWS_REGION): str,
        Optional(SECTION_AWS_PROFILE): str,
        Optional(SECTION_AWS_CREDENTIALPATH): str,
        Optional(SECTION_AWS_ENDPOINT_URL): str,
        Optional(SECTION_GCP_PROJECTNAME): str,
        Optional(SECTION_CACHE_TYPE): SECTION_CACHE_TYPE_SCHEMA,
        Optional(SECTION_CACHE_PROTECTED,
                 default=False): And(str, is_bool, Use(to_bool)),
        Optional(SECTION_REMOTE_USER): str,
        Optional(SECTION_REMOTE_PORT): Use(int),
        Optional(SECTION_REMOTE_KEY_FILE): str,
        Optional(SECTION_REMOTE_TIMEOUT): Use(int),
        Optional(SECTION_REMOTE_PASSWORD): str,
        Optional(SECTION_REMOTE_ASK_PASSWORD): And(str, is_bool, Use(to_bool)),
    }

    SECTION_STATE = 'state'
    SECTION_STATE_ROW_LIMIT = 'row_limit'
    SECTION_STATE_ROW_CLEANUP_QUOTA = 'row_cleanup_quota'
    SECTION_STATE_SCHEMA = {
        Optional(SECTION_STATE_ROW_LIMIT): And(Use(int), is_whole),
        Optional(SECTION_STATE_ROW_CLEANUP_QUOTA): And(Use(int), is_percent),
    }

    SCHEMA = {
        Optional(SECTION_CORE, default={}): SECTION_CORE_SCHEMA,
        Optional(Regex(SECTION_REMOTE_REGEX)): SECTION_REMOTE_SCHEMA,
        Optional(SECTION_CACHE, default={}): SECTION_CACHE_SCHEMA,
        Optional(SECTION_STATE, default={}): SECTION_STATE_SCHEMA,

        # backward compatibility
        Optional(SECTION_AWS, default={}): SECTION_AWS_SCHEMA,
        Optional(SECTION_GCP, default={}): SECTION_GCP_SCHEMA,
        Optional(SECTION_LOCAL, default={}): SECTION_LOCAL_SCHEMA,
    }

    def __init__(self, dvc_dir):
        self.dvc_dir = os.path.abspath(os.path.realpath(dvc_dir))
        self.config_file = os.path.join(dvc_dir, self.CONFIG)
        self.config_local_file = os.path.join(dvc_dir, self.CONFIG_LOCAL)

        try:
            self._config = configobj.ConfigObj(self.config_file)
            local = configobj.ConfigObj(self.config_local_file)

            # NOTE: schema doesn't support ConfigObj.Section validation, so we
            # need to convert our config to dict before passing it to
            self._config = self._lower(self._config)
            local = self._lower(local)
            self._config = self._merge(self._config, local)

            self._config = Schema(self.SCHEMA).validate(self._config)

            # NOTE: now converting back to ConfigObj
            self._config = configobj.ConfigObj(self._config,
                                               write_empty_values=True)
            self._config.filename = self.config_file
        except Exception as ex:
            raise ConfigError(ex)

    @staticmethod
    def _merge(first, second):
        res = {}
        sections = list(first.keys()) + list(second.keys())
        for section in sections:
            f = first.get(section, {}).copy()
            s = second.get(section, {}).copy()
            f.update(s)
            res[section] = f
        return res

    @staticmethod
    def _lower(config):
        new_config = {}
        for s_key, s_value in config.items():
            new_s = {}
            for key, value in s_value.items():
                new_s[key.lower()] = value
            new_config[s_key.lower()] = new_s
        return new_config

    @staticmethod
    def init(dvc_dir):
        config_file = os.path.join(dvc_dir, Config.CONFIG)
        open(config_file, 'w+').close()
        return Config(dvc_dir)
