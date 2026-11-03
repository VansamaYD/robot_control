"""Document integrity only; no product behavior is exercised. Standard library only."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parent
DOCS = [
    '需求规格说明书-V0.7.md',
    '代码管理与可视化执行-需求补充-V0.8.md',
    '运行规则与界面流程-需求补充-V0.9.md',
]


def expand_ids(text):
    def expand(match):
        prefix, start, end = match.groups()
        return ' '.join(f'{prefix}{i:02d}' for i in range(int(start), int(end) + 1))
    text = re.sub(r'([A-Z]{2})(\d{2})[–—-]\1(\d{2})', expand, text)
    return set(re.findall(r'\b[A-Z]{2}\d{2}\b', text))


def check():
    requirements = {}
    for name in DOCS:
        content = (ROOT / name).read_text()
        for req, priority, description in re.findall(
            r'^\| ([A-Z]{2}\d{2}) \| (P[012][^|]*)\| ([^\n]+)', content, re.M
        ):
            assert req not in requirements, f'duplicate requirement {req}'
            requirements[req] = {'priority': priority.strip(), 'document': name}
    mappings = {}
    for req in re.findall(r'^\| (OP\d{2}) \|', (ROOT / DOCS[2]).read_text(), re.M):
        assert req not in requirements
        requirements[req] = {'priority': 'P0 (proposed)', 'document': DOCS[2]}
    base = (ROOT / '验收场景与追踪矩阵-V0.7.md').read_text()
    for case, body in re.findall(r'^### (AT\d{2})[^\n]*\n(.*?)(?=^### |\Z)', base, re.M | re.S):
        trace = re.search(r'\*\*追踪：\*\*([^\n]+)', body)
        assert trace, case
        mappings[case] = expand_ids(trace.group(1))
    for name, prefix in [(DOCS[1], 'CT'), (DOCS[2], 'OT')]:
        content = (ROOT / name).read_text()
        if prefix == 'CT':
            for case, trace in re.findall(r'^\| (CT\d{2})[^|]*\| ([^|]+)\|', content, re.M):
                assert case not in mappings
                mappings[case] = expand_ids(trace)
        else:
            for req, case in re.findall(r'^\| (OP\d{2}) \| [^|]+\| (OT\d{2})：', content, re.M):
                assert case not in mappings
                mappings[case] = {req}
    assert len(mappings) == 48, len(mappings)
    covered = set().union(*mappings.values())
    unknown = sorted(covered - requirements.keys())
    missing = sorted(req for req, data in requirements.items()
                     if data['priority'].startswith('P0') and req not in covered)
    assert not unknown, unknown
    assert not missing, missing
    report = {
        'check': 'document-integrity', 'status': 'passed',
        'effective_p0_count': sum(d['priority'].startswith('P0') for d in requirements.values()),
        'acceptance_scenario_count': len(mappings),
        'missing_p0_coverage': missing, 'unknown_trace_ids': unknown,
        'product_acceptance': 'not executed',
        'traceability': {case: sorted(ids) for case, ids in mappings.items()},
    }
    (ROOT / 'requirements-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'traceability'}, ensure_ascii=False))


if __name__ == '__main__':
    check()
