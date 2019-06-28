from __future__ import unicode_literals

import os
import getpass
import logging
import itertools
import errno
from concurrent.futures import ThreadPoolExecutor

try:
    import paramiko
except ImportError:
    paramiko = None

import dvc.prompt as prompt
from dvc.remote.ssh.connection import SSHConnection
from dvc.config import Config
from dvc.utils import to_chunks
from dvc.utils.compat import urlparse, StringIO
from dvc.remote.base import RemoteBASE
from dvc.scheme import Schemes


logger = logging.getLogger(__name__)


class RemoteSSH(RemoteBASE):
    scheme = Schemes.SSH
    REQUIRES = {"paramiko": paramiko}

    JOBS = 4
    PARAM_CHECKSUM = "md5"
    DEFAULT_PORT = 22
    TIMEOUT = 1800

    def __init__(self, repo, config):
        super(RemoteSSH, self).__init__(repo, config)

        url = config.get(Config.SECTION_REMOTE_URL)
        if url:
            parsed = urlparse(url)
            user_ssh_config = self._load_user_ssh_config(parsed.hostname)

            host = user_ssh_config.get("hostname", parsed.hostname)
            user = (
                config.get(Config.SECTION_REMOTE_USER)
                or parsed.username
                or user_ssh_config.get("user")
                or getpass.getuser()
            )
            port = (
                config.get(Config.SECTION_REMOTE_PORT)
                or parsed.port
                or self._try_get_ssh_config_port(user_ssh_config)
                or self.DEFAULT_PORT
            )
            self.path_info = self.path_cls.from_parts(
                scheme=self.scheme,
                host=host,
                user=user,
                port=port,
                path=parsed.path,
            )
        else:
            self.path_info = None
            user_ssh_config = {}

        self.keyfile = config.get(
            Config.SECTION_REMOTE_KEY_FILE
        ) or self._try_get_ssh_config_keyfile(user_ssh_config)
        self.timeout = config.get(Config.SECTION_REMOTE_TIMEOUT, self.TIMEOUT)
        self.password = config.get(Config.SECTION_REMOTE_PASSWORD, None)
        self.ask_password = config.get(
            Config.SECTION_REMOTE_ASK_PASSWORD, False
        )

    @staticmethod
    def ssh_config_filename():
        return os.path.expanduser(os.path.join("~", ".ssh", "config"))

    @staticmethod
    def _load_user_ssh_config(hostname):
        user_config_file = RemoteSSH.ssh_config_filename()
        user_ssh_config = dict()
        if hostname and os.path.exists(user_config_file):
            ssh_config = paramiko.SSHConfig()
            with open(user_config_file) as f:
                # For whatever reason parsing directly from f is unreliable
                f_copy = StringIO(f.read())
                ssh_config.parse(f_copy)
            user_ssh_config = ssh_config.lookup(hostname)
        return user_ssh_config

    @staticmethod
    def _try_get_ssh_config_port(user_ssh_config):
        try:
            return int(user_ssh_config.get("port"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _try_get_ssh_config_keyfile(user_ssh_config):
        identity_file = user_ssh_config.get("identityfile")
        if identity_file and len(identity_file) > 0:
            return identity_file[0]
        return None

    def ssh(self, path_info):
        host, user, port = path_info.host, path_info.user, path_info.port

        logger.debug(
            "Establishing ssh connection with '{host}' "
            "through port '{port}' as user '{user}'".format(
                host=host, user=user, port=port
            )
        )

        if self.ask_password and not self.password:
            self.password = prompt.password(
                "Enter a private key passphrase or a password for "
                "host '{host}' port '{port}' user '{user}'".format(
                    host=host, port=port, user=user
                )
            )

        return SSHConnection(
            host,
            username=user,
            port=port,
            key_filename=self.keyfile,
            timeout=self.timeout,
            password=self.password,
        )

    def exists(self, path_info):
        with self.ssh(path_info) as ssh:
            return ssh.exists(path_info.path)

    def batch_exists(self, path_infos, callback):
        def _exists(chunk_and_channel):
            chunk, channel = chunk_and_channel
            ret = []
            for path in chunk:
                try:
                    channel.stat(path)
                    ret.append(True)
                except IOError as exc:
                    if exc.errno != errno.ENOENT:
                        raise
                    ret.append(False)
                callback.update(path)
            return ret

        with self.ssh(path_infos[0]) as ssh:
            with ssh.open_max_sftp_channels() as channels:
                max_workers = len(channels)

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    paths = [path_info.path for path_info in path_infos]
                    chunks = to_chunks(paths, num_chunks=max_workers)
                    chunks_and_channels = zip(chunks, channels)
                    outcome = executor.map(_exists, chunks_and_channels)
                    results = list(itertools.chain.from_iterable(outcome))

            return results

    def get_file_checksum(self, path_info):
        if path_info.scheme != self.scheme:
            raise NotImplementedError

        with self.ssh(path_info) as ssh:
            return ssh.md5(path_info.path)

    def isdir(self, path_info):
        with self.ssh(path_info) as ssh:
            return ssh.isdir(path_info.path)

    def copy(self, from_info, to_info, ctx=None):
        if from_info.scheme != self.scheme or to_info.scheme != self.scheme:
            raise NotImplementedError

        if ctx:
            ctx.cp(from_info.path, to_info.path)
        else:
            with self.ssh(from_info) as ssh:
                ssh.cp(from_info.path, to_info.path)

    def remove(self, path_info):
        if path_info.scheme != self.scheme:
            raise NotImplementedError

        with self.ssh(path_info) as ssh:
            ssh.remove(path_info.path)

    def move(self, from_info, to_info):
        if from_info.scheme != self.scheme or to_info.scheme != self.scheme:
            raise NotImplementedError

        with self.ssh(from_info) as ssh:
            ssh.move(from_info.path, to_info.path)

    def transfer_context(self):
        return self.ssh(self.path_info)

    def _download(
        self,
        from_info,
        to_file,
        name=None,
        ctx=None,
        no_progress_bar=False,
        resume=False,
    ):
        assert from_info.isin(self.path_info)
        ctx.download(
            from_info.path,
            to_file,
            progress_title=name,
            no_progress_bar=no_progress_bar,
        )

    def _upload(
        self, from_file, to_info, name=None, ctx=None, no_progress_bar=False
    ):
        assert to_info.isin(self.path_info)
        ctx.upload(
            from_file,
            to_info.path,
            progress_title=name,
            no_progress_bar=no_progress_bar,
        )

    def list_cache_paths(self):
        with self.ssh(self.path_info) as ssh:
            return list(ssh.walk_files(self.path_info.path))

    def walk(self, path_info):
        with self.ssh(path_info) as ssh:
            for entry in ssh.walk(path_info.path):
                yield entry

    def makedirs(self, path_info):
        with self.ssh(path_info) as ssh:
            ssh.makedirs(path_info.path)
