import csv
import re
from pathlib import Path

MAPPING_RE  = re.compile(
    r'mappingId\s+(\S+)\s+target\s+(.*?)\s+source\s+(.*?)(?=mappingId|\]\])',
    re.DOTALL
)
CONDITION_RE = re.compile(
    r'["`]?(\w+)["`]?\s*(is\s+not\s+null|is\s+null|=|!=|<>|>=|<=|>|<)\s*([^\s;]+)?',
    re.IGNORECASE
)

def extract_mappings(content: str) -> list[dict]:
    return [
        {'id': m.group(1).strip(),
         'target': m.group(2).strip(),
         'source': m.group(3).strip()}
        for m in MAPPING_RE.finditer(content)
    ]


def parse_subject_and_pairs(target: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Split a target into its subject and a list of (predicate, object) pairs.
    Strips the 'a resq:X' type declaration — that becomes its own pair with
    predicate 'rdf:type'.
    """
    target = target.strip()

    # Subject is always the first token
    m = re.match(r'(\S+)\s+(.*)', target, re.DOTALL)
    if not m:
        return target, []

    subject = m.group(1).strip()
    body    = m.group(2).strip()

    # Rewrite "a resq:X" → "rdf:type resq:X" for uniform handling
    body = re.sub(r'\ba\s+(resq:\S+|scdm:\S+|sct:\S+|data:\S+)', r'rdf:type \1', body)

    # Split on semicolons that are not inside {placeholders}
    parts = re.split(r';\s*(?![^{]*})', body)

    pairs = []
    for part in parts:
        part = part.strip().rstrip('.')
        if not part:
            continue
        pm = re.match(r'(\S+)\s+(.*)', part, re.DOTALL)
        if pm:
            pred = pm.group(1).strip()
            obj  = pm.group(2).strip().rstrip('.')
            pairs.append((pred, obj))

    return subject, pairs


def parse_where_conditions(source: str) -> tuple[
    tuple[str,str,str|None],   # condition 1  (field, op, value)  — may be (None,None,None)
    tuple[str,str,str|None],   # condition 2  (AND branch)        — may be (None,None,None)
    tuple[str,str,str|None],   # condition 3  (OR branch)         — may be (None,None,None)
]:
    """
    Parse the WHERE clause of a source SQL into up to three conditions:
      - cond1 + cond2: joined by AND
      - cond1 + cond3: joined by OR (cond2 is empty in this case)

    Returns three (field, operator, value) tuples; unused slots are (None, None, None).
    """
    empty = (None, None, None)

    where_m = re.search(r'\bwhere\b\s+(.*)', source, re.IGNORECASE | re.DOTALL)
    if not where_m:
        return empty, empty, empty

    clause = where_m.group(1).strip()

    # Determine whether this is AND or OR
    has_and = bool(re.search(r'\band\b', clause, re.IGNORECASE))
    has_or  = bool(re.search(r'\bor\b',  clause, re.IGNORECASE))

    def parse_one(text: str) -> tuple[str, str, str | None]:
        m = CONDITION_RE.search(text)
        if not m:
            return empty
        field = m.group(1)
        op    = m.group(2).strip()
        val   = m.group(3).strip() if m.group(3) else None
        # Strip stray AND/OR that got captured as the value
        if val and re.match(r'^(and|or)$', val, re.IGNORECASE):
            val = None
        return field, op, val

    if has_and:
        parts = re.split(r'\band\b', clause, flags=re.IGNORECASE)
        cond1 = parse_one(parts[0])
        cond2 = parse_one(parts[1]) if len(parts) > 1 else empty
        return cond1, cond2, empty

    if has_or:
        parts = re.split(r'\bor\b', clause, flags=re.IGNORECASE)
        cond1 = parse_one(parts[0])
        # The OR branch becomes cond3; cond2 stays empty
        cond3 = parse_one(parts[1]) if len(parts) > 1 else empty
        return cond1, empty, cond3

    return parse_one(clause), empty, empty


def selected_fields(source: str) -> list[str]:
    """Return non-case_id fields selected in the SQL."""
    m = re.match(r'SELECT\s+(.*?)\s+FROM', source, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    fields = []
    for token in re.split(r',', m.group(1)):
        token = token.strip()
        alias = re.search(r'\bAS\s+(\w+)\s*$', token, re.IGNORECASE)
        if alias:
            fields.append(alias.group(1))
            continue
        simple = re.search(r'["`]?(\w+)["`]?\s*$', token)
        if simple and simple.group(1).lower() != 'case_id':
            fields.append(simple.group(1))
    return fields


COLUMNS = [
    'mapping_id',
    'subject',
    'predicate',
    'object',
    'cond1_field', 'cond1_op', 'cond1_val',
    'cond2_field', 'cond2_op', 'cond2_val',
    'cond3_field', 'cond3_op', 'cond3_val',
    'source_fields',   # comma-separated non-case_id fields in SELECT
    'source',
]


def obda_to_csv(obda_path: str, output_path: str) -> None:
    print(f"Reading {obda_path} ...")
    content  = Path(obda_path).read_text(encoding='utf-8')
    mappings = extract_mappings(content)
    print(f"Found {len(mappings)} mappings")

    rows = []

    for mapping in mappings:
        mid    = mapping['id']
        source = mapping['source']

        subject, pairs = parse_subject_and_pairs(mapping['target'])
        (c1f, c1o, c1v), (c2f, c2o, c2v), (c3f, c3o, c3v) = parse_where_conditions(source)
        fields = ','.join(selected_fields(source))

        for pred, obj in pairs:
            rows.append({
                'mapping_id':   mid,
                'subject':      subject,
                'predicate':    pred,
                'object':       obj,
                'cond1_field':  c1f or '',
                'cond1_op':     c1o or '',
                'cond1_val':    c1v or '',
                'cond2_field':  c2f or '',
                'cond2_op':     c2o or '',
                'cond2_val':    c2v or '',
                'cond3_field':  c3f or '',
                'cond3_op':     c3o or '',
                'cond3_val':    c3v or '',
                'source_fields': fields,
                'source':       source,
            })

    print(f"Generated {len(rows)} triple-rows from {len(mappings)} mappings")
    print(f"Writing {output_path} ...")

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print("Done.")


if __name__ == '__main__':
    obda_to_csv(
        '',
        '',
    )