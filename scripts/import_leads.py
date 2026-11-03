"""Import the supplied CSV file. Run this from the project root:

    python scripts/import_leads.py

You do not need to edit Python paths or database settings.
"""

from pathlib import Path
import sys

# Add backend/ to Python's import path, since this script lives in scripts/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from backend.app.services.csv_importer import import_csv


def main():
    result = import_csv()
    print("CSV import complete!")
    print(f"CSV rows:             {result['csv_rows']}")
    print(f"New leads saved:      {result['new_leads']}")
    print(f"Already in database:  {result['existing_leads']}")
    print(f"Original rows saved:  {result['archived_rows_added']}")


if __name__ == "__main__":
    main()
