from __future__ import unicode_literals

import traceback
import logging
import colorama

import dvc.logger
from dvc.exceptions import DvcException


dvc.logger.setup()
logger = logging.getLogger("dvc")
formatter = dvc.logger.ColorFormatter()
colors = {
    "blue": colorama.Fore.BLUE,
    "red": colorama.Fore.RED,
    "yellow": colorama.Fore.YELLOW,
    "nc": colorama.Fore.RESET,
}


class TestColorFormatter:
    def test_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="dvc"):
            logger.debug("message")

        expected = "{blue}DEBUG{nc}: message".format(**colors)

        assert expected == formatter.format(caplog.records[0])

    def test_info(self, caplog):
        logger.info("message")

        assert "message" == formatter.format(caplog.records[0])

    def test_warning(self, caplog):
        logger.warning("message")

        expected = "{yellow}WARNING{nc}: message".format(**colors)

        assert expected == formatter.format(caplog.records[0])

    def test_error(self, caplog):
        logger.error("message")

        expected = (
            "{red}ERROR{nc}: message\n"
            "\n"
            "{footer}".format(footer=formatter.footer, **colors)
        )

        assert expected == formatter.format(caplog.records[0])

    def test_exception(self, caplog):
        try:
            raise ValueError
        except Exception:
            logger.exception("message")

        expected = (
            "{red}ERROR{nc}: message\n"
            "\n"
            "{footer}".format(footer=formatter.footer, **colors)
        )

        with caplog.at_level(logging.INFO, logger="dvc"):
            assert expected == formatter.format(caplog.records[0])

    def test_exception_with_description_and_without_message(self, caplog):
        try:
            raise Exception("description")
        except Exception:
            logger.exception("")

        expected = (
            "{red}ERROR{nc}: description\n"
            "\n"
            "{footer}".format(footer=formatter.footer, **colors)
        )

        with caplog.at_level(logging.INFO, logger="dvc"):
            assert expected == formatter.format(caplog.records[0])

    def test_exception_with_description_and_message(self, caplog):
        with caplog.at_level(logging.INFO, logger="dvc"):
            try:
                raise Exception("description")
            except Exception:
                logger.exception("message")

            expected = (
                "{red}ERROR{nc}: message - description\n"
                "\n"
                "{footer}".format(footer=formatter.footer, **colors)
            )

            assert expected == formatter.format(caplog.records[0])

    def test_exception_under_verbose(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="dvc"):
            try:
                raise Exception("description")
            except Exception:
                stack_trace = traceback.format_exc()
                logger.exception("")

            expected = (
                "{red}ERROR{nc}: description\n"
                "{red}{line}{nc}\n"
                "{stack_trace}"
                "{red}{line}{nc}\n"
                "\n"
                "{footer}".format(
                    footer=formatter.footer,
                    line="-" * 60,
                    stack_trace=stack_trace,
                    **colors
                )
            )

            assert expected == formatter.format(caplog.records[0])

    def test_nested_exceptions(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="dvc"):
            try:
                raise Exception("first")
            except Exception as exc:
                first_traceback = traceback.format_exc()
                try:
                    raise DvcException("second", cause=exc)
                except DvcException:
                    second_traceback = traceback.format_exc()
                    logger.exception("message")

            expected = (
                "{red}ERROR{nc}: message - second: first\n"
                "{red}{line}{nc}\n"
                "{stack_trace}"
                "{red}{line}{nc}\n"
                "\n"
                "{footer}".format(
                    footer=formatter.footer,
                    line="-" * 60,
                    stack_trace="\n".join([first_traceback, second_traceback]),
                    **colors
                )
            )

            assert expected == formatter.format(caplog.records[0])
