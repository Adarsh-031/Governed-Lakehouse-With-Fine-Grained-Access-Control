"""Prove matrix behavior with separately authenticated, direct Trino clients."""

import pytest

from .oracle import CASES, TABLES, USERS, assert_rows, decisions

USER_TABLES = [(user, table) for user in USERS for table in TABLES]
DOCTOR_TABLES = [
    (user, table) for user in ("doctor_alice", "doctor_bob") for table in ("patients", "encounters")
]


@pytest.mark.parametrize("user", USERS)
def test_authenticated_identity_and_server_group(access, user):
    assert access.execute("SELECT current_user, current_groups()", user) == [[user, [USERS[user]]]]


@pytest.mark.parametrize(
    "user,row", CASES, ids=[f"{user}/{row['table']}/{row['column']}" for user, row in CASES]
)
def test_every_matrix_cell_against_independent_values(access, user, row):
    table, column = row["table"], row["column"]
    resource = TABLES[table]["resource"]
    denied = row["operations"] == "NONE" or row["visibility"] == "hidden"
    actual = access.execute(f"SELECT {column} FROM {resource}", user, denied=denied)
    if denied:
        return
    assert_rows(actual, access.oracle.rows(user, table, [column]), table, [column])
    # arbitrary() keeps one row for an empty #46 release (all cells suppressed).
    assert access.execute(f"SELECT typeof(arbitrary({column})) FROM {resource}", user) == [
        [TABLES[table]["columns"][column].lower()]
    ]


@pytest.mark.parametrize("user,table", USER_TABLES)
def test_table_projection_star_and_metadata(access, user, table):
    contract = decisions(user, table)
    resource = TABLES[table]["resource"]
    if contract[0]["operations"] == "NONE":
        access.execute(f"SELECT count(*) FROM {resource}", user, denied=True)
        return
    columns = [row["column"] for row in contract if row["visibility"] != "hidden"]
    actual = access.execute(f"SELECT {', '.join(columns)} FROM {resource}", user)
    assert_rows(actual, access.oracle.rows(user, table, columns), table, columns)
    assert [row[0] for row in access.execute(f"SHOW COLUMNS FROM {resource}", user)] == columns
    hidden = any(row["visibility"] == "hidden" for row in contract)
    stars = access.execute(f"SELECT * FROM {resource}", user, denied=hidden)
    if not hidden:
        assert_rows(stars, access.oracle.rows(user, table, columns), table, columns)


@pytest.mark.parametrize("user,table", DOCTOR_TABLES)
def test_doctor_tokens_equal_saved_oracle_with_zero_unauthorized_rows(access, user, table):
    actual = access.execute(f"SELECT patient_token FROM hospital.silver.{table}", user)
    expected = access.oracle.tokens(user, table)
    observed = {row[0] for row in actual}
    unauthorized_rows = sum(row[0] not in expected for row in actual)
    missing = len(expected - observed)
    access.doctor_results[f"{user}/{table}"] = {
        "expected_tokens": len(expected),
        "returned_tokens": len(observed),
        "returned_rows": len(actual),
        "unauthorized_rows": unauthorized_rows,
        "missing_tokens": missing,
    }
    assert unauthorized_rows == 0, f"{user}/{table}: {unauthorized_rows} unauthorized rows"
    assert missing == 0, f"{user}/{table}: {missing} missing tokens"
    # A multiset comparison also catches duplicate inflation from overlapping
    # assignments; set equality alone would conceal extra clinical rows.
    assert_rows(
        actual, access.oracle.rows(user, table, ["patient_token"]), table, ["patient_token"]
    )


@pytest.mark.parametrize("user,table", DOCTOR_TABLES)
@pytest.mark.parametrize("shape", ["cte", "subquery", "join", "snapshot"])
def test_doctor_filter_survives_query_shapes(access, user, table, shape):
    resource = f"hospital.silver.{table}"
    sql = {
        "cte": f"WITH x AS (SELECT * FROM {resource}) SELECT DISTINCT patient_token FROM x",
        "subquery": f"SELECT DISTINCT patient_token FROM (SELECT * FROM {resource}) x",
        "join": (
            f"SELECT DISTINCT x.patient_token FROM {resource} x "
            "JOIN hospital.silver.patients p ON p.patient_token = x.patient_token"
        ),
        "snapshot": (
            f"SELECT DISTINCT patient_token FROM {resource} FOR VERSION AS OF "
            f"{access.oracle.boundary['snapshots'][table]}"
        ),
    }[shape]
    actual = {row[0] for row in access.execute(sql, user)}
    assert actual == access.oracle.tokens(user, table)


HIDDEN = [
    (user, row)
    for user, row in CASES
    if row["operations"] == "SELECT" and row["visibility"] == "hidden"
]


@pytest.mark.parametrize(
    "user,row", HIDDEN, ids=[f"{user}/{row['table']}/{row['column']}" for user, row in HIDDEN]
)
@pytest.mark.parametrize("context", ["predicate", "expression", "group", "order", "join", "cte"])
def test_hidden_fields_cannot_be_used_indirectly(access, user, row, context):
    resource, column = TABLES[row["table"]]["resource"], row["column"]
    sql = {
        "predicate": f"SELECT count(*) FROM {resource} WHERE {column} IS NULL",
        "expression": f"SELECT coalesce(CAST({column} AS VARCHAR), 'x') FROM {resource}",
        "group": f"SELECT count(*) FROM {resource} GROUP BY {column}",
        "order": f"SELECT 1 FROM {resource} ORDER BY {column}",
        "join": f"SELECT 1 FROM {resource} a JOIN {resource} b ON a.{column} = b.{column}",
        "cte": f"WITH x AS (SELECT {column} FROM {resource}) SELECT * FROM x",
    }[context]
    access.execute(sql, user, denied=True)


def forbidden_operations(user):
    # Write negatives target a table the user can SELECT, so another table's
    # blanket read denial cannot conceal a missing write restriction.
    table = "claims" if user == "billing" else "patients"
    resource = f"hospital.silver.{table}"
    return {
        "insert": f"INSERT INTO {resource} (patient_token) VALUES ('forbidden')",
        "update": f"UPDATE {resource} SET patient_token = 'forbidden'",
        "delete": f"DELETE FROM {resource}",
        "merge": f"MERGE INTO {resource} t USING (VALUES 'forbidden') s(token) ON t.patient_token=s.token WHEN MATCHED THEN DELETE",
        "truncate": f"TRUNCATE TABLE {resource}",
        "drop": f"DROP TABLE {resource}",
        "alter": f"ALTER TABLE {resource} ADD COLUMN forbidden VARCHAR",
        "create_table": "CREATE TABLE hospital.silver.forbidden (value BIGINT)",
        "create_view": f"CREATE VIEW hospital.silver.forbidden AS SELECT patient_token FROM {resource}",
        "create_schema": "CREATE SCHEMA hospital.forbidden",
        "grant": f"GRANT SELECT ON {resource} TO doctor_alice",
        "revoke": f"REVOKE SELECT ON {resource} FROM doctor_alice",
        "ownership": f"ALTER TABLE {resource} SET AUTHORIZATION USER doctor_alice",
        "care_team_insert": "INSERT INTO hospital.silver.care_team VALUES ('doctor_alice', 'forbidden', DATE '2020-01-01', NULL)",
        "care_team_update": "UPDATE hospital.silver.care_team SET doctor_user = 'doctor_alice'",
        "care_team_delete": "DELETE FROM hospital.silver.care_team",
        "bronze": "SELECT * FROM hospital.bronze.access_probe",
        "system": "SELECT * FROM system.runtime.queries",
        "session": "SET SESSION query_max_run_time = '10m'",
        "catalog_session": "SET SESSION hospital.query_partition_filter_required = false",
        "procedure": "CALL hospital.system.register_table(schema_name => 'silver', table_name => 'forbidden', table_location => 's3://warehouse/silver/forbidden')",
        "table_procedure": f"ALTER TABLE {resource} EXECUTE optimize",
        "table_function": "SELECT * FROM TABLE(hospital.system.table_changes(schema_name => 'silver', table_name => 'patients', start_snapshot_id => 1, end_snapshot_id => 2))",
    }


@pytest.mark.parametrize("user", USERS)
@pytest.mark.parametrize("operation", list(forbidden_operations("admin")))
def test_forbidden_operations_require_permission_denied(access, user, operation):
    access.execute(forbidden_operations(user)[operation], user, denied=True)


@pytest.mark.parametrize("user", USERS)
@pytest.mark.parametrize(
    "target", ["admin", "doctor_alice", "doctor_bob", "policy_care_team_reader"]
)
def test_authenticated_user_cannot_impersonate_another_identity(access, user, target):
    if user == target:
        assert access.execute("SELECT current_user", user) == [[user]]
    else:
        access.execute("SELECT 1", target, authenticated_as=user, denied=True)


@pytest.mark.parametrize("user", [user for user in USERS if user != "admin"])
def test_forged_group_header_cannot_elevate_access(access, user):
    access.execute(
        "SELECT * FROM hospital.silver.care_team",
        user,
        headers={"X-Trino-Group": "admin"},
        denied=True,
    )


@pytest.mark.parametrize("user", USERS)
@pytest.mark.parametrize(
    "suffix", ["files", "manifests", "snapshots", "history", "partitions", "properties", "refs"]
)
def test_iceberg_metadata_tables_are_not_implicit_grants(access, user, suffix):
    table = "claims" if user == "billing" else "patients"
    access.execute(f'SELECT * FROM hospital.silver."{table}${suffix}"', user, denied=True)


@pytest.mark.parametrize("user", USERS)
def test_metadata_and_hidden_connector_columns(access, user):
    permitted = [
        table
        for table in ("patients", "encounters", "claims", "care_team")
        if decisions(user, table)[0]["operations"] == "SELECT"
    ]
    assert access.execute("SHOW TABLES FROM hospital.silver", user) == [
        [table] for table in sorted(permitted)
    ]
    assert access.execute(
        "SELECT table_name, column_name FROM hospital.information_schema.columns WHERE table_schema='silver' ORDER BY table_name, ordinal_position",
        user,
    ) == [
        [table, row["column"]]
        for table in sorted(permitted)
        for row in decisions(user, table)
        if row["visibility"] != "hidden"
    ]
    resource = f"hospital.silver.{permitted[0]}"
    for column in ("$path", "$partition", "$file_modified_time"):
        access.execute(f'SELECT "{column}" FROM {resource}', user, denied=True)
    access.execute(f"SHOW CREATE TABLE {resource}", user, denied=True)
