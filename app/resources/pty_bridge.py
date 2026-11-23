#!/usr/bin/env python3
"""Run a command inside a pseudo-terminal and relay it over plain pipes.

stdin  -> what the user types
stdout <- what the program draws
fd 3   -> "cols rows\\n" whenever the window is resized (only when RELAYFORGE_RESIZE_FD=3)

Only the standard library is used, so the app needs no compiled add-on.
"""
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios


def resize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


def main():
    command = sys.argv[1:]
    if not command:
        print('usage: pty_bridge.py COMMAND [ARGS...]', file=sys.stderr)
        return 2
    cols, rows = int(os.environ.get('COLUMNS', 100)), int(os.environ.get('LINES', 30))
    control = os.environ.get('RELAYFORGE_RESIZE_FD') == '3'  # checked before the fork: the terminal itself may get number 3
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(command[0], command)
        except OSError as error:
            print(f'无法启动 {command[0]}：{error}', file=sys.stderr)
            os._exit(127)
    resize(fd, cols, rows)
    signal.signal(signal.SIGTERM, lambda *_: os.kill(pid, signal.SIGHUP))
    watch, pending = [0, fd], b''
    if control:
        watch.append(3)
    while True:
        try:
            ready, _, _ = select.select(watch, [], [])
        except InterruptedError:
            continue
        if fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b''
            if not data:
                break
            os.write(1, data)
        if 0 in ready:
            data = os.read(0, 65536)
            if not data:
                os.kill(pid, signal.SIGHUP)
                watch.remove(0)
            else:
                os.write(fd, data)
        if control and 3 in ready:
            data = os.read(3, 4096)
            if not data:
                watch.remove(3)
            pending += data
            *lines, pending = pending.split(b'\n')
            for line in lines:
                try:
                    c, r = (int(x) for x in line.split())
                    resize(fd, c, r)
                    os.kill(pid, signal.SIGWINCH)
                except (ValueError, OSError):
                    pass
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == '__main__':
    sys.exit(main())
