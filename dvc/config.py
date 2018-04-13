"""
DVC config objects.
"""
import os
import schema
import configobj

from dvc.exceptions import DvcException


class ConfigError(DvcException):
    """ DVC config exception """
    def __init__(self, ex=None):
        super(ConfigError, self).__init__('Config file error', ex)


def supported_url(url):
    from dvc.cloud.data_cloud import DataCloud
    return DataCloud.supported(url) != None


class Config(object):
    CONFIG = 'config'
    CONFIG_LOCAL = 'config.local'

    SECTION_CORE = 'core'
    SECTION_CORE_LOGLEVEL = 'loglevel'
    SECTION_CORE_LOGLEVEL_SCHEMA = schema.And(schema.Use(str.lower), lambda l: l in ('info', 'debug', 'warning', 'error'))
    SECTION_CORE_REMOTE = 'remote'

    # backward compatibility
    SECTION_CORE_CLOUD = 'cloud'
    SECTION_CORE_CLOUD_SCHEMA = schema.And(schema.Use(str.lower), lambda c: c in ('aws', 'gcp', 'local', ''))
    SECTION_CORE_STORAGEPATH = 'storagepath'

    SECTION_CORE_SCHEMA = {
        schema.Optional(SECTION_CORE_LOGLEVEL, default='info'): schema.And(str, schema.Use(str.lower), SECTION_CORE_LOGLEVEL_SCHEMA),
        schema.Optional(SECTION_CORE_REMOTE, default=''): schema.And(str, schema.Use(str.lower)),

        # backward compatibility
        schema.Optional(SECTION_CORE_CLOUD, default=''): SECTION_CORE_CLOUD_SCHEMA,
        schema.Optional(SECTION_CORE_STORAGEPATH, default=''): str,
    }

    # backward compatibility
    SECTION_AWS = 'aws'
    SECTION_AWS_STORAGEPATH = 'storagepath'
    SECTION_AWS_CREDENTIALPATH = 'credentialpath'
    SECTION_AWS_REGION = 'region'
    SECTION_AWS_PROFILE = 'profile'
    SECTION_AWS_SCHEMA = {
        SECTION_AWS_STORAGEPATH: str,
        schema.Optional(SECTION_AWS_REGION): str,
        schema.Optional(SECTION_AWS_PROFILE, default='default'): str,
        schema.Optional(SECTION_AWS_CREDENTIALPATH, default = ''): str,
    }

    # backward compatibility
    SECTION_GCP = 'gcp'
    SECTION_GCP_STORAGEPATH = SECTION_AWS_STORAGEPATH
    SECTION_GCP_PROJECTNAME = 'projectname'
    SECTION_GCP_SCHEMA = {
        SECTION_GCP_STORAGEPATH: str,
        schema.Optional(SECTION_GCP_PROJECTNAME): str,
    }

    # backward compatibility
    SECTION_LOCAL = 'local'
    SECTION_LOCAL_STORAGEPATH = SECTION_AWS_STORAGEPATH
    SECTION_LOCAL_SCHEMA = {
        SECTION_LOCAL_STORAGEPATH: str,
    }


    SECTION_REMOTE_REGEX = r'^\s*remote\s*"(.*)"\s*$'
    SECTION_REMOTE_FMT = 'remote "{}"'
    SECTION_REMOTE_URL = 'url'
    SECTION_REMOTE_SCHEMA = {
        SECTION_REMOTE_URL: supported_url,
        schema.Optional(SECTION_AWS_REGION): str,
        schema.Optional(SECTION_AWS_PROFILE, default='default'): str,
        schema.Optional(SECTION_AWS_CREDENTIALPATH, default = ''): str,
        schema.Optional(SECTION_GCP_PROJECTNAME): str,
    }

    SCHEMA = {
        schema.Optional(SECTION_CORE, default={}): SECTION_CORE_SCHEMA,
        schema.Optional(schema.Regex(SECTION_REMOTE_REGEX)): SECTION_REMOTE_SCHEMA,

        # backward compatibility
        schema.Optional(SECTION_AWS, default={}): SECTION_AWS_SCHEMA,
        schema.Optional(SECTION_GCP, default={}): SECTION_GCP_SCHEMA,
        schema.Optional(SECTION_LOCAL, default={}): SECTION_LOCAL_SCHEMA,
    }

    def __init__(self, dvc_dir):
        self.dvc_dir = os.path.abspath(os.path.realpath(dvc_dir))
        self.config_file = os.path.join(dvc_dir, self.CONFIG)
        self.config_local_file = os.path.join(dvc_dir, self.CONFIG_LOCAL)

        try:
            self._config = configobj.ConfigObj(self.config_file)
            local = configobj.ConfigObj(self.config_local_file)

            # NOTE: schema doesn't support ConfigObj.Section validation, so we
            # need to convert our config to dict before passing it to schema.
            self._config = self._lower(self._config)
            local = self._lower(local)
            self._config.update(local)

            self._config = schema.Schema(self.SCHEMA).validate(self._config)

            # NOTE: now converting back to ConfigObj
            self._config = configobj.ConfigObj(self._config, write_empty_values=True)
            self._config.filename = self.config_file
        except Exception as ex:
            raise ConfigError(ex)

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
