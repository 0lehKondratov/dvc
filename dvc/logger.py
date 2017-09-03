import sys
import logging

import colorama

colorama.init()

class Logger(object):
    DEFAULT_LEVEL = logging.INFO

    LEVEL_MAP = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warn': logging.WARNING,
        'error': logging.ERROR
    }

    COLOR_MAP = {
        'debug': colorama.Fore.BLUE,
        'warn': colorama.Fore.YELLOW,
        'error': colorama.Fore.RED
    }

    logging.basicConfig(stream=sys.stdout, format='%(message)s', level=DEFAULT_LEVEL)

    _logger = logging.getLogger('dvc')

    @staticmethod
    def set_level(level):
        Logger._logger.setLevel(Logger.LEVEL_MAP.get(level.lower(), logging.DEBUG))

    @staticmethod
    def be_quiet():
        Logger._logger.setLevel(logging.CRITICAL)

    @staticmethod
    def be_verbose():
        Logger._logger.setLevel(logging.DEBUG)

    @staticmethod
    def colorize(msg, typ):
        header = ''
        footer = ''

        if sys.stdout.isatty():
            header = Logger.COLOR_MAP.get(typ.lower(), '')
            footer = colorama.Style.RESET_ALL

        return u'{}{}{}'.format(header, msg, footer)

    @staticmethod
    def error(msg, **kwargs):
        return Logger._logger.error(Logger.colorize(msg, 'error'), **kwargs)

    @staticmethod
    def warn(msg, **kwargs):
        return Logger._logger.warn(Logger.colorize(msg, 'warn'), **kwargs)

    @staticmethod
    def debug(msg, **kwargs):
        return Logger._logger.debug(Logger.colorize(msg, 'debug'), **kwargs)

    @staticmethod
    def info(msg, **kwargs):
        return Logger._logger.info(Logger.colorize(msg, 'info'), **kwargs)
