"""Find possible duplicate company leads without deleting any records.

Run from backend/:
    python -m app.services.duplicates

The scanner:
1. Finds possible duplicate pairs using company and contact information.
2. Saves one DUPLICATE_LEAD warning for each matching lead.
3. Does not merge or change the original leads. A person reviews matches later.

This module reuses the Lead and LeadIssue models from Step 3.
"""

from collections import defaultdict
from difflib import SequenceMatcher
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal, create_tables
from ..models import Lead, LeadIssue


RULE_CODE = "DUPLICATE_LEAD"


def normalize_company(value: str | None) -> str:
    """Make company names easier to compare.

    Example: 'Padma Textiles Ltd.' -> 'padma textiles'.
    """
    words = re.findall(r"[a-z0-9]+", (value or "").casefold())
    # 'Ltd' and 'Limited' are legal suffixes, not meaningful differences.
    while words and words[-1] in {"ltd", "limited"}:
        words.pop()
    return " ".join(words)


def normalize_email(value: str | None) -> str:
    """Ignore whitespace and capitalization in emails."""
    return (value or "").strip().casefold()


def normalize_phone(value: str | None) -> str:
    """Compare digits only so spaces and hyphens do not matter."""
    return "".join(char for char in (value or "") if char.isdigit())


def normalize_person(value: str | None) -> str:
    """Ignore periods and extra spaces in contact names."""
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def compare_leads(first: Lead, second: Lead) -> dict | None:
    """Return a reason when two leads look like duplicates, otherwise None.

    Contact matches help avoid guessing based only on fuzzy company names.
    Results are suggestions for review, never automatic merges.
    """
    company_a = normalize_company(first.company)
    company_b = normalize_company(second.company)
    email_a, email_b = normalize_email(first.contact_email), normalize_email(second.contact_email)
    phone_a, phone_b = normalize_phone(first.contact_phone), normalize_phone(second.contact_phone)
    person_a, person_b = normalize_person(first.contact_name), normalize_person(second.contact_name)

    same_company = bool(company_a and company_a == company_b)
    same_email = bool(email_a and email_a == email_b)
    same_phone = bool(phone_a and phone_a == phone_b)
    same_person = bool(person_a and person_a == person_b)

    # Require strong contact evidence for differently named companies.
    if same_company and (same_email or same_phone or same_person):
        match_type = "same_company_and_contact"
    elif same_email and same_phone and same_person:
        match_type = "same_contact_different_company_name"
    elif same_company:
        # A company can have multiple genuine deals, so only suggest review.
        match_type = "same_company_review"
    else:
        similarity = SequenceMatcher(None, company_a, company_b).ratio()
        if not (similarity >= 0.75 and same_person and (same_email or same_phone)):
            return None
        match_type = "similar_company_and_contact"

    matched_fields = [
        name for name, matched in (
            ("company", same_company),
            ("contact_email", same_email),
            ("contact_phone", same_phone),
            ("contact_name", same_person),
        ) if matched
    ]
    return {"match_type": match_type, "matched_fields": matched_fields}


def find_duplicate_pairs(db: Session) -> list[dict]:
    """Find duplicate candidates among active, unmerged records.

    To reduce comparisons, first group leads by shared company, email,
    or phone. Leads with no shared information are not compared.
    """
    leads = db.scalars(
        select(Lead).where(Lead.merged_into_id.is_(None)).order_by(Lead.lead_id)
    ).all()

    # A value in these groups gives us a reason to compare two leads.
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_id = {lead.lead_id: lead for lead in leads}

    for lead in leads:
        for field, value in (
            ("company", normalize_company(lead.company)),
            ("email", normalize_email(lead.contact_email)),
            ("phone", normalize_phone(lead.contact_phone)),
        ):
            if value:  # Never group empty emails or phone numbers.
                groups[(field, value)].append(lead.lead_id)

    # A pair may share both an email and phone. A set avoids double counting.
    candidate_pairs: set[tuple[str, str]] = set()
    for lead_ids in groups.values():
        for index, first_id in enumerate(lead_ids):
            for second_id in lead_ids[index + 1:]:
                candidate_pairs.add(tuple(sorted((first_id, second_id))))

    matches = []
    for first_id, second_id in sorted(candidate_pairs):
        details = compare_leads(by_id[first_id], by_id[second_id])
        if details:
            matches.append({
                "lead_id_1": first_id,
                "company_1": by_id[first_id].company,
                "lead_id_2": second_id,
                "company_2": by_id[second_id].company,
                **details,
            })
    return matches


def run_duplicate_scan(db: Session) -> dict:
    """Store duplicate warnings; caller commits the database transaction.

    - One warning per matched lead, even if it matches multiple records.
    - Scanning twice will not insert duplicate warnings.
    - Warnings disappear from the open list after a reviewed merge.
    - A dismissed warning stays dismissed if the evidence has not changed.
    """
    matches = find_duplicate_pairs(db)
    # Save matching leads on both sides so users can inspect either record.
    found: dict[str, list[dict]] = defaultdict(list)
    for match in matches:
        for lead_id, other_id, other_company in (
            (match["lead_id_1"], match["lead_id_2"], match["company_2"]),
            (match["lead_id_2"], match["lead_id_1"], match["company_1"]),
        ):
            found[lead_id].append({
                "lead_id": other_id,
                "company": other_company,
                "match_type": match["match_type"],
                "matched_fields": match["matched_fields"],
            })

    old_issues = db.scalars(
        select(LeadIssue).where(LeadIssue.rule_code == RULE_CODE)
    ).all()
    by_lead_id = {issue.lead_id: issue for issue in old_issues}

    created = 0
    resolved = 0
    for lead_id, related_leads in sorted(found.items()):
        evidence = {"matches": sorted(related_leads, key=lambda item: item["lead_id"])}
        message = f"Possible duplicate of {', '.join(item['lead_id'] for item in related_leads)}. Review before merging."
        old = by_lead_id.get(lead_id)
        if old is None:
            db.add(LeadIssue(
                lead_id=lead_id,
                rule_code=RULE_CODE,
                message=message,
                severity="high",
                status="open",
                evidence=evidence,
            ))
            created += 1
        elif not (old.status == "dismissed" and old.evidence == evidence):
            old.message = message
            old.severity = "high"
            old.status = "open"
            old.evidence = evidence

    for lead_id, old in by_lead_id.items():
        if lead_id not in found and old.status == "open":
            old.status = "resolved"
            resolved += 1

    return {
        "pairs_found": len(matches),
        "leads_flagged": len(found),
        "new_issues_saved": created,
        "old_issues_resolved": resolved,
        "pairs": matches,
    }


if __name__ == "__main__":
    create_tables()
    with SessionLocal.begin() as session:
        result = run_duplicate_scan(session)

    print(f"Duplicate pairs found: {result['pairs_found']}")
    print(f"Leads flagged:         {result['leads_flagged']}")
    print(f"New warnings saved:    {result['new_issues_saved']}")
    print(f"Old warnings resolved: {result['old_issues_resolved']}")
    for pair in result["pairs"]:
        print(f"  {pair['lead_id_1']} + {pair['lead_id_2']} ({pair['match_type']})")
    if not result["pairs"]:
        print("If no leads are in the database, import the CSV first.")
