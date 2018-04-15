from __future__ import print_function
import sys
import threading 

class Progress(object):
    """
    Simple multi-target progress bar.
    """
    def __init__(self):
        self._n_total = 0
        self._n_finished = 0
        self._lock = threading.Lock()

    def set_n_total(self, total):
        self._n_total = total
        self._n_finished = 0

    def _clearln(self):
        print('\r\x1b[K', end='')

    def _writeln(self, line):
        self._clearln()
        print(line, end='')
        sys.stdout.flush()

    def update_target(self, name, current, total):
        # Just go away if it is locked. Will update next time
        if not self._lock.acquire(False):
            return

        bar = self._bar(name, current, total)

        if sys.stdout.isatty():
            self._writeln(bar)

        self._lock.release()

    def finish_target(self, name):
        # We have to write a msg about finished target
        with self._lock:
            bar = self._bar(name, 100, 100)

            if sys.stdout.isatty():
                self._clearln()

            print(bar)

            self._n_finished += 1

    def _bar(self, target_name, current, total):
        """
        Make a progress bar out of info, which looks like:
        (1/2): [########################################] 100% master.zip
        """
        total_len = 100
        bar_len = 30

        if total == None:
            progress = 0
            percent = "?% "
        else:
            total = int(total)
            progress = int((100 * current)/total) if current < total else 100
            percent = str(progress) + "% "

        num = "({}/{}): ".format(self._n_finished + 1, self._n_total)

        n_sh = int((progress * bar_len)/100)
        n_sp = bar_len - n_sh
        bar = "[" + '#'*n_sh + ' '*n_sp + "] "

        name_len = total_len - len(num + bar + percent)
        name = target_name[:name_len] if len(target_name) > name_len else target_name

        return num + bar + percent + name

progress = Progress()
