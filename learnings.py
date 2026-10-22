"""What a Machine has learned, without the messages it learned it from: for studying how The Machine works across
installs and baking it into the product (`python3 learnings.py --database ...`, or the hosting agent's `learnings`
command).

In: feedback and the rules, facts and per-channel voices learned from it, the owner's profile and notes (USER.md, MEMORY.md, memory/),
skills and proposals, how reviews, preparations, cards and Claude Code or Codex jobs went (states, error codes, times), roles learned for people
in counts, code faults, which sources are on and how they sync.
Never: what messages say (subjects, bodies, metadata beyond counts, card text, drafts, tool results; see
message_seal.py), names or addresses of people, tokens. The database is read without the message key and only the
columns below are selected, so a sealed database gives the same answer as a plain one."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

ROWS = 2000
TEXT = 4000
# table: columns; an entry 'expr AS name' is computed. Never a column message_seal seals (test_message_seal checks).
TABLES = {
    'suggestions': ('id', 'status', 'created_at', 'snoozed_until', 'early', 'urgent', 'due', 'feedback_kind', 'closed_state',
                    'resume_status', 'completed_from', 'project',
                    "json_array_length(event_ids) AS signals",
                    "(SELECT e.kind FROM events e WHERE e.id=json_extract(suggestions.event_ids,'$[0]')) AS kind"),
    'decisions': ('id', 'suggestion_id', 'choice', 'feedback', 'source', 'created_at'),
    'decision_annotations': ('decision_id', 'status', 'reason', 'superseded_by'),
    'note_kinds': ('decision_id', 'suggestion_id', 'kind', 'source', 'created_at'),
    'facts': ('id', 'kind', 'text', 'suggestion_id', 'created_at', 'removed_at', 'merged_into'),
    'rule_changes': ('at', 'kind', 'text'),
    'voices': ('channel', 'text', 'source', 'updated_at'),
    'draft_edits': ('suggestion_id', 'kind', 'source', 'created_at'),
    'deliveries': ('suggestion_id', 'status', 'error', 'updated_at', 'needs_edit'),
    'agent_jobs': ('suggestion_id', 'tool', 'place', 'status', 'error_kind', 'turns', 'outcome', 'created_at', 'updated_at', 'started_at', 'finished_at'),
    'action_jobs': ('suggestion_id', 'status', 'source', 'error', 'created_at', 'updated_at', "json_extract(payload,'$.kind') AS kind"),
    'preparations': ('suggestion_id', 'status', 'reason', 'error', 'attempts', 'auto_retries', 'created_at', 'started_at', 'updated_at'),
    'preparation_runs': ('job_id', 'status', 'started_at', 'finished_at', 'history_read'),
    'review_runs': ('run_id', 'status', 'started_at', 'finished_at', 'attempts', 'error', 'preview'),
    'profile_runs': ('run_id', 'status', 'started_at', 'finished_at', 'attempts', 'error', 'draft'),
    'analyses': ('id', 'status', 'started_at', 'finished_at', "json_array_length(items) AS signals"),
    'promises': ('direction', 'status', 'due', 'created_at'),
    'skills': ('slug', 'name', 'description', 'body', 'status', 'origin', 'version', 'note', 'created_at', 'updated_at'),
    'skill_hints': ('suggestion_id', 'skill', 'kind', 'name'),
    'tool_approval_proposals': ('title', 'detail', 'created_at'),
    'faults': ('day', 'kind', 'frames', 'location', 'version', 'count'),
    'sync_activity': ('at', 'status', 'signals', 'error', 'retry_at', 'account_id'),
    'checkpoints': ('account_id', 'last_success', 'error', 'error_at', 'failures'),
}
COUNTS = {'roles': "SELECT role, source, count(*) n FROM person_roles GROUP BY role, source",
          'role_corrections': "SELECT old_role, new_role, count(*) n FROM role_corrections GROUP BY old_role, new_role",
          'signals': "SELECT kind, count(*) n, min(occurred_at) first, max(occurred_at) last FROM events WHERE deleted=0 GROUP BY kind"}
NOTES = ('USER.md', 'MEMORY.md', 'AGENTS.md', 'memory')  # in the OpenClaw workspace
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
SCRUB_KEYS = {'error', 'account_id'}


def _accounts(rows):
    """Account ids name mailboxes: kept only as service and a number."""
    seen = {}
    for row in rows:
        if row.get('account_id'):
            seen.setdefault(row['account_id'], f"{row['account_id'].split(':')[0]}#{len(seen) + 1}")
    return seen


def export(database, workspace=None, config=None):
    out = {'tables': {}, 'counts': {}}
    with closing(sqlite3.connect(f'file:{Path(database).expanduser()}?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in TABLES.items():
            if table not in tables:
                continue
            have = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            picked = [c for c in columns if ' AS ' in c or c in have]
            order = next((c for c in ('created_at', 'at', 'started_at', 'day', 'updated_at') if c in have), 'rowid')
            try:
                rows = [dict(r) for r in db.execute(f'SELECT {",".join(picked)} FROM {table} ORDER BY {order} DESC LIMIT ?', (ROWS,))]
            except sqlite3.Error:
                rows = [dict(r) for r in db.execute(f'SELECT {",".join(c for c in picked if " AS " not in c)} FROM {table} '
                                                    f'ORDER BY {order} DESC LIMIT ?', (ROWS,))]
            out['tables'][table] = rows
        names = _accounts([r for t in out['tables'].values() for r in t])
        for rows in out['tables'].values():
            for row in rows:
                for key, value in row.items():
                    if key == 'account_id' and value:
                        row[key] = names.get(value, value.split(':')[0])
                    elif isinstance(value, str):
                        row[key] = EMAIL.sub('[email]', value)[:TEXT] if key in SCRUB_KEYS else value[:TEXT]
        for name, query in COUNTS.items():
            try:
                out['counts'][name] = [dict(r) for r in db.execute(query)]
            except sqlite3.Error:
                pass
    if workspace:
        out['notes'] = notes(Path(workspace).expanduser())
    if config:
        out['config'] = settings(config)
    return out


def notes(workspace):
    found = {}
    for name in NOTES:
        path = workspace / name
        files = sorted(path.rglob('*.md'))[:200] if path.is_dir() else [path] if path.is_file() else []
        for file in files:
            try:
                found[str(file.relative_to(workspace))] = file.read_text(errors='replace')[:20000]
            except OSError:
                pass
    return found


def settings(config):
    """Which sources are on, never a token, address or path. (The model is in the hosting agent's report.)"""
    return {'message_protection': config.get('message_protection'),
            'accounts': [{'service': a.get('service'), 'boundary': a.get('boundary'), 'enabled': a.get('enabled', True)}
                         for a in config.get('accounts') or [] if isinstance(a, dict)]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--database', required=True)
    parser.add_argument('--workspace')
    args = parser.parse_args()
    print(json.dumps(export(args.database, args.workspace), indent=1, ensure_ascii=False, default=str))
