"""Native pywin32 SCM host. Installation is an explicit operator action."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import threading
from pathlib import Path

SERVICE = 'AcademicOSV2'

if os.name == 'nt':
    import servicemanager
    import win32service
    import win32serviceutil

    class AcademicOSService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE
        _svc_display_name_ = 'Academic OS 2.0'
        _svc_description_ = 'Local academic operations and authenticated read-only dashboard'

        def __init__(self, args):
            super().__init__(args)
            self.stop = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=120000)
            self.stop.set()

        def SvcDoRun(self):
            from .config import Settings
            from .core import Core
            from .db import Database
            from .runtime import InstanceLock, Runtime
            try:
                config = Path(win32serviceutil.GetServiceCustomOption(SERVICE, 'ConfigPath')).resolve()
                settings = Settings.load(config)
                with InstanceLock(settings.data_dir):
                    core = Core(Database(settings.database_path), settings)
                Runtime(core, config).run(self.stop)
            except BaseException:
                servicemanager.LogErrorMsg('Academic OS runtime failed; inspect configuration, ports and instance ownership. No credentials are logged.')
                self.ReportServiceStatus(win32service.SERVICE_STOPPED, win32ExitCode=1066, svcExitCode=1)
                raise


def main(argv=None):
    if os.name != 'nt':
        raise SystemExit('Windows service control is available on Windows only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['host', 'install', 'start', 'stop', 'remove', 'status'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--account', help=r'Same credential-owning account, e.g. COMPUTER\owner')
    args = parser.parse_args(argv)
    if args.command == 'host':
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(AcademicOSService)
        servicemanager.StartServiceCtrlDispatcher()
    elif args.command == 'install':
        if not args.config or not args.account:
            parser.error('install requires --config and --account; LocalSystem cannot read your Credential Manager')
        from .config import Settings
        config = args.config.resolve()
        Settings.load(config)
        password = getpass.getpass(f'Windows password for {args.account}: ')
        # python.exe hosts the native dispatcher, preserving the venv and avoiding global DLL installation.
        win32serviceutil.InstallService('academic_os.windows_service.AcademicOSService', SERVICE, 'Academic OS 2.0',
                                       startType=win32service.SERVICE_AUTO_START, userName=args.account, password=password,
                                       exeName=sys.executable, exeArgs='-m academic_os.windows_service host',
                                       description='Academic OS single-node runtime', delayedstart=True)
        win32serviceutil.SetServiceCustomOption(SERVICE, 'ConfigPath', str(config))
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            service = win32service.OpenService(manager, SERVICE, win32service.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, {'ResetPeriod': 86400, 'RebootMsg': '', 'Command': '', 'Actions': [(win32service.SC_ACTION_RESTART, 60000), (win32service.SC_ACTION_RESTART, 120000), (win32service.SC_ACTION_RESTART, 300000)]})
                win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
            finally:
                win32service.CloseServiceHandle(service)
        finally:
            win32service.CloseServiceHandle(manager)
        print('Installed with delayed automatic start and failure recovery; service has not been started.')
    elif args.command == 'start':
        win32serviceutil.StartService(SERVICE)
    elif args.command == 'stop':
        win32serviceutil.StopService(SERVICE)
        win32serviceutil.WaitForServiceStatus(SERVICE, win32service.SERVICE_STOPPED, 120)
    elif args.command == 'remove':
        win32serviceutil.RemoveService(SERVICE)
    else:
        print(win32serviceutil.QueryServiceStatus(SERVICE))


if __name__ == '__main__':
    main()
