"""Deterministic source-backed classification; labels never confirm mail or create deadlines."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

from .db import dumps
from .mail_triage import formal


PATTERNS = {'exam': r'\bexam\b|考试', 'quiz': r'\bquiz\b|测验', 'assignment': r'\b(?:assignment|homework|essay|lab|project)\b|作业|实验报告', 'event': r'\b(?:lecture|workshop|seminar)\b|讲座|活动'}


def resolve(candidates, default=None):
    values = list(dict.fromkeys(c['value'] for c in candidates))
    if len(values) > 1:
        return {'value': None, 'status': 'conflict', 'candidates': values, 'evidence': candidates}
    return {'value': values[0] if values else default, 'status': 'rule_identified' if values else 'unidentified' if default is None else 'not_applicable', 'candidates': values, 'evidence': candidates}


def classify(item, rules=()):
    evidence = item.get('evidence', [])
    text = str(item.get('title', ''))
    source_group = 'school' if any(e.get('source', '').startswith('moodle') for e in evidence) else 'mail' if any(e.get('source', '').startswith('gmail:') for e in evidence) else 'manual' if item.get('manual_origin') else 'other'
    course = str(item.get('course') or '').strip()
    if course.casefold() in {'unknown course', 'unknown', '未知课程'}:
        course = ''
    identity = next((e for e in reversed(evidence) if e.get('course_id') is not None), {})
    site = str(identity.get('course_site') or '')
    site = (urlsplit(site).scheme.casefold() + '://' + urlsplit(site).netloc.casefold() + urlsplit(site).path.rstrip('/')) if site else next((e.get('source', '') for e in evidence if not e.get('source', '').startswith('gmail:')), 'mail')
    scope = f"{site}:course:{identity['course_id']}" if identity else 'course-name:' + course.casefold()
    applicable = [r for r in rules if (not r['scope'].get('source') or any(e.get('source') == r['scope']['source'] for e in evidence)) and (not r['scope'].get('course_key') or course and scope == r['scope']['course_key']) and any(term.casefold() in text.casefold() for term in r['terms'])]
    matches = {field: [{'value': r['value'], 'rule_id': r['id'], 'scope': r['scope'], 'basis': 'title_terms'} for r in applicable if r['field'] == field] for field in ('category', 'course', 'group')}
    if course:
        course_field = {'value': course, 'status': 'source_identified', 'candidates': [course], 'evidence': [{'basis': 'item.course', 'course_id': identity.get('course_id'), 'value': course}]}
    else:
        codes = [m[1].replace(' ', '').replace('-', '') for m in re.finditer(r'(?<![A-Za-z0-9])([A-Z]{3,4}[ -]?\d{4})(?!\d)', text)]
        matches['course'] += [{'value': code, 'rule_id': 'explicit_course_code', 'basis': text} for code in codes]
        course_field = resolve(matches['course'])
        course = course_field['value'] or ''
        if not identity:
            scope = 'course-name:' + course.casefold()
    # Course evidence is resolved before course-scoped type/group rules.
    applicable = [r for r in rules if (not r['scope'].get('source') or any(e.get('source') == r['scope']['source'] for e in evidence)) and (not r['scope'].get('course_key') or course and scope == r['scope']['course_key']) and any(term.casefold() in text.casefold() for term in r['terms'])]
    for field in ('category', 'group'):
        matches[field] = [{'value': r['value'], 'rule_id': r['id'], 'scope': r['scope'], 'basis': 'title_terms'} for r in applicable if r['field'] == field]
    kind = item.get('kind', 'task')
    if kind in PATTERNS:
        category_field = {'value': kind, 'status': 'source_identified', 'candidates': [kind], 'evidence': [{'basis': 'item.kind', 'value': kind}]}
    else:
        # Evaluate every applicable rule; a match is evidence, not an early exit.
        if not matches['category']:
            matches['category'] += [{'value': kind, 'rule_id': 'title.' + kind, 'scope': 'untyped_item', 'basis': text} for kind, pattern in PATTERNS.items() if re.search(pattern, text, re.I)]
        category_field = resolve(matches['category'], 'task')
    groups = []
    for group in re.finditer(r'\b(?:group|team)\s+([A-Z0-9]{1,12})\b|第\s*([一二三四五六七八九十\d]+)\s*组', text, re.I):
        if (group[1] or '').casefold() not in {'project', 'work', 'assignment', 'members', 'meeting'}:
            groups.append(('Group ' + group[1]) if group[1] else '第' + group[2] + '组')
    matches['group'] += [{'value': group, 'rule_id': 'explicit_group', 'basis': text} for group in groups]
    group_field = resolve(matches['group'], '')
    if group_field['value'] and not course:
        group_field['status'] = 'needs_course_scope'
    fields = {'course': course_field, 'category': category_field, 'group': group_field}
    unresolved = [name for name, field in fields.items() if field['status'] in {'conflict', 'unidentified', 'needs_course_scope'}]
    classification = {'version': 2, 'category': category_field['value'], 'course': course or None, 'course_key': scope if course else None,
                      'course_status': 'source' if course_field['status'] == 'source_identified' else 'explicit_code' if course else course_field['status'],
                      'group': group_field['value'] or None, 'source_group': source_group, 'formal': formal(item),
                      'basis': 'scoped_evidence_pipeline', 'fields': fields, 'unresolved_fields': unresolved,
                      'state': 'needs_review' if unresolved else 'classified',
                      'ai': {'status': 'disabled', 'requested': False, 'eligible_fields': unresolved},
                      'trace': [{'stage': 'source', 'source_group': source_group}, {'stage': 'scoped_rules', 'matched_rule_ids': [r['id'] for r in applicable]},
                                {'stage': 'conflict_check', 'unresolved_fields': unresolved}, {'stage': 'ai_port', 'status': 'disabled'}]}
    category = classification['category']
    group_name = group_field['value'] if group_field['status'] == 'rule_identified' else None
    labels = []

    def add(name, kind, label_scope):
        key = hashlib.sha256(dumps([kind, label_scope, name]).encode()).hexdigest()[:24]
        labels.append({'id': 'auto_' + key, 'name': name, 'kind': kind, 'scope': label_scope, 'version': 1, 'archived': 0, 'origin': 'auto'})

    if course:
        add(course[:80], 'course', scope)
    add({'assignment': '作业', 'quiz': '测验', 'exam': '考试', 'event': '活动', 'task': '其他事项'}.get(category, '待分类'), 'assignment' if category == 'assignment' else 'custom', 'auto:category')
    if group_name:
        add(group_name, 'group', scope if course else 'unidentified-course:' + item['id'])
    return classification, labels


def apply(item, db=None):
    row = db.execute("SELECT value FROM meta WHERE key='classification-rules'").fetchone() if db is not None else None
    rules = json.loads(row[0])['rules'] if row else []
    item['classification'], item['automatic_tags'] = classify(item, rules)
    item['classification']['rules_revision'] = json.loads(row[0])['revision'] if row else 0
    item['classification']['fingerprint'] = hashlib.sha256(dumps(item['classification']).encode()).hexdigest()


def migrate(core):
    with core.db.transaction() as db:
        if db.execute("SELECT 1 FROM meta WHERE key='automatic-classification-v2'").fetchone():
            return
        rows = db.execute('SELECT document FROM items').fetchall()
        if rows:
            core.db.backup(core.settings.data_dir / 'backups' / ('before-auto-classification-' + uuid.uuid4().hex + '.sqlite3'))
            for row in rows:
                core._put(db, json.loads(row[0]))
        db.execute("INSERT INTO meta VALUES('automatic-classification-v2','true')")


class ClassificationRules:
    def __init__(self, core):
        self.core = core

    def get(self):
        return self.core.db.get_meta('classification-rules', {'revision': 0, 'rules': []})

    def replace(self, rules, expected_revision, actor='user'):
        from .core import Conflict
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError('At most 100 scoped classification rules')
        ids = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {'id', 'scope', 'field', 'terms', 'value'}:
                raise ValueError('Invalid classification rule fields')
            if not isinstance(rule['id'], str) or not 1 <= len(rule['id']) <= 80 or rule['id'] in ids:
                raise ValueError('Rule IDs must be distinct')
            ids.add(rule['id'])
            scope = rule['scope']
            if not isinstance(scope, dict) or not scope or set(scope) - {'source', 'course_key'} or any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in scope.values()):
                raise ValueError('Each rule needs a source or course scope')
            if rule['field'] not in {'category', 'course', 'group'} or not isinstance(rule['value'], str) or not 1 <= len(rule['value']) <= 80:
                raise ValueError('Invalid rule output')
            if rule['field'] == 'category' and rule['value'] not in {'assignment', 'quiz', 'exam', 'event', 'task'}:
                raise ValueError('Invalid category')
            if not isinstance(rule['terms'], list) or not 1 <= len(rule['terms']) <= 10 or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 80 for t in rule['terms']):
                raise ValueError('Use 1–10 literal terms, not arbitrary regex')
        with self.core.db.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='classification-rules'").fetchone()
            current = json.loads(row[0]) if row else {'revision': 0, 'rules': []}
            if current['revision'] != expected_revision:
                raise Conflict('classification_rule_revision_changed')
            self.core.db.backup(self.core.settings.data_dir / 'backups' / ('before-classification-rules-' + uuid.uuid4().hex + '.sqlite3'))
            result = {'revision': expected_revision + 1, 'rules': rules}
            db.execute("INSERT OR REPLACE INTO meta VALUES('classification-rules',?)", (dumps(result),))
            for row in db.execute('SELECT document FROM items').fetchall():
                self.core._put(db, json.loads(row[0]))
            self.core.audit(db, actor, 'classification.rules.replace', None, {'revision': result['revision'], 'rule_count': len(rules)})
            return result
