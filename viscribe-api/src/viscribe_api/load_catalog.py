import argparse
import csv
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter, ValidationError

from viscribe_api.azure_clients import AzureCallError, AzureGateway
from viscribe_api.catalog import (
    VERSION_PATTERN,
    CatalogError,
    embed_release,
    import_release,
    read_catalog,
)
from viscribe_api.config import get_settings
from viscribe_api.db import make_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a versioned ViScribe ICD-10 catalog")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--embed", action="store_true", help="Generate missing Azure embeddings")
    args = parser.parse_args()
    if not VERSION_PATTERN.fullmatch(args.version):
        parser.error("Invalid catalog version identifier")
    try:
        source_url = str(TypeAdapter(HttpUrl).validate_python(args.source_url))
        rows = read_catalog(args.csv)
    except ValidationError:
        parser.error("--source-url must be an HTTP(S) URL")
    except (CatalogError, OSError, UnicodeError, csv.Error) as error:
        parser.error(str(error))
    settings = get_settings()
    sessions = make_session_factory(settings)
    try:
        created = import_release(sessions, rows, args.version, source_url)
        print(f"{args.version}: {'imported' if created else 'unchanged'}, {len(rows)} codes")
        if args.embed:
            azure = AzureGateway(settings, sessions)
            try:
                count = embed_release(sessions, rows, args.version, azure)
                print(f"{args.version}: {count} new embeddings")
            except AzureCallError as error:
                parser.exit(1, f"Embedding ended with {error.code}. Rerun to resume.\n")
            finally:
                azure.close()
    except CatalogError as error:
        parser.error(str(error))
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
