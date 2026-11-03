"""Import BIAI's 25 sample leads from CSV into SQLite.

Run from the backend folder:
    python -m app.csv_importer

This file ONLY imports data. Later, validation.py will flag duplicates,
missing contacts, overdue follow-ups, and incorrect date order.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from ..config import CSV_COLUMN_MAP, LEADS_CSV_PATH, parse_stage
from ..database import SessionLocal, create_tables
from ..models import ImportRow, Lead


def clean_text(value: str) -> str | None:
    """Remove extra spaces. Turn an empty cell into None (SQL NULL)."""
    text = value.strip()
    return text if text else None


def parse_date(value: str, column: str, lead_id: str):
    """Convert a CSV date like 10/28/2026 into a Python date."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError as exc:
        raise ValueError(
            f"{lead_id}: invalid {column} date {text!r}. Expected M/D/YYYY."
        ) from exc


def parse_amount(value: str, lead_id: str) -> int | None:
    """Convert a BDT amount to an integer; keep blank amounts unknown."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        amount = int(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"{lead_id}: invalid deal value {text!r}.") from exc
    if amount < 0:
        raise ValueError(f"{lead_id}: deal value cannot be negative.")
    return amount


def prepare_lead(raw_row: dict[str, str]) -> Lead:
    """Convert one original CSV row to a database Lead object."""
    # Use the shared column mapping from config.py.
    data = {
        new_name: clean_text(raw_row[old_name])
        for old_name, new_name in CSV_COLUMN_MAP.items()
    }

    lead_id = data["lead_id"]
    if not lead_id:
        raise ValueError("A CSV row is missing its lead ID.")
    if not data["company"]:
        raise ValueError(f"{lead_id}: company name is required.")
    if not data["stage"]:
        raise ValueError(f"{lead_id}: stage is required.")

    # Parse the fields that need special data types.
    data["stage"] = parse_stage(data["stage"])
    data["deal_value_bdt"] = parse_amount(
        raw_row["deal value (BDT)"], lead_id
    )
    data["last_contact"] = parse_date(
        raw_row["last contact"], "last contact", lead_id
    )
    data["next_follow_up"] = parse_date(
        raw_row["next follow-up"], "next follow-up", lead_id
    )

    # Do not correct bad date ORDER or merge duplicates here.
    # The next validation step needs to detect those original problems.
    return Lead(**data)


def import_csv(csv_path: str | Path = LEADS_CSV_PATH) -> dict[str, int]:
    """Import leads once without overwriting existing leads or archive rows.

    Returns counts so we can check what happened after each run.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")

    # Treat all cells as text first so phone numbers keep leading zeros.
    # utf-8-sig also handles the BOM at the start of BIAI's CSV.
    frame = pd.read_csv(
        path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )

    # Catch missing or unexpected columns before saving anything.
    expected = set(CSV_COLUMN_MAP)
    actual = set(frame.columns)
    if expected != actual:
        raise ValueError(
            f"CSV headers do not match. Missing: {sorted(expected - actual)}. "
            f"Unexpected: {sorted(actual - expected)}."
        )

    raw_rows = frame.to_dict(orient="records")
    prepared_leads = [prepare_lead(row) for row in raw_rows]

    # Duplicate IDs would break the primary key. Fail before writing.
    lead_ids = [lead.lead_id for lead in prepared_leads]
    if len(lead_ids) != len(set(lead_ids)):
        raise ValueError("The CSV contains repeated lead IDs.")

    create_tables()

    # A transaction makes the whole import succeed or fail together.
    with SessionLocal.begin() as db:
        # Keep original CSV rows for auditing. Do not archive them twice.
        archived = db.scalars(
            select(ImportRow)
            .where(ImportRow.source_file == path.name)
            .order_by(ImportRow.row_number)
        ).all()

        if archived:
            # Avoid quietly mixing two different CSVs with the same name.
            old_rows = [(item.row_number, item.raw_data) for item in archived]
            new_rows = list(enumerate(raw_rows, start=2))
            if old_rows != new_rows:
                raise ValueError(
                    f"{path.name} was imported before, but its contents changed. "
                    "Review the file before importing a changed version."
                )
            archived_added = 0
        else:
            for row_number, raw_row in enumerate(raw_rows, start=2):
                db.add(
                    ImportRow(
                        source_file=path.name,
                        row_number=row_number,
                        original_lead_id=clean_text(raw_row["lead id"]),
                        raw_data=raw_row,  # Keep original cell values.
                    )
                )
            archived_added = len(raw_rows)

        # Fetch existing IDs in one query instead of querying per lead.
        existing_ids = set(
            db.scalars(
                select(Lead.lead_id).where(Lead.lead_id.in_(lead_ids))
            ).all()
        )
        new_leads = [
            lead for lead in prepared_leads if lead.lead_id not in existing_ids
        ]
        db.add_all(new_leads)

    return {
        "csv_rows": len(raw_rows),
        "new_leads": len(new_leads),
        "existing_leads": len(existing_ids),
        "archived_rows_added": archived_added,
    }


if __name__ == "__main__":
    result = import_csv()
    print("CSV import complete!")
    print(f"CSV rows:             {result['csv_rows']}")
    print(f"New leads saved:      {result['new_leads']}")
    print(f"Already in database:  {result['existing_leads']}")
    print(f"Original rows saved:  {result['archived_rows_added']}")
