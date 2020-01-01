import os

import mockssh
import pytest
from git import Repo
from git.exc import GitCommandNotFound

from dvc.remote.ssh.connection import SSHConnection
from dvc.repo import Repo as DvcRepo
from .basic_env import TestDirFixture, TestDvcGitFixture, TestGitFixture
from .dir_helpers import *  # noqa


# Prevent updater and analytics from running their processes
os.environ["DVC_TEST"] = "true"
# Ensure progress output even when not outputting to raw sys.stderr console
os.environ["DVC_IGNORE_ISATTY"] = "true"


@pytest.fixture(autouse=True)
def reset_loglevel(request, caplog):
    """
    Use it to ensure log level at the start of each test
    regardless of dvc.logger.setup(), Repo configs or whatever.
    """
    level = request.config.getoption("--log-level")
    if level:
        with caplog.at_level(level.upper(), logger="dvc"):
            yield
    else:
        yield


# Wrap class like fixture as pytest-like one to avoid code duplication
@pytest.fixture
def repo_dir():
    old_fixture = TestDirFixture()
    old_fixture.setUp()
    try:
        yield old_fixture
    finally:
        old_fixture.tearDown()


# NOTE: this duplicates code from GitFixture,
# would fix itself once class-based fixtures are removed
@pytest.fixture
def git(repo_dir):
    # NOTE: handles EAGAIN error on BSD systems (osx in our case).
    # Otherwise when running tests you might get this exception:
    #
    #    GitCommandNotFound: Cmd('git') not found due to:
    #        OSError('[Errno 35] Resource temporarily unavailable')
    retries = 5
    while True:
        try:
            git = Repo.init()
            break
        except GitCommandNotFound:
            retries -= 1
            if not retries:
                raise

    try:
        git.index.add([repo_dir.CODE])
        git.index.commit("add code")
        yield git
    finally:
        git.close()


@pytest.fixture
def dvc_repo(repo_dir):
    yield DvcRepo.init(repo_dir._root_dir, no_scm=True)


here = os.path.abspath(os.path.dirname(__file__))

user = "user"
key_path = os.path.join(here, "{0}.key".format(user))


@pytest.fixture
def ssh_server():
    users = {user: key_path}
    with mockssh.Server(users) as s:
        s.test_creds = {
            "host": s.host,
            "port": s.port,
            "username": user,
            "key_filename": key_path,
        }
        yield s


@pytest.fixture
def ssh(ssh_server):
    yield SSHConnection(**ssh_server.test_creds)


@pytest.fixture
def erepo(repo_dir):
    repo = TestDvcGitFixture()
    repo.setUp()
    try:
        stage_foo = repo.dvc.add(repo.FOO)[0]
        stage_bar = repo.dvc.add(repo.BAR)[0]
        stage_data_dir = repo.dvc.add(repo.DATA_DIR)[0]
        repo.dvc.scm.add([stage_foo.path, stage_bar.path, stage_data_dir.path])
        repo.dvc.scm.commit("init repo")

        repo.create("version", "master")
        repo.dvc.add("version")
        repo.dvc.scm.add([".gitignore", "version.dvc"])
        repo.dvc.scm.commit("master")

        repo.dvc.scm.checkout("branch", create_new=True)
        os.unlink(os.path.join(repo.root_dir, "version"))
        repo.create("version", "branch")
        repo.dvc.add("version")
        repo.dvc.scm.add([".gitignore", "version.dvc"])
        repo.dvc.scm.commit("branch")

        repo.dvc.scm.checkout("master")

        repo.dvc.scm.close()
        repo.git.close()

        os.chdir(repo._saved_dir)
        yield repo
    finally:
        repo.tearDown()


@pytest.fixture(scope="session", autouse=True)
def _close_pools():
    from dvc.remote.pool import close_pools

    yield
    close_pools()


@pytest.fixture
def git_erepo():
    repo = TestGitFixture()
    repo.setUp()
    yield repo
    repo.tearDown()
