import os
import ctypes
import reflink

if os.name == 'nt':
    import ntfsutils.hardlink as winlink
    from ntfsutils.fs import getdirinfo


class System(object):
    @staticmethod
    def is_unix():
        return os.name != 'nt'

    @staticmethod
    def hardlink(source, link_name):
        if System.is_unix():
            return os.link(source, link_name)

        return winlink.create(source, link_name)

    @staticmethod
    def symlink(source, link_name):
        if System.is_unix():
            return os.symlink(source, link_name)

        flags = 0
        if source is not None and os.path.isdir(source):
            flags = 1

        func = ctypes.windll.kernel32.CreateSymbolicLinkW
        func.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        func.restype = ctypes.c_ubyte

        if func(link_name, source, flags) == 0:
            raise ctypes.WinError()

    @staticmethod
    def reflink(source, link_name):
        return reflink.reflink(source, link_name)

    @staticmethod
    def inode(path):
        if System.is_unix():
            return os.stat(path).st_ino

        # getdirinfo from ntfsutils works on both files and dirs
        info = getdirinfo(path)
        return hash((info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow))
