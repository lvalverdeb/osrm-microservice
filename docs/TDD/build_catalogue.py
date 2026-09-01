#!/usr/bin/env python3
"""Normalise CAT-VRP-003 entries and emit machine-readable extracts.

Reads the authored markdown, rewrites each scenario block into a uniform,
self-contained, anchored form, and emits scenarios.jsonl + a validation report.
Run: python3 build_catalogue.py <src.md> <out.md> <out.jsonl>
"""
import json
import re
import sys
from collections import Counter, OrderedDict

# `PATHOLOGICAL` is not a routing variant. It marks the adversarial instances of
# §11, which are entries so that queries and coverage gates can see them, and are
# kept out of the §2 variant audit because "is CVRP coverage adequate" is not a
# question their count answers. Filter on it to get the operational set.
VARIANTS = {"TSP","CVRP","VRPTW","MDHVRPTW","PDPTW","DARP","IRP","CARP","LRP",
            "PATHOLOGICAL"}

# Controlled vocabulary. The bracket tag in the authored markdown is free text;
# these maps pin it to the closed set above so the extract is filterable.
ALIAS = {"TSP-ish": "TSP", "Dial-a-ride": "DARP", "Dynamic PDPTW": "PDPTW",
         "dynamic PDPTW": "PDPTW", "CARP-adjacent": "CARP"}
# Entries whose bracket names a mode rather than a variant: pin the variant explicitly.
OVERRIDE = {"UC-031": ("PDPTW", ["dynamic"]),
            "UC-032": ("VRPTW", ["dynamic", "re-optimisation"]),
            "UC-033": ("VRPTW", ["dynamic", "insertion"]),
            "UC-034": ("VRPTW", ["dynamic", "re-optimisation"]),
            "UC-035": ("VRPTW", ["dynamic", "retry"]),
            "UC-044": ("VRPTW", ["dynamic", "preemptive"]),
            "UC-088": ("TSP", ["arc-routing-boundary"]),
            "UC-171": ("MDHVRPTW", ["dynamic", "re-planning"]),
            "UC-172": ("VRPTW", ["dynamic", "network-change"])}


def normalise_tags(uc_id, bracket):
    """Split a free-text bracket into (canonical_variant, tags)."""
    if uc_id in OVERRIDE:
        return OVERRIDE[uc_id]
    parts = [t.strip() for t in re.split(r'[,+]| with ', bracket) if t.strip()]
    head = ALIAS.get(parts[0], parts[0])
    tags = [t.lower().replace(" ", "-").replace("&", "and") for t in parts[1:]]
    return head, tags
TIERS = {"P0","P1","P2"}
STATUS = {"MODELLED","PARTIALLY_MODELLED","NOT_MODELLED"}

HDR = re.compile(r'^\*\*`(UC-\d{3})` (.+?) — (P[012])\*\* `\[(.+?)\]`\s*$', re.MULTILINE)
SECTION = re.compile(r'^(#{2,3}) (.+)$', re.MULTILINE)


def parse(src):
    """Return (records, section_index). Records preserve document order."""
    sections = [(m.start(), m.group(2).strip()) for m in SECTION.finditer(src)]

    def section_for(pos):
        cur = "(none)"
        for start, title in sections:
            if start < pos:
                cur = title
            else:
                break
        return cur

    records, spans = [], []
    matches = list(HDR.finditer(src))
    heading_starts = [s for s, _ in sections]
    for i, m in enumerate(matches):
        # An entry ends at the next entry OR the next heading, whichever comes
        # first. Without the heading bound the final entry swallows the tail of
        # the document.
        bounds = [matches[i + 1].start()] if i + 1 < len(matches) else []
        bounds += [h for h in heading_starts if h > m.end()]
        end = min(bounds) if bounds else len(src)
        body = src[m.end():end]
        # description = text before the first bullet
        desc = body.split("\n- ", 1)[0].strip()
        fields = OrderedDict()
        for fm in re.finditer(r'^- (Binds|Exercises|Breaks|Status): (.+?)(?=\n- |\n\n|\Z)',
                              body, re.MULTILINE | re.DOTALL):
            fields[fm.group(1)] = " ".join(fm.group(2).split())
        variant, tags = normalise_tags(m.group(1), m.group(4))
        # The lookbehind is load-bearing: without it `NFR-04` matches as `FR-04`
        # and an entry citing a non-functional requirement silently acquires a
        # functional one it never mentioned.
        reqs = sorted(set(re.findall(r'(?<![A-Za-z])FR-(?:P?\d+)',
                                     fields.get("Exercises", ""))))
        records.append(OrderedDict(
            id=m.group(1),
            name=m.group(2).strip(),
            tier=m.group(3),
            variant=variant,
            tags=tags,
            section=section_for(m.start()),
            description=" ".join(desc.split()),
            binds=fields.get("Binds", ""),
            exercises_raw=fields.get("Exercises", ""),
            requirements=reqs,
            breaks=fields.get("Breaks", ""),
            status=fields.get("Status", "MODELLED").split(" — ")[0],
            status_note=(fields.get("Status", "").split(" — ", 1) + [""])[1],
        ))
        spans.append((m.start(), end))
    return records, spans


def render(rec):
    """Uniform, self-contained block. Every field labelled; no cross-references
    required to understand the entry in isolation."""
    L = [f"#### {rec['id']} — {rec['name']}", ""]
    L.append(f"{rec['description']}")
    L.append("")
    L.append(f"- **Variant:** {rec['variant']}")
    L.append(f"- **Tier:** {rec['tier']}")
    if rec["tags"]:
        L.append(f"- **Tags:** {', '.join(rec['tags'])}")
    L.append(f"- **Binds:** {rec['binds']}")
    L.append(f"- **Exercises:** {rec['exercises_raw']}")
    L.append(f"- **Breaks:** {rec['breaks']}")
    if rec["status"] != "MODELLED":
        note = f" — {rec['status_note']}" if rec["status_note"] else ""
        L.append(f"- **Status:** {rec['status']}{note}")
    L.append("")
    return "\n".join(L)


def validate(records, src):
    errs, warns = [], []
    ids = [r["id"] for r in records]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    if dupes:
        errs.append(f"duplicate ids: {dupes}")
    for r in records:
        if r["variant"] not in VARIANTS:
            errs.append(f"{r['id']}: unknown variant {r['variant']!r}")
        if r["tier"] not in TIERS:
            errs.append(f"{r['id']}: unknown tier {r['tier']!r}")
        if r["status"] not in STATUS:
            errs.append(f"{r['id']}: unknown status {r['status']!r}")
        for f in ("description", "binds", "breaks"):
            if len(r[f]) < 20:
                errs.append(f"{r['id']}: field {f!r} too short to stand alone")
        # self-containment: an entry must not depend on a neighbour to be understood
        if re.search(r'\bas above\b|\bsame\b\.|\bditto\b', r["breaks"], re.IGNORECASE):
            errs.append(f"{r['id']}: breaks field is not self-contained")
    # referenced-but-undefined ids
    defined = set(ids) | set(re.findall(r'\| `(UC-\d{3})` \|', src))
    for ref in sorted(set(re.findall(r'UC-\d{3}', src))):
        if ref not in defined:
            errs.append(f"{ref} referenced but never defined")
    if re.search(r'FR-\d+-style', src):
        errs.append("dangling pseudo-requirement reference (FR-nn-style)")
    return errs, warns


def main():
    src_path, out_md, out_jsonl = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(src_path) as handle:
        src = handle.read()
    records, spans = parse(src)

    # rewrite entry blocks in place, back to front so offsets stay valid
    out = src
    for rec, (a, b) in reversed(list(zip(records, spans))):
        out = out[:a] + render(rec) + out[b:]

    # fill generated blocks
    import re as _re
    gens = {"coverage": gen_coverage(records),
            "variant_index": gen_variant_index(records)}
    for name, block in gens.items():
        pat = _re.compile(r'<!-- BEGIN:GENERATED ' + name + r' -->.*?<!-- END:GENERATED -->',
                          _re.DOTALL)
        if not pat.search(out):
            raise SystemExit(f"missing sentinel for generated block {name!r}")
        out = pat.sub(f"<!-- BEGIN:GENERATED {name} -->\n{block}<!-- END:GENERATED -->", out)

    # appendix index + front matter
    out = out.replace("## 14. References",
                      "## 14. Scenario index\n\n"
                      "Complete lookup table. Generated; do not edit by hand.\n\n"
                      "<!-- BEGIN:GENERATED full_index -->\n" + gen_full_index(records) +
                      "<!-- END:GENERATED -->\n\n---\n\n## 15. References")
    out = gen_front_matter(records) + out

    errs, _warns = validate(records, out)

    counts = Counter(r["variant"] for r in records)
    tiers = Counter(r["tier"] for r in records)
    print(f"scenarios: {len(records)}")
    print("by variant:", dict(counts))
    print("by tier:", dict(tiers))
    print("errors:", errs or "none")

    with open(out_jsonl, "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    with open(out_md, "w") as handle:
        handle.write(out)
    return 1 if errs else 0




# ---------------------------------------------------------------- generated
def gen_coverage(records):
    from collections import Counter, defaultdict
    prim = Counter(r["variant"] for r in records)
    sect = defaultdict(set)
    for r in records:
        sect[r["variant"]].add(r["section"].split("—")[0].strip())
    order = ["TSP", "CVRP", "VRPTW", "MDHVRPTW", "PDPTW", "DARP", "IRP", "CARP", "LRP"]
    rows = ["| Variant | Primary scenarios | Tiers (P0/P1/P2) | Status |",
            "|---|---|---|---|"]
    for v in order:
        rs = [r for r in records if r["variant"] == v]
        if not rs:
            continue
        t = Counter(r["tier"] for r in rs)
        status = "adequate" if len(rs) >= 10 else (
            "deliberately partial" if v in ("IRP", "CARP", "LRP") else "thin")
        rows.append(f"| **{v}** | {prim[v]} | {t['P0']} / {t['P1']} / {t['P2']} | {status} |")
    adversarial = [r for r in records if r["variant"] == "PATHOLOGICAL"]
    operational = len(records) - len(adversarial)
    rows.append(f"| *(adversarial, §11)* | {len(adversarial)} | — | n/a |")
    rows.append("")
    rows.append(f"Total scenarios: **{operational} operational + {len(adversarial)} "
                f"adversarial = {len(records)}**. Counts in this table are generated from "
                f"the entries by `build_catalogue.py`; do not edit by hand.")
    rows.append("")
    return "\n".join(rows)


def gen_variant_index(records):
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        by[r["variant"]].append(r["id"].replace("UC-", ""))
    order = ["TSP", "CVRP", "VRPTW", "MDHVRPTW", "PDPTW", "DARP", "IRP", "CARP", "LRP",
             "PATHOLOGICAL"]
    rows = ["| Variant | Scenario ids |", "|---|---|"]
    for v in order:
        if v in by:
            rows.append(f"| {v} | {', '.join(sorted(by[v]))} |")
    rows.append("")
    return "\n".join(rows)


def gen_full_index(records):
    rows = ["| ID | Name | Variant | Tier | Tags |", "|---|---|---|---|---|"]
    for r in sorted(records, key=lambda x: x["id"]):
        rows.append(f"| `{r['id']}` | {r['name']} | {r['variant']} | {r['tier']} | "
                    f"{', '.join(r['tags']) or '—'} |")
    rows.append("")
    return "\n".join(rows)


def gen_front_matter(records):
    from collections import Counter
    c = Counter(r["variant"] for r in records)
    t = Counter(r["tier"] for r in records)
    adversarial = c["PATHOLOGICAL"]
    return f"""---
document_id: CAT-VRP-003
title: Real-World Problem Catalogue for Vehicle Routing
version: 2.1
companion_documents: [SDD-VRP-001, SDD-VRP-UI-002]
machine_readable_extract: scenarios.jsonl
build_script: build_catalogue.py
source_document: vrp-catalogue-v2.1.src.md
entry_schema:
  id: string, stable, pattern UC-nnn
  name: string
  variant: enum {sorted(VARIANTS)}
  tier: enum [P0, P1, P2]
  tags: list of string
  section: string
  description: string
  binds: string
  exercises_raw: string
  requirements: list of requirement ids (FR-nn or FR-Pnn)
  breaks: string
  status: enum [MODELLED, PARTIALLY_MODELLED, NOT_MODELLED]
counts:
  operational_scenarios: {len(records) - adversarial}
  adversarial_instances: {adversarial}
  by_variant: {{{', '.join(f'{k}: {v}' for k, v in sorted(c.items()))}}}
  by_tier: {{P0: {t['P0']}, P1: {t['P1']}, P2: {t['P2']}}}
---

"""


if __name__ == "__main__":
    sys.exit(main())
