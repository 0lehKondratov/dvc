"""Main entry point for dvc CLI."""

import errno
import logging

from dvc import analytics
from dvc.cli import parse_args
from dvc.config import ConfigError
from dvc.exceptions import DvcException, DvcParserError, NotDvcRepoError
from dvc.external_repo import clean_repos
from dvc.logger import FOOTER, disable_other_loggers
from dvc.remote.pool import close_pools
from dvc.utils import format_link

# Workaround for CPython bug. See [1] and [2] for more info.
# [1] https://github.com/aws/aws-cli/blob/1.16.277/awscli/clidriver.py#L55
# [2] https://bugs.python.org/issue29288

"".encode("idna")

logger = logging.getLogger("dvc")


def main(argv=None):
    """Run dvc CLI command.

    Args:
        argv: optional list of arguments to parse. sys.argv is used by default.

    Returns:
        int: command's return code.
    """
    args = None
    disable_other_loggers()

    outerLogLevel = logger.level
    try:
        args = parse_args(argv)

        verbosity = args.verbose - args.quiet
        if verbosity:
            logger.setLevel(
                {
                    -2: logging.CRITICAL,
                    -1: logging.ERROR,
                    1: logging.DEBUG,
                    2: logging.TRACE,
                }[max(-2, min(verbosity, 2))]
            )
        logger.trace(args)

        cmd = args.func(args)
        ret = cmd.run()
    except ConfigError:
        logger.exception("configuration error")
        ret = 251
    except KeyboardInterrupt:
        logger.exception("interrupted by the user")
        ret = 252
    except NotDvcRepoError:
        logger.exception("")
        ret = 253
    except DvcParserError:
        ret = 254
    except DvcException:
        ret = 255
        logger.exception("")
    except Exception as exc:  # pylint: disable=broad-except
        if isinstance(exc, OSError) and exc.errno == errno.EMFILE:
            logger.exception(
                "too many open files, please visit "
                "{} to see how to handle this "
                "problem".format(
                    format_link("https://error.dvc.org/many-files")
                ),
                extra={"tb_only": True},
            )
        else:
            logger.exception("unexpected error")
        ret = 255
    finally:
        logger.setLevel(outerLogLevel)

        # Closing pools by-hand to prevent wierd messages when closing SSH
        # connections. See https://github.com/iterative/dvc/issues/3248 for
        # more info.
        close_pools()

        # Remove cached repos in the end of the call, these are anonymous
        # so won't be reused by any other subsequent run anyway.
        clean_repos()

    if ret != 0:
        logger.info(FOOTER)

    if analytics.is_enabled():
        analytics.collect_and_send_report(args, ret)

    return ret
