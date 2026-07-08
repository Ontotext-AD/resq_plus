import argparse
import csv
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg2
from dotenv import load_dotenv
from rdflib import BNode, Graph, Literal, URIRef
from SPARQLWrapper import SPARQLWrapper

load_dotenv()

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

GRAPHDB_URL   = os.getenv("GRAPHDB_BASE_URL", "")
VIRTUAL_REPO  = os.getenv("VIRTUAL_REPO_ID",  "")
MATERIAL_REPO = os.getenv("MATERIAL_REPO_ID", "RESQMat")

# The base URI for case nodes — e.g. data:Case_44 expands to this + "Case_44"
DATA_NS = "http://resqplus-resources/ontologies/resqplus-data#"

# How many cases to process before logging a progress update
PROGRESS_EVERY = 50

# Set from --normalize-literals; see canonical_literal()
NORMALIZE_LITERALS = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Discrepancy record — one row of the report
# ---------------------------------------------------------------------------

@dataclass
class Discrepancy:
    case_id: int
    category: str            # MATERIALIZED_ONLY | VIRTUAL_ONLY | VALUE_MISMATCH
    subject: str
    predicate: str
    virtual_value: str       # "" when the field is absent from virtual
    materialized_value: str  # "" when the field is absent from materialized


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str):
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)          # progress + summaries only
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)    # everything, incl. per-field detail
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Step 1: Get all case IDs from the database
# ---------------------------------------------------------------------------

def get_all_case_ids(start_offset: int = 0) -> list[int]:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT case_id
                FROM public.strokehealthcaremodel_inpatientcase
                ORDER BY case_id
                OFFSET %s
            """, (start_offset,))
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 2: Fetch all RDF triples for a case from a GraphDB repository
# ---------------------------------------------------------------------------

def get_triples_for_case(repo_id: str, case_id: int) -> set[tuple]:
    """
    Fetch a case's whole subgraph with  (!rdf:type)* : follow any predicate
    except rdf:type (so we never step onto shared class nodes and drag in other
    cases), then grab every outgoing triple of each reached node.
    """
    case_uri = f"{DATA_NS}Case_{case_id}"
    endpoint = f"{GRAPHDB_URL}/repositories/{repo_id}"

    query = f"""
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        CONSTRUCT {{ ?s ?p ?o }}
        WHERE {{
            <{case_uri}> (!rdf:type)* ?s .
            ?s ?p ?o .
        }}
    """

    sparql = SPARQLWrapper(endpoint)
    sparql.setQuery(query)
    sparql.setReturnFormat("turtle")

    raw_result = None
    for attempt in range(3):
        try:
            raw_result = sparql.queryAndConvert()
            break
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(
                    f"Failed to query {repo_id} for case {case_id} "
                    f"after 3 attempts: {e}"
                )
            logger.warning(
                "Query attempt %d for case %d failed, retrying in %ds...",
                attempt + 1, case_id, 2 ** attempt,
            )
            time.sleep(2 ** attempt)

    g = Graph()
    if isinstance(raw_result, bytes):
        g.parse(data=raw_result, format="turtle")
    else:
        g.parse(data=str(raw_result), format="turtle")

    triples = set()
    for subject, predicate, obj in g:
        triples.add((
            node_to_string(subject, g),
            str(predicate),
            node_to_string(obj, g),
        ))
    return triples


def canonical_literal(node: Literal) -> str:
    """
    Normalize a literal so equivalent lexical forms compare equal:
      "5"^^xsd:int == "5"^^xsd:integer,  2.50 == 2.5,  "true" == "1"(bool).
    Falls back to exact (lexical + datatype) form for datatypes rdflib can't
    interpret. Only used when NORMALIZE_LITERALS is on.
    """
    py = node.value  # rdflib's Python-typed value, or None for unknown dtypes
    if py is None:
        return f'"{node}"^^{node.datatype}' if node.datatype else f'"{node}"'
    if isinstance(py, bool):                       # check before int
        return f"bool:{str(py).lower()}"
    if isinstance(py, int):
        return f"num:{py}"
    if isinstance(py, Decimal):
        return f"num:{py.normalize()}"
    if isinstance(py, float):
        return f"num:{Decimal(str(py)).normalize()}"
    return f"val:{py}"                              # dates, etc.


def node_to_string(node, graph: Graph) -> str:
    """Convert an RDF node to a stable string for comparison."""
    if isinstance(node, URIRef):
        return str(node)

    if isinstance(node, Literal):
        if NORMALIZE_LITERALS:
            return canonical_literal(node)
        if node.datatype:
            return f'"{node}"^^{node.datatype}'
        return f'"{node}"'

    if isinstance(node, BNode):
        # No stable identity across repos — fingerprint by outgoing structure.
        outgoing = sorted(
            (str(p), node_to_string(o, graph))
            for _, p, o in graph.triples((node, None, None))
        )
        return f"_:BNODE{outgoing}"

    return str(node)


# ---------------------------------------------------------------------------
# Step 3: Compare the two repos for a case, at the field level
# ---------------------------------------------------------------------------

def build_field_map(triples: set[tuple]) -> dict[tuple, set]:
    """
    Group triples into fields: {(subject, predicate): {object, object, ...}}.
    A predicate can be multi-valued, hence a set of objects per field.
    """
    fields: dict[tuple, set] = {}
    for s, p, o in triples:
        fields.setdefault((s, p), set()).add(o)
    return fields


EMPTY_MARKER = "-"  # shown when a side has no (unique) values, so empty is visibly intentional


def _fmt_values(values: set) -> str:
    """Render a value-set for the report (sorted, joined). Empty -> '-'."""
    return " | ".join(sorted(values)) if values else EMPTY_MARKER


def compare_case(case_id: int) -> list[Discrepancy]:
    """
    Compare virtual vs materialized for one case and return the discrepancies.
    Raises on an unrecoverable query error — the caller logs and continues.
    """
    virtual      = build_field_map(get_triples_for_case(VIRTUAL_REPO,  case_id))
    materialized = build_field_map(get_triples_for_case(MATERIAL_REPO, case_id))

    v_keys = set(virtual)
    m_keys = set(materialized)
    out: list[Discrepancy] = []

    # Category 1: field in materialized, not in virtual
    for (s, p) in sorted(m_keys - v_keys):
        out.append(Discrepancy(
            case_id, "MATERIALIZED_ONLY", s, p,
            virtual_value=EMPTY_MARKER, materialized_value=_fmt_values(materialized[(s, p)]),
        ))

    # Category 2: field in virtual, not in materialized
    for (s, p) in sorted(v_keys - m_keys):
        out.append(Discrepancy(
            case_id, "VIRTUAL_ONLY", s, p,
            virtual_value=_fmt_values(virtual[(s, p)]), materialized_value=EMPTY_MARKER,
        ))

    # Category 3: field in both, different values.
    # For multi-valued fields we report only the DELTA — the objects unique to
    # each side — so the row pinpoints what differs instead of dumping both
    # full value-sets. Objects both repos agree on are dropped.
    for (s, p) in sorted(v_keys & m_keys):
        v_vals = virtual[(s, p)]
        m_vals = materialized[(s, p)]
        if v_vals != m_vals:
            out.append(Discrepancy(
                case_id, "VALUE_MISMATCH", s, p,
                virtual_value=_fmt_values(v_vals - m_vals),       # only in virtual
                materialized_value=_fmt_values(m_vals - v_vals),  # only in materialized
            ))

    return out


# ---------------------------------------------------------------------------
# Step 4: Main loop — check every case, never halt
# ---------------------------------------------------------------------------

CSV_HEADER = [
    "case_id", "category", "subject", "predicate",
    "virtual_value", "materialized_value",
]


def run(start_offset: int, report_file: str, log_file: str):
    logger.info("Virtual repo:      %s/repositories/%s", GRAPHDB_URL, VIRTUAL_REPO)
    logger.info("Materialized repo: %s/repositories/%s", GRAPHDB_URL, MATERIAL_REPO)
    logger.info("Report (CSV):      %s", report_file)
    logger.info("Log file:          %s", log_file)
    logger.info("Literal normalization: %s", "ON" if NORMALIZE_LITERALS else "OFF")

    logger.info("Loading case IDs from database...")
    case_ids = get_all_case_ids(start_offset=start_offset)
    logger.info("Found %d cases to check", len(case_ids))

    counts = {"MATERIALIZED_ONLY": 0, "VIRTUAL_ONLY": 0, "VALUE_MISMATCH": 0}
    mismatched_cases = 0
    error_cases = 0
    start_time = time.time()

    # Stream discrepancies to CSV as we go, so a crash keeps partial results.
    with open(report_file, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)

        for i, case_id in enumerate(case_ids, start=1):
            try:
                discrepancies = compare_case(case_id)
            except Exception as e:
                error_cases += 1
                logger.error("Case %d: could not compare — %s", case_id, e)
                continue

            if discrepancies:
                mismatched_cases += 1
                per_case = {"MATERIALIZED_ONLY": 0, "VIRTUAL_ONLY": 0, "VALUE_MISMATCH": 0}
                for d in discrepancies:
                    writer.writerow([
                        d.case_id, d.category, d.subject, d.predicate,
                        d.virtual_value, d.materialized_value,
                    ])
                    counts[d.category] += 1
                    per_case[d.category] += 1
                    logger.debug(
                        "    [%d] %-17s %s  %s  (virt=%r | mat=%r)",
                        d.case_id, d.category, d.subject, d.predicate,
                        d.virtual_value, d.materialized_value,
                    )
                fh.flush()
                logger.info(
                    "Case %d: %d discrepancies — %d mat-only, %d virt-only, %d value-mismatch",
                    case_id, len(discrepancies),
                    per_case["MATERIALIZED_ONLY"], per_case["VIRTUAL_ONLY"],
                    per_case["VALUE_MISMATCH"],
                )
            else:
                logger.debug("Case %d: OK", case_id)

            if i % PROGRESS_EVERY == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed else 0
                logger.info(
                    "Progress: %d/%d cases | %d mismatched | %d errors | %.1f cases/s",
                    i, len(case_ids), mismatched_cases, error_cases, rate,
                )

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("DONE")
    logger.info("  Cases checked:                     %d", len(case_ids))
    logger.info("  Cases with discrepancies:          %d", mismatched_cases)
    logger.info("  Cases with errors:                 %d", error_cases)
    logger.info("  (1) Fields only in materialized:   %d", counts["MATERIALIZED_ONLY"])
    logger.info("  (2) Fields only in virtual:        %d", counts["VIRTUAL_ONLY"])
    logger.info("  (3) Fields with different values:  %d", counts["VALUE_MISMATCH"])
    logger.info("  Time elapsed:                      %.1fs", elapsed)
    logger.info("  Report: %s", report_file)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect field-level discrepancies between virtualized and "
                    "materialized RDF data in GraphDB (never halts)."
    )
    parser.add_argument("--report-file", type=str, default=None,
                        help="CSV output path (default: dated file in cwd)")
    parser.add_argument("--log-file", type=str, default=None,
                        help="Log output path (default: dated file in cwd)")
    parser.add_argument("--start-offset", type=int, default=0,
                        help="Skip the first N cases (useful for resuming)")
    parser.add_argument("--normalize-literals", action="store_true",
                        help="Collapse equivalent literal forms before comparing")
    args = parser.parse_args()

    NORMALIZE_LITERALS = args.normalize_literals

    stamp       = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = args.report_file or f"repo_discrepancies_{stamp}.csv"
    log_file    = args.log_file    or f"repo_comparison_{stamp}.log"

    setup_logging(log_file)

    try:
        run(start_offset=args.start_offset, report_file=report_file, log_file=log_file)
    except Exception as e:
        logger.critical("Fatal error before/around the case loop: %s", e)
        sys.exit(1)