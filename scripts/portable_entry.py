import sys

if sys.platform == 'win32' and sys.argv[1:2] == ['--moodle-callback']:
    import ctypes
    ctypes.windll.kernel32.FreeConsole()

from academic_os.portable import main

if __name__ == "__main__":
    raise SystemExit(main())
