"""Weekly KPI report for the v1.0 plan (milestone "v1.0 圣诞版", tracker issue #11).

Read-only: it never writes items, jobs or notifications. A KPI the stored data cannot answer yet is
reported as ``not_measured`` with the reason, never as zero, so a missing measurement cannot pass as a
good week.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .core import moment
from .mail_triage import formal

ACTIVE = {'pending', 'in_progress', 'unknown'}
TARGETS = {
    'R3_push_latency_p95_seconds': 600,
    'R4_mail_candidate_precision': 0.60,
    'R5_plan_coverage': 0.80,
    'H1_reauthorizations': 0,
    'H5_duplicate_calendar_events': 0,
    'H6_stale_sources': 0,
}


def not_measured(reason):
    return {'status': 'not_measured', 'reason': reason}


def verdict(value, target, higher_is_better=True):
    if value is None:
        return 'no_data'
    return 'pass' if (value >= target if higher_is_better else value <= target) else 'fail'


def weekly_report(core, now=None, days=7):
    if not 1 <= days <= 31:
        raise ValueError('KPI window must be 1–31 days')
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    since = start.isoformat()
    health = core.health()
    with core.db.connect() as db:
        reviews = dict(db.execute(
            "SELECT action,COUNT(*) FROM audit WHERE action IN ('mail.confirm','mail.ignore') AND created_at>=? GROUP BY action", (since,)).fetchall())
        auth_alerts = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE unique_key LIKE 'auth-required:workbench:%' AND created_at>=?", (since,)).fetchone()[0]
        agent_alerts = dict(db.execute(
            "SELECT CASE WHEN unique_key LIKE 'agent-stale:%' THEN 'stale' ELSE 'stuck' END,COUNT(*) FROM notifications "
            "WHERE (unique_key LIKE 'agent-stale:workbench:%' OR unique_key LIKE 'agent-stuck:workbench:%') AND created_at>=? GROUP BY 1", (since,)).fetchall())
        duplicates = db.execute(
            "SELECT COUNT(*) FROM (SELECT item_id FROM calendar_mappings GROUP BY item_id,provider,calendar_id HAVING COUNT(*)>1)").fetchone()[0]
    kpis = {}

    # R3: mail collected → acknowledged by Hermes (the consumer the plan pushes through).
    consumption = health['latency']['consumption']
    agent = consumption.get('hermes') or (next(iter(consumption.values())) if consumption else None)
    if agent and agent['count']:
        p95 = agent['p95_seconds']
        kpis['R3_push_latency_p95_seconds'] = {'value': p95, 'samples': agent['count'], 'collection_p95_seconds': health['latency']['collection']['p95_seconds'],
                                               'target': TARGETS['R3_push_latency_p95_seconds'], 'verdict': verdict(p95, TARGETS['R3_push_latency_p95_seconds'], False),
                                               'note': 'collected→acknowledged; add the collection stage for arrival→push'}
    else:
        kpis['R3_push_latency_p95_seconds'] = not_measured('no Hermes acknowledgements in the latency window')

    # R4: of the mail candidates reviewed in the window, how many became to-dos.
    confirmed, ignored = reviews.get('mail.confirm', 0), reviews.get('mail.ignore', 0)
    if confirmed + ignored:
        precision = round(confirmed / (confirmed + ignored), 3)
        kpis['R4_mail_candidate_precision'] = {'value': precision, 'confirmed': confirmed, 'ignored': ignored,
                                               'target': TARGETS['R4_mail_candidate_precision'], 'verdict': verdict(precision, TARGETS['R4_mail_candidate_precision'])}
    else:
        kpis['R4_mail_candidate_precision'] = not_measured('no mail candidates confirmed or ignored in the window')

    # R5: formal open items due in the next 7 days that already have a study block.
    horizon = now + timedelta(days=7)
    due_soon = [i for i in core.list_items() if formal(i) and i['status'] in ACTIVE and not i.get('calendar_deleted')
                and (due := moment(i.get('source_due_at'))) and now <= due <= horizon]
    if due_soon:
        planned = [i for i in due_soon if i.get('scheduled_start')]
        coverage = round(len(planned) / len(due_soon), 3)
        reasons = unplaced_reasons(core, now, {i['id'] for i in due_soon} - {i['id'] for i in planned})
        kpis['R5_plan_coverage'] = {'value': coverage, 'planned': len(planned), 'due_next_7_days': len(due_soon), 'unplaced_reasons': reasons,
                                    'target': TARGETS['R5_plan_coverage'], 'verdict': verdict(coverage, TARGETS['R5_plan_coverage'])}
    else:
        kpis['R5_plan_coverage'] = not_measured('no open formal items due in the next 7 days')

    for key, reason in (('R1_missed_deadlines', 'needs the Moodle reconciliation report (week 1, #13) and your own log'),
                        ('R2_moodle_coverage', 'needs the Moodle reconciliation report (week 1, #13)'),
                        ('R6_daily_use', 'no per-day board/briefing history is stored yet'),
                        ('R7_study_block_completion', 'study blocks are not tracked separately until planner v2 (week 2, #14)'),
                        ('H2_availability', 'only the latest heartbeat is stored, not its history'),
                        ('H3_sync_success_rate', 'sources keep only their latest result, not per-run history')):
        kpis[key] = not_measured(reason)

    states = health['authorizations']
    kpis['H1_reauthorizations'] = {'value': auth_alerts, 'current': {s['account_id']: s['state'] for s in states},
                                   'publishing': core.settings.google_oauth_publishing,
                                   'target': TARGETS['H1_reauthorizations'], 'verdict': verdict(auth_alerts, TARGETS['H1_reauthorizations'], False)}
    kpis['H4_agent_heartbeat'] = {'alerts': {'stale': agent_alerts.get('stale', 0), 'stuck': agent_alerts.get('stuck', 0)},
                                  'current': {c['name']: c['state'] for c in health['consumers']}}
    kpis['H5_duplicate_calendar_events'] = {'value': duplicates, 'target': 0, 'verdict': verdict(duplicates, 0, False)}
    stale = sorted(s['id'] for s in health['sources'] if s['stale'])
    kpis['H6_stale_sources'] = {'value': len(stale), 'stale': stale, 'sources': {s['id']: s.get('last_success') for s in health['sources']},
                                'target': 0, 'verdict': verdict(len(stale), 0, False) if health['sources'] else 'no_data'}
    return {'window_days': days, 'from': since, 'to': now.isoformat(), 'generated_at': now.isoformat(),
            'mode': health['mode'], 'worker_online': health['worker_online'], 'kpis': kpis}


def unplaced_reasons(core, now, item_ids):
    """Why unscheduled items would not fit, from a dry run of the planner (nothing is written)."""
    if not item_ids:
        return {}
    from .calendars import calendar_busy
    from .planner import generate_plan
    from .sources import local_busy
    try:
        plan = generate_plan(core.list_items(), calendar_busy(core, now=now) + local_busy(core, now=now), core.settings, now=now, days=7)
    except (ValueError, KeyError) as exc:
        return {'planner_error': str(exc)}
    reasons = {}
    for entry in plan['unplaced']:
        if entry['item_id'] in item_ids:
            reasons[entry['reason']] = reasons.get(entry['reason'], 0) + 1
    placeable = sum(change['item_id'] in item_ids for change in plan['changes'])
    if placeable:
        reasons['fits_but_not_scheduled'] = placeable
    return reasons


LABELS = {
    'R1_missed_deadlines': 'R1 漏截止', 'R2_moodle_coverage': 'R2 Moodle 覆盖率', 'R3_push_latency_p95_seconds': 'R3 推送延迟 p95（秒）',
    'R4_mail_candidate_precision': 'R4 邮件候选精确率', 'R5_plan_coverage': 'R5 排程覆盖率', 'R6_daily_use': 'R6 每日使用',
    'R7_study_block_completion': 'R7 学习块完成率', 'H1_reauthorizations': 'H1 需重新授权', 'H2_availability': 'H2 可用率',
    'H3_sync_success_rate': 'H3 同步成功率', 'H4_agent_heartbeat': 'H4 Hermes 心跳', 'H5_duplicate_calendar_events': 'H5 重复日历事件',
    'H6_stale_sources': 'H6 过期来源',
}
VERDICTS = {'pass': '✅ 达标', 'fail': '❌ 未达标', 'no_data': '— 无数据'}


def render_markdown(report):
    lines = [f"# Academic OS KPI 周报 · {report['from'][:10]} → {report['to'][:10]}", '',
             f"模式：{report['mode']} · Worker 在线：{'是' if report['worker_online'] else '否'} · 生成于 {report['generated_at']}", '',
             '| KPI | 读数 | 目标 | 结论 | 说明 |', '|---|---|---|---|---|']
    for key, label in LABELS.items():
        entry = report['kpis'][key]
        if entry.get('status') == 'not_measured':
            lines.append(f"| {label} | 未测量 | | — | {entry['reason']} |")
            continue
        value = entry.get('value', '')
        if isinstance(value, float) and value <= 1 and key in {'R4_mail_candidate_precision', 'R5_plan_coverage'}:
            value = f'{value:.0%}'
        target = entry.get('target', '')
        if isinstance(target, float) and target <= 1:
            target = f'≥ {target:.0%}'
        elif key == 'R3_push_latency_p95_seconds':
            target = f'≤ {target}'
        detail = {k: v for k, v in entry.items() if k not in {'value', 'target', 'verdict'}}
        note = '; '.join(f'{k}={v}' for k, v in detail.items()).replace('|', '/')
        lines.append(f"| {label} | {value} | {target} | {VERDICTS.get(entry.get('verdict'), '—')} | {note} |")
    lines += ['', '未测量的项目会在对应周补上数据来源（见 #11）。本报告只读，不修改任何数据。', '']
    return '\n'.join(lines)


def save_markdown(core, report):
    moment_ = datetime.fromisoformat(report['to'])
    year, week, _ = moment_.isocalendar()
    target = core.settings.data_dir / 'exports' / f'kpi-{year}-W{week:02d}.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(report), encoding='utf-8')
    return target
