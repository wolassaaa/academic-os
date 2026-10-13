"""Receive a local school callback and report safe, actionable results."""
from __future__ import annotations

import json
import time

import httpx

from .config import Settings

KNOWN_ERRORS = {
    '无效的学校登录回调。': 'invalid_callback',
    '授权已失效或已使用，请重新登录。': 'expired_flow',
    '后台登录已失效，请重新登录后台。': 'expired_session',
    '连接设置已变更，请重新发起授权。': 'changed_connection',
}
SCHOOL_CODES = {'invalidtoken', 'accessexception', 'webservice_access_exception', 'servicenotavailable',
                'invalidfunction', 'wsfunctionnotavailable', 'invalidparameter', 'moodle_api_error'}


def _request(url, uri):
    with httpx.Client(trust_env=False, timeout=90) as client:
        return client.post(url, json={'uri': uri})


def complete_callback(uri, config, *, request=_request, notify=None):
    report = {'time': time.time(), 'ok': False}
    message = '学校授权未完成，请返回工作台重新发起学校登录。'
    settings = None
    try:
        settings = Settings.load(config)
        response = request(f'http://127.0.0.1:{settings.port}/api/v1/connections/moodle/callback', uri)
        report['http_status'] = response.status_code
        if response.status_code == 200:
            report['ok'] = True
            message = '学校账号连接成功，可以返回工作台。'
        else:
            try:
                body = response.json()
                code = body.get('code')
                report['error_code'] = code if code in SCHOOL_CODES else KNOWN_ERRORS.get(body.get('error'), 'callback_rejected')
            except (ValueError, AttributeError, TypeError):
                report['error_code'] = 'callback_rejected'
            if report['error_code'] == 'expired_flow':
                message = '本次学校授权已失效或送到了另一个后台。请关闭旧版窗口，在当前工作台重新点“学校网页登录”，不要刷新旧白屏页面。'
            elif report['error_code'] == 'invalid_callback':
                message = '学校回传的授权格式无法识别，请返回工作台重新登录。'
            elif report['error_code'] in SCHOOL_CODES:
                message = '学校登录已完成，但 Moodle 移动接口拒绝连接。错误类别：' + report['error_code'] + '。请将此提示告诉维护人员。'
            elif response.status_code == 502:
                message = '学校授权已回传，但学校接口验证失败。请返回工作台重试；安全诊断已记录。'
    except httpx.HTTPError:
        report['error_code'] = 'local_backend_unavailable'
        message = '学校授权未能送达本机后台。请保持 Academic OS 运行，在当前工作台重新发起学校登录。'
    except Exception:
        report['error_code'] = 'callback_processing_failed'
    if settings is not None:
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            (settings.data_dir / 'moodle-login-result.json').write_text(json.dumps(report), encoding='utf-8')
        except OSError:
            pass
    if notify is not None:
        notify(message)
    elif not report['ok']:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Academic OS · 学校登录结果', message, parent=root)
            root.destroy()
        except Exception:
            pass
    return 0 if report['ok'] else 1
