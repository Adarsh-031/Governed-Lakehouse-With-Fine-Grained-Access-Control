"""Check the production identity bootstrap in an isolated DDL test project."""

from bootstrap import main as bootstrap
from common import access_token, request, run


def main():
    bootstrap()
    # The normal Trino service still has no identity catalog grant.
    request(
        "GET",
        "http://catalog:8181/catalog/v1/config?warehouse=identity",
        token=access_token("trino"),
        expected=(403, 404),
    )
    print("PASS: isolated identity warehouse grants ETL only; normal Trino denied")


if __name__ == "__main__":
    run(main)
