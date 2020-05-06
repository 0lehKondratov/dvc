from functools import wraps

from funcy import decorator


@decorator
def rwlocked(call, read=None, write=None):
    import sys
    from dvc.rwlock import rwlock
    from dvc.dependency.repo import RepoDependency

    if read is None:
        read = []

    if write is None:
        write = []

    stage = call._args[0]

    assert stage.repo.lock.is_locked

    def _chain(names):
        return [
            item.path_info
            for attr in names
            for item in getattr(stage, attr)
            # There is no need to lock RepoDependency deps, as there is no
            # corresponding OutputREPO, so we can't even write it.
            if not isinstance(item, RepoDependency)
        ]

    cmd = " ".join(sys.argv)

    with rwlock(stage.repo.tmp_dir, cmd, _chain(read), _chain(write)):
        return call()


def unlocked_repo(f):
    @wraps(f)
    def wrapper(stage, *args, **kwargs):
        stage.repo.state.dump()
        stage.repo.lock.unlock()
        stage.repo._reset()
        try:
            ret = f(stage, *args, **kwargs)
        finally:
            stage.repo.lock.lock()
            stage.repo.state.load()
        return ret

    return wrapper
