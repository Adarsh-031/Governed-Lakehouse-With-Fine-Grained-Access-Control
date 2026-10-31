"""Streamlit demo for user-scoped queries through the existing Trino boundary.

This dashboard never performs authorization itself or uses a service account.
Every query is sent to Trino using the credentials entered for the selected user.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import streamlit as st
import trino
from requests.exceptions import RequestException
from trino.auth import BasicAuthentication


@dataclass(frozen=True)
class TrinoSettings:
    host: str = "localhost"
    port: int = 8443
    user: str = ""
    password: str = ""
    catalog: str = "hospital"
    schema: str = "silver"


def get_settings_from_environment() -> tuple[str, int, str, str]:
    """Read only non-secret endpoint defaults. Never read a shared service password."""
    host = os.environ.get("TRINO_HOST", "localhost")
    port = int(os.environ.get("TRINO_PORT", "8443"))
    catalog = os.environ.get("TRINO_CATALOG", "hospital")
    schema = os.environ.get("TRINO_SCHEMA", "silver")
    if not 1 <= port <= 65535:
        raise ValueError("TRINO_PORT must be between 1 and 65535")
    for label, value in (("TRINO_CATALOG", catalog), ("TRINO_SCHEMA", schema)):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"{label} must be a simple SQL identifier")
    return host, port, catalog, schema


def query_examples_for_user(user: str) -> dict[str, str]:
    """Return explicitly projected allowed examples and one expected-denial query per role."""
    identity = "SELECT current_user, current_groups()"
    denied_claims = (
        "SELECT claim_id, patient_token, payer, procedure_code, total_cost, payer_coverage "
        "FROM hospital.silver.claims LIMIT 10"
    )
    denied_patients = "SELECT patient_token, age_band, zip FROM hospital.silver.patients LIMIT 10"

    if user in {"doctor_alice", "doctor_bob"}:
        return {
            "Current identity and groups": identity,
            "My care-team patients — allowed": (
                "SELECT patient_token, birthdate, age_band, sex, race, ethnicity, zip "
                "FROM hospital.silver.patients ORDER BY patient_token LIMIT 20"
            ),
            "My care-team encounters — allowed": (
                "SELECT encounter_id, patient_token, start_time, stop_time, encounter_class, "
                "code, description, reason_code, reason_description "
                "FROM hospital.silver.encounters ORDER BY start_time DESC LIMIT 20"
            ),
            "Expected denial: claims table": denied_claims,
        }
    if user == "researcher":
        return {
            "Current identity and groups": identity,
            "Research cohort — suppressed release (cells >= 11 patients)": (
                "SELECT age_band, sex, race, patient_count "
                "FROM hospital.gold.researcher_demographics ORDER BY patient_count DESC LIMIT 20"
            ),
            "Research encounters — monthly time masks": (
                "SELECT encounter_id, patient_token, start_time, stop_time, encounter_class, "
                "code FROM hospital.silver.encounters "
                "ORDER BY start_time DESC LIMIT 20"
            ),
            "Expected denial: row-level demographics": (
                "SELECT patient_token, age_band, sex, zip FROM hospital.silver.patients LIMIT 10"
            ),
            "Expected denial: claims table": denied_claims,
        }
    if user == "billing":
        return {
            "Current identity and groups": identity,
            "Claims for reconciliation — allowed": (
                "SELECT claim_id, patient_token, encounter_id, payer, procedure_code, "
                "total_cost, payer_coverage FROM hospital.silver.claims "
                "ORDER BY claim_id LIMIT 20"
            ),
            "Expected denial: patient demographics": denied_patients,
        }
    if user == "admin":
        return {
            "Current identity and groups": identity,
            "Patients — allowed": (
                "SELECT patient_token, birthdate, age_band, sex, race, ethnicity, zip "
                "FROM hospital.silver.patients ORDER BY patient_token LIMIT 20"
            ),
            "Claims — allowed": (
                "SELECT claim_id, patient_token, encounter_id, payer, procedure_code, "
                "procedure_description, total_cost, payer_coverage "
                "FROM hospital.silver.claims ORDER BY claim_id LIMIT 20"
            ),
            "Expected denial: restricted identity catalog": (
                "SELECT patient_token, name FROM identity.identity.patient_identity LIMIT 10"
            ),
        }
    raise ValueError(f"No query examples are configured for user {user!r}")


def execute_query(settings: TrinoSettings, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute as the user-provided identity over HTTPS with certificate validation."""
    if not settings.user.strip():
        raise ValueError("Enter your own Trino username")
    if not settings.password:
        raise ValueError("Enter your own Trino password")
    if not sql.strip():
        raise ValueError("Enter a read-only query")

    # A usability guardrail only; Trino remains the security boundary.
    if not re.match(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN)\b", sql, re.IGNORECASE):
        raise ValueError("The demo accepts read-only SQL statements only")

    connection = trino.dbapi.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user.strip(),
        auth=BasicAuthentication(settings.user.strip(), settings.password),
        http_scheme="https",
        verify=os.environ.get("TRINO_CA_BUNDLE", "data/secrets/ca.crt"),
        catalog=settings.catalog,
        schema=settings.schema,
        request_timeout=60,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description] if cursor.description else []
        return columns, rows
    finally:
        connection.close()


def trino_user_error_message(exc: trino.exceptions.TrinoUserError) -> str:
    """Preserve the server-provided message for user/query errors."""
    return exc.message


def main() -> None:
    st.set_page_config(page_title="Governed Lakehouse Demo", page_icon="🔐", layout="wide")
    st.title("Governed Hospital Lakehouse")
    st.caption(
        "Optional demonstration UI. Trino enforces access; this dashboard never grants permissions."
    )

    try:
        host, port, catalog, schema = get_settings_from_environment()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.header("Your Trino login")
        st.warning(
            "Use your own Trino account. No shared or elevated service-account credentials are used."
        )
        user = st.selectbox(
            "Username",
            ["doctor_alice", "doctor_bob", "researcher", "billing", "admin"],
            index=0,
        )
        # The per-user key avoids reusing a previous account's password after switching users.
        password = st.text_input(
            "Your Trino password", type="password", key=f"trino_password_{user}"
        )
        st.caption(f"HTTPS endpoint: {host}:{port}")
        st.caption(f"SQL catalog/schema: {catalog}.{schema}")

    examples = query_examples_for_user(user)
    selected = st.selectbox("Example query", list(examples), key=f"example_query_{user}")
    sql = st.text_area(
        "SQL query", value=examples[selected], height=140, key=f"sql_query_{user}_{selected}"
    )
    st.caption(
        "Expected-denial examples should fail at Trino. Row filters, masks, table grants, and "
        "column permissions are enforced by Trino—not by this UI."
    )

    if st.button("Run query", type="primary"):
        settings = TrinoSettings(
            host=host,
            port=port,
            user=user,
            password=password,
            catalog=catalog,
            schema=schema,
        )
        try:
            with st.spinner("Running query through Trino…"):
                columns, rows = execute_query(settings, sql)
            if selected.startswith("Expected denial:"):
                st.warning(
                    "This example was expected to be denied but succeeded. Review server-side policy."
                )
            else:
                st.success(f"Query completed as {user}. Rows returned: {len(rows)}")
            if columns:
                st.dataframe(
                    {column: [row[index] for row in rows] for index, column in enumerate(columns)},
                    use_container_width=True,
                )
            else:
                st.info("The statement returned no tabular result.")
        except trino.exceptions.TrinoUserError as exc:
            if (
                selected.startswith("Expected denial:")
                and "access denied" in exc.message.casefold()
            ):
                st.success("Trino denied this query as expected.")
            st.error(trino_user_error_message(exc))
        except (
            ValueError,
            OSError,
            trino.exceptions.TrinoException,
            RequestException,
        ) as exc:
            # Avoid surfacing transport internals, TLS details, or credentials in the UI.
            st.error(f"Unable to query Trino ({type(exc).__name__}).")
            st.caption("Check your credentials, CA trust, connection, and server availability.")


if __name__ == "__main__":
    main()
