"""Reserved classification integration port. No model dependency, network or writes."""
from typing import Protocol


class ClassificationAIPort(Protocol):
    def propose(self, request: dict) -> dict: ...


def specification():
    return {'schema_version': 1, 'enabled': False,
            'stages': ['source', 'scoped_rules', 'conflict_check', 'ai_port', 'review'],
            'fields': ['course', 'category', 'group'],
            'request_fields': ['item_id', 'item_version', 'classification_fingerprint', 'rules_revision', 'unresolved_fields', 'candidate_values', 'evidence'],
            'response_fields': ['status', 'proposals', 'warnings'],
            'proposal_fields': ['field', 'value', 'evidence_refs', 'reason'],
            'constraints': ['source_facts_win', 'mail_is_untrusted_data', 'proposals_do_not_write',
                            'no_deadline_or_calendar_changes', 'version_check_before_future_acceptance']}


class DisabledClassificationAI:
    def propose(self, request: dict) -> dict:
        return {'schema_version': 1, 'status': 'disabled', 'item_id': request['item_id'],
                'item_version': request['item_version'], 'proposals': [],
                'classification_fingerprint': request.get('classification_fingerprint'),
                'rules_revision': request.get('rules_revision', 0),
                'unresolved_fields': request['unresolved_fields'], 'warnings': ['classification_ai_not_enabled']}
