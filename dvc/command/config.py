import os
import configobj

from dvc.command.common.base import CmdBase
from dvc.logger import Logger
from dvc.config import Config
from dvc.project import Project


class CmdConfig(CmdBase):
    def __init__(self, args):
        self.args = args
        root_dir = self._find_root()
        if args.local:
            config = Config.CONFIG_LOCAL
        else:
            config = Config.CONFIG
        self.config_file = os.path.join(root_dir, Project.DVC_DIR, config)
        # Using configobj because it doesn't
        # drop comments like configparser does.
        self.configobj = configobj.ConfigObj(self.config_file)

    def run_cmd(self):
        return self.run()

    def _get_key(self, d, name, add=False):
        for k in d.keys():
            if k.lower() == name.lower():
                return k

        if add:
            d[name] = {}
            return name

        return None

    def save(self):
        try:
            self.configobj.write()
        except Exception as exc:
            Logger.error("Failed to write config '{}'".format(self.configobj.filename), exc)
            return 1
        return 0

    def unset(self, section, opt):
        if section not in self.configobj.keys():
            Logger.error("Section '{}' doesn't exist".format(section))
            return 1

        if opt not in self.configobj[section].keys():
            return 0

        del self.configobj[section][opt]
        if len(self.configobj[section]) == 0:
            del self.configobj[section]

        return self.save()

    def show(self):
        Logger.info(self.configobj[self.section][self.opt])
        return 0

    def set(self, section, opt, value):
        if section not in self.configobj.keys():
            self.configobj[section] = {}

        self.configobj[section][opt] = value

        return self.save()

    def check_opt(self):
        _section, _opt = self.args.name.strip().split('.', 1)
        add = (self.args.value != None and self.args.unset == False)

        section = self._get_key(self.configobj, _section, add)

        if not section:
            Logger.error('Invalid option name {}'.format(_section))
            return 1

        opt = self._get_key(self.configobj[section], _opt, add)
        if not opt:
            Logger.error('Invalid option value: {}'.format(_opt))
            return 1

        self.section = section
        self.opt = opt

        return 0

    def run(self):
        if self.check_opt() != 0:
            return 1

        if self.args.unset:
            return self.unset(self.section, self.opt)

        if self.args.value is None:
            return self.show()

        return self.set(self.section, self.opt, self.args.value)
