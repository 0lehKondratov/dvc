import errno
import os
from concurrent.futures import ThreadPoolExecutor

from dvc.exceptions import DvcIgnoreInCollectedDirError
from dvc.hash_info import HashInfo
from dvc.ignore import DvcIgnore
from dvc.progress import Tqdm
from dvc.utils import file_md5


def get_file_hash(path_info, tree, name):
    info = tree.info(path_info)
    if name in info:
        assert not info[name].endswith(".dir")
        return HashInfo(name, info[name], size=info["size"])

    func = getattr(tree, name, None)
    if func:
        return func(path_info)

    if name == "md5":
        return HashInfo(
            name, file_md5(path_info, tree), size=tree.getsize(path_info)
        )

    raise NotImplementedError


def _calculate_hashes(file_infos, tree, name):
    def _get_file_hash(path_info):
        return get_file_hash(path_info, tree, name)

    with Tqdm(
        total=len(file_infos),
        unit="md5",
        desc="Computing file/dir hashes (only done once)",
    ) as pbar:
        worker = pbar.wrap_fn(_get_file_hash)
        with ThreadPoolExecutor(max_workers=tree.hash_jobs) as executor:
            hash_infos = executor.map(worker, file_infos)
            return dict(zip(file_infos, hash_infos))


def _iter_hashes(path_info, tree, name, **kwargs):
    if name in tree.DETAIL_FIELDS:
        for details in tree.ls(path_info, recursive=True, detail=True):
            file_info = path_info.replace(path=details["name"])
            hash_info = HashInfo(
                name, details[name], size=details.get("size"),
            )
            yield file_info, hash_info

        return None

    file_infos = []
    for file_info in tree.walk_files(path_info, **kwargs):
        hash_info = tree.state.get(  # pylint: disable=assignment-from-none
            file_info
        )
        if not hash_info:
            file_infos.append(file_info)
            continue
        assert hash_info.name == name
        yield file_info, hash_info

    yield from _calculate_hashes(file_infos, tree, name).items()


def _collect_dir(path_info, tree, name, **kwargs):
    from dvc.dir_info import DirInfo

    dir_info = DirInfo()
    for fi, hi in _iter_hashes(path_info, tree, name, **kwargs):
        if DvcIgnore.DVCIGNORE_FILE == fi.name:
            raise DvcIgnoreInCollectedDirError(fi.parent)

        # NOTE: this is lossy transformation:
        #   "hey\there" -> "hey/there"
        #   "hey/there" -> "hey/there"
        # The latter is fine filename on Windows, which
        # will transform to dir/file on back transform.
        #
        # Yes, this is a BUG, as long as we permit "/" in
        # filenames on Windows and "\" on Unix
        dir_info.trie[fi.relative_to(path_info).parts] = hi

    return dir_info


def get_dir_hash(path_info, tree, name, **kwargs):
    from dvc.objects import Tree

    dir_info = _collect_dir(path_info, tree, name, **kwargs)
    hash_info = Tree.save_dir_info(tree.repo.cache.local, dir_info)
    hash_info.size = dir_info.size
    hash_info.dir_info = dir_info
    return hash_info


def get_hash(path_info, tree, name, **kwargs):
    assert path_info and (
        isinstance(path_info, str) or path_info.scheme == tree.scheme
    )

    if not tree.exists(path_info):
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), path_info
        )

    with tree.state:
        # pylint: disable=assignment-from-none
        hash_info = tree.state.get(path_info)

        # If we have dir hash in state db, but dir cache file is lost,
        # then we need to recollect the dir via .get_dir_hash() call below,
        # see https://github.com/iterative/dvc/issues/2219 for context
        if (
            hash_info
            and hash_info.isdir
            and not tree.cache.tree.exists(
                tree.cache.hash_to_path_info(hash_info.value)
            )
        ):
            hash_info = None

        if hash_info:
            if hash_info.isdir:
                from dvc.objects import Tree

                # NOTE: loading the tree will restore hash_info.dir_info
                Tree.load(tree.cache, hash_info)
            assert hash_info.name == name
            return hash_info

        if tree.isdir(path_info):
            hash_info = get_dir_hash(path_info, tree, name, **kwargs)
        else:
            hash_info = get_file_hash(path_info, tree, name)

        if hash_info and tree.exists(path_info):
            tree.state.save(path_info, hash_info)

    return hash_info
