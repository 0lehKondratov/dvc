import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict, Union

from funcy import cached_property, collecting, first, isa, join, reraise

from dvc.dependency.param import ParamsDependency
from dvc.exceptions import DvcException
from dvc.parsing.interpolate import ParseError
from dvc.path_info import PathInfo
from dvc.utils import relpath

from .context import (
    Context,
    ContextError,
    KeyNotInContext,
    MergeError,
    Node,
    VarsAlreadyLoaded,
)
from .interpolate import (
    check_recursive_parse_errors,
    is_interpolated_string,
    recurse,
    to_str,
)

if TYPE_CHECKING:
    from dvc.repo import Repo

logger = logging.getLogger(__name__)

STAGES_KWD = "stages"
VARS_KWD = "vars"
WDIR_KWD = "wdir"
DEFAULT_PARAMS_FILE = ParamsDependency.DEFAULT_PARAMS_FILE
PARAMS_KWD = "params"
FOREACH_KWD = "foreach"
DO_KWD = "do"

DEFAULT_SENTINEL = object()

JOIN = "@"


class ResolveError(DvcException):
    pass


class EntryNotFound(DvcException):
    pass


def _format_preamble(msg, path, spacing=" "):
    return f"failed to parse {msg} in '{path}':{spacing}"


def format_and_raise(exc, msg, path):
    spacing = (
        "\n"
        if isinstance(exc, (ParseError, MergeError, VarsAlreadyLoaded))
        else " "
    )
    message = _format_preamble(msg, path, spacing) + str(exc)

    # FIXME: cannot reraise because of how we log "cause" of the exception
    # the error message is verbose, hence need control over the spacing
    _reraise_err(ResolveError, message, from_exc=exc)


def _reraise_err(exc_cls, *args, from_exc=None):
    err = exc_cls(*args)
    if from_exc and logger.isEnabledFor(logging.DEBUG):
        raise err from from_exc
    raise err


def check_syntax_errors(
    definition: dict, name: str, path: str, where: str = "stages"
):
    for key, d in definition.items():
        try:
            check_recursive_parse_errors(d)
        except ParseError as exc:
            format_and_raise(exc, f"'{where}.{name}.{key}'", path)


def is_map_or_seq(data):
    _is_map_or_seq = isa(Mapping, Sequence)
    return not isinstance(data, str) and _is_map_or_seq(data)


def split_foreach_name(name):
    group, *keys = name.rsplit(JOIN, maxsplit=1)
    return group, first(keys)


def check_interpolations(data, where, path):
    def func(s):
        if is_interpolated_string(s):
            raise ResolveError(
                _format_preamble(f"'{where}'", path)
                + "interpolating is not allowed"
            )

    return recurse(func)(data)


Definition = Union["ForeachDefinition", "EntryDefinition"]
DictStr = Dict[str, Any]


def make_definition(
    resolver: "DataResolver", name: str, definition: DictStr, **kwargs
) -> Definition:
    args = resolver, resolver.context, name, definition
    if FOREACH_KWD in definition:
        return ForeachDefinition(*args, **kwargs)
    return EntryDefinition(*args, **kwargs)


class DataResolver:
    def __init__(self, repo: "Repo", wdir: PathInfo, d: dict):
        self.tree = tree = repo.tree
        self.wdir = wdir
        self.relpath = relpath(self.wdir / "dvc.yaml")

        vars_ = d.get(VARS_KWD, [])
        check_interpolations(vars_, VARS_KWD, self.relpath)
        self.context: Context = Context()

        try:
            args = tree, vars_, wdir  # load from `vars` section
            self.context.load_from_vars(*args, default=DEFAULT_PARAMS_FILE)
        except ContextError as exc:
            format_and_raise(exc, "'vars'", self.relpath)

        # we wrap the definitions into ForeachDefinition and EntryDefinition,
        # that helps us to optimize, cache and selectively load each one of
        # them as we need, and simplify all of this DSL/parsing logic.
        # we use `tracked_vars` to keep a dictionary of used variables
        # by the interpolated entries.
        self.tracked_vars: Dict[str, Mapping] = {}

        stages_data = d.get(STAGES_KWD, {})
        self.definitions: Dict[str, Definition] = {
            name: make_definition(self, name, definition)
            for name, definition in stages_data.items()
        }

    def resolve_one(self, name):
        group, key = split_foreach_name(name)

        if not self._has_group_and_key(group, key):
            raise EntryNotFound(f"Could not find '{name}'")

        # all of the checks for `key` not being None for `ForeachDefinition`
        # and/or `group` not existing in the `interim`, etc. should be
        # handled by the `self.has_key()` above.
        definition = self.definitions[group]
        if isinstance(definition, EntryDefinition):
            return definition.resolve()
        return definition.resolve_one(key)

    def resolve(self):
        """Used for testing purposes, otherwise use resolve_one()."""
        data = join(map(self.resolve_one, self.get_keys()))
        logger.trace(  # type: ignore[attr-defined]
            "Resolved dvc.yaml:\n%s", data
        )
        return {STAGES_KWD: data}

    def has_key(self, key):
        return self._has_group_and_key(*split_foreach_name(key))

    def _has_group_and_key(self, group, key=None):
        try:
            definition = self.definitions[group]
        except KeyError:
            return False

        if key:
            return isinstance(
                definition, ForeachDefinition
            ) and definition.has_member(key)
        return not isinstance(definition, ForeachDefinition)

    @collecting
    def get_keys(self):
        for name, definition in self.definitions.items():
            if isinstance(definition, ForeachDefinition):
                yield from definition.get_generated_names()
                continue
            yield name

    def track_vars(self, name, vars_):
        self.tracked_vars[name] = vars_


class EntryDefinition:
    def __init__(
        self,
        resolver: DataResolver,
        context: Context,
        name: str,
        definition: dict,
        where: str = STAGES_KWD,
    ):
        self.resolver = resolver
        self.wdir = self.resolver.wdir
        self.relpath = self.resolver.relpath
        self.context = context
        self.name = name
        self.definition = definition
        self.where = where

    def _resolve_wdir(
        self, context: Context, name: str, wdir: str = None
    ) -> PathInfo:
        if not wdir:
            return self.wdir

        try:
            wdir = to_str(context.resolve_str(wdir, unwrap=True))
        except (ContextError, ParseError) as exc:
            format_and_raise(exc, f"'{self.where}.{name}.wdir'", self.relpath)
        return self.wdir / wdir

    def resolve(self, **kwargs):
        try:
            return self.resolve_stage(**kwargs)
        except ContextError as exc:
            format_and_raise(exc, f"stage '{self.name}'", self.relpath)

    def resolve_stage(self, skip_checks=False):
        context = self.context
        name = self.name
        if not skip_checks:
            # we can check for syntax errors as we go for interpolated entries,
            # but for foreach-generated ones, once is enough, which it does
            # that itself. See `ForeachDefinition.do_definition`.
            check_syntax_errors(self.definition, name, self.relpath)

        # we need to pop vars from generated/evaluated data
        definition = deepcopy(self.definition)

        wdir = self._resolve_wdir(context, name, definition.get(WDIR_KWD))
        if self.wdir != wdir:
            logger.debug(
                "Stage %s has different wdir than dvc.yaml file", name
            )

        vars_ = definition.pop(VARS_KWD, [])
        # FIXME: Should `vars` be templatized?
        check_interpolations(vars_, f"{self.where}.{name}.vars", self.relpath)
        if vars_:
            # Optimization: Lookahead if it has any vars, if it does not, we
            # don't need to clone them.
            context = Context.clone(context)

        try:
            tree = self.resolver.tree
            context.load_from_vars(tree, vars_, wdir, stage_name=name)
        except VarsAlreadyLoaded as exc:
            format_and_raise(exc, f"'{self.where}.{name}.vars'", self.relpath)

        logger.trace(  # type: ignore[attr-defined]
            "Context during resolution of stage %s:\n%s", name, context
        )

        with context.track() as tracked_data:
            # NOTE: we do not pop "wdir", and resolve it again
            # this does not affect anything and is done to try to
            # track the source of `wdir` interpolation.
            # This works because of the side-effect that we do not
            # allow overwriting and/or str interpolating complex objects.
            # Fix if/when those assumptions are no longer valid.
            resolved = {
                key: self._resolve(context, value, key, skip_checks)
                for key, value in definition.items()
            }

        self.resolver.track_vars(name, tracked_data)
        return {name: resolved}

    def _resolve(self, context, value, key, skip_checks):
        try:
            return context.resolve(
                value, skip_interpolation_checks=skip_checks
            )
        except (ParseError, KeyNotInContext) as exc:
            format_and_raise(
                exc, f"'{self.where}.{self.name}.{key}'", self.relpath
            )


class ForeachDefinition:
    def __init__(
        self,
        resolver: DataResolver,
        context: Context,
        name: str,
        definition: dict,
        where: str = STAGES_KWD,
    ):
        self.resolver = resolver
        self.relpath = self.resolver.relpath
        self.context = context
        self.name = name

        assert DO_KWD in definition
        self.foreach_data = definition[FOREACH_KWD]
        self._do_definition = definition[DO_KWD]

        self.where = where

    @cached_property
    def do_definition(self):
        # optimization: check for syntax errors only once for `foreach` stages
        check_syntax_errors(self._do_definition, self.name, self.relpath)
        return self._do_definition

    @cached_property
    def resolved_iterable(self):
        return self._resolve_foreach_data()

    def _resolve_foreach_data(self):
        try:
            iterable = self.context.resolve(self.foreach_data, unwrap=False)
        except (ContextError, ParseError) as exc:
            format_and_raise(
                exc, f"'{self.where}.{self.name}.foreach'", self.relpath
            )

        # foreach data can be a resolved dictionary/list.
        self._check_is_map_or_seq(iterable)
        # foreach stages will have `item` and `key` added to the context
        # so, we better warn them if they have them already in the context
        # from the global vars. We could add them in `set_temporarily`, but
        # that'd make it display for each iteration.
        self._warn_if_overwriting(self._inserted_keys(iterable))
        return iterable

    def _check_is_map_or_seq(self, iterable):
        if not is_map_or_seq(iterable):
            node = iterable.value if isinstance(iterable, Node) else iterable
            typ = type(node).__name__
            raise ResolveError(
                f"failed to resolve '{self.where}.{self.name}.foreach'"
                f" in '{self.relpath}': expected list/dictionary, got " + typ
            )

    def _warn_if_overwriting(self, keys):
        warn_for = [k for k in keys if k in self.context]
        if warn_for:
            linking_verb = "is" if len(warn_for) == 1 else "are"
            logger.warning(
                "%s %s already specified, "
                "will be overwritten for stages generated from '%s'",
                " and ".join(warn_for),
                linking_verb,
                self.name,
            )

    @staticmethod
    def _inserted_keys(iterable):
        keys = ["item"]
        if isinstance(iterable, Mapping):
            keys.append("key")
        return keys

    @cached_property
    def normalized_iterable(self):
        """Convert sequence to Mapping with keys normalized."""
        iterable = self.resolved_iterable
        if isinstance(iterable, Mapping):
            return iterable

        assert isinstance(iterable, Sequence)
        if any(map(is_map_or_seq, iterable)):
            # if the list contains composite data, index are the keys
            return {to_str(idx): value for idx, value in enumerate(iterable)}

        # for simple lists, eg: ["foo", "bar"],  contents are the key itself
        return {to_str(value): value for value in iterable}

    def has_member(self, key):
        return key in self.normalized_iterable

    def get_generated_names(self):
        return list(map(self._generate_name, self.normalized_iterable))

    def _generate_name(self, key):
        return f"{self.name}{JOIN}{key}"

    def resolve_all(self):
        return join(map(self.resolve_one, self.normalized_iterable))

    def resolve_one(self, key):
        return self._each_iter(key)

    def _each_iter(self, key):
        name = self.name
        err_message = f"Could not find '{key}' in foreach group '{self.name}'"
        with reraise(KeyError, EntryNotFound(err_message)):
            value = self.normalized_iterable[key]

        # NOTE: we need to use resolved iterable/foreach-data,
        # not the normalized ones to figure out whether to make item/key
        # available
        inserted = self._inserted_keys(self.resolved_iterable)
        temp_dict = {"item": value}
        if "key" in inserted:
            temp_dict["key"] = key

        with self.context.set_temporarily(temp_dict):
            # optimization: item and key can be removed on __exit__() as they
            # are top-level values, and are not merged recursively.
            # This helps us avoid cloning context, which is slower
            # (increasing the size of the context might increase
            # the no. of items to be generated which means more cloning,
            # i.e. quadratic complexity).
            generated = self._generate_name(key)
            entry = EntryDefinition(
                self.resolver, self.context, generated, self.do_definition
            )
            try:
                # optimization: skip checking for syntax errors on each foreach
                # generated stages. We do it once when accessing do_definition.
                return entry.resolve_stage(skip_checks=True)
            except ContextError as exc:
                # pylint: disable=no-member
                if isinstance(exc, MergeError) and exc.key in inserted:
                    raise ResolveError(
                        f"attempted to redefine '{exc.key}' "
                        f"in stage '{generated}' generated through 'foreach'"
                    )
                format_and_raise(
                    exc,
                    f"stage '{generated}' (gen. from '{name}')",
                    self.relpath,
                )
