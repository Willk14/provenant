"""Contract review: an agent reads a contract against a playbook; a lawyer decides what each finding means."""
from provenant.legal.playbook import DEFAULT_TOPICS, PLAYBOOK_VERSION, Topic, fingerprint, topics_with
from provenant.legal.toolbox import Answer, ContractDocument, Finding, ReviewToolbox

__all__ = [
    "Answer",
    "ContractDocument",
    "DEFAULT_TOPICS",
    "Finding",
    "PLAYBOOK_VERSION",
    "ReviewToolbox",
    "Topic",
    "fingerprint",
    "topics_with",
]
