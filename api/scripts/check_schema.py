from collections import defaultdict

from app.common.database import Base


def check_duplicate_indexes():
    seen = defaultdict(list)
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            seen[index.name].append(table.name)

    dupes = {name: tables for name, tables in seen.items() if len(tables) > 1}
    if dupes:
        for name, tables in dupes.items():
            print(f"DUPLICATE INDEX: '{name}' appears on: {tables}")
        raise SystemExit(1)
    print("No duplicate indexes found.")


check_duplicate_indexes()
