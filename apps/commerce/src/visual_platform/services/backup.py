"""Private native backup/restore for an isolated platform database and object store."""

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4, uuid5

import psycopg
from botocore.exceptions import ClientError
from psycopg import sql
from psycopg.types.json import Jsonb

from visual_platform.db.result import required_row
from visual_platform.services.config_backup import (
    capture_configuration,
    restore_configuration,
    validate_configuration,
)
from visual_platform.storage.s3 import S3Storage

SCHEMAS = ("app", "auth", "ops", "public")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_file(root, relative):
    path = root / relative
    require(
        path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root),
        "archive file escapes the private backup",
    )
    return path


def now():
    return datetime.now(timezone.utc).isoformat()


def database_url(value):
    value = value.replace("postgresql+psycopg://", "postgresql://")
    u = urlsplit(value)
    require(
        u.scheme in ("postgres", "postgresql")
        and u.hostname in ("127.0.0.1", "localhost")
        and u.username
        and not u.query
        and not u.fragment,
        "explicit loopback database URL without extra parameters required",
    )
    return value, u


def pg_environment(value):
    _, u = database_url(value)
    return {
        **os.environ,
        "PGHOST": u.hostname,
        "PGPORT": str(u.port or 5432),
        "PGDATABASE": unquote(u.path.lstrip("/")),
        "PGUSER": unquote(u.username),
        "PGPASSWORD": unquote(u.password or ""),
        "PGCONNECT_TIMEOUT": "5",
    }


def native(pg_bin, tool):
    path = Path(pg_bin) / (tool + (".exe" if os.name == "nt" else ""))
    require(path.is_file(), "native PostgreSQL tool missing")
    return str(path)


def run_pg(command, dsn, log):
    result = subprocess.run(command, env=pg_environment(dsn), capture_output=True, timeout=300)
    Path(log).write_bytes(result.stdout + result.stderr)
    require(result.returncode == 0, "native PostgreSQL command failed; private diagnostic retained")


def table_fingerprints(c):
    c.execute("SET LOCAL TIME ZONE 'UTC'")
    tables = c.execute(
        "SELECT n.nspname,r.relname FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=ANY(%s) AND r.relkind IN ('r','p') ORDER BY 1,2",
        (list(SCHEMAS),),
    ).fetchall()
    result = {}
    for schema, table in tables:
        require(
            schema != "public" or table == "alembic_version",
            "database is not an isolated platform database",
        )
        digest = hashlib.sha256()
        count = 0
        with c.cursor(name="backup_" + uuid4().hex) as cursor:
            cursor.execute(
                sql.SQL(
                    'SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text COLLATE "C"'
                ).format(sql.Identifier(schema), sql.Identifier(table))
            )
            for row in cursor:
                digest.update(row[0].encode() + b"\n")
                count += 1
        result[schema + "." + table] = {"count": count, "sha256": digest.hexdigest()}
    require(
        "app.wallets" in result and "public.alembic_version" in result, "platform schema missing"
    )
    return result


def invariant_counts(c):
    bad_wallets = required_row(
        c.execute(
            "SELECT count(*) FROM app.wallets WHERE available<0 OR reserved<0 OR available+reserved<>funded_total-spent_total"
        )
    )[0]
    bad_ledger = required_row(
        c.execute(
            """SELECT count(*) FROM app.wallets w LEFT JOIN (SELECT user_id,sum(available_delta) a,sum(reserved_delta) r,sum(funded_delta) f,sum(spent_delta) s FROM app.credit_transactions GROUP BY user_id) l USING(user_id) WHERE w.available<>COALESCE(l.a,0) OR w.reserved<>COALESCE(l.r,0) OR w.funded_total<>COALESCE(l.f,0) OR w.spent_total<>COALESCE(l.s,0)"""
        )
    )[0]
    bad_charge = required_row(
        c.execute(
            "SELECT count(*) FROM (SELECT task_id FROM app.credit_transactions WHERE type IN ('charge','refund') AND task_id IS NOT NULL GROUP BY task_id HAVING count(*)>1) q"
        )
    )[0]
    require(not (bad_wallets or bad_ledger or bad_charge), "financial invariant failure")
    return {
        "bad_wallets": bad_wallets,
        "bad_ledger_totals": bad_ledger,
        "duplicate_terminal_settlements": bad_charge,
    }


def function_fingerprint(c):
    c.execute("SET LOCAL search_path=pg_catalog")
    rows = c.execute(
        "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),r.rolname,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname IN ('app','auth','ops') ORDER BY 1,2,3"
    ).fetchall()
    return sha(json.dumps(rows, default=str).encode())


def roles(c):
    names = c.execute(
        "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname"
    ).fetchall()
    names = [r[0] for r in names]
    memberships = c.execute(
        """SELECT parent.rolname,member.rolname,a.admin_option,a.inherit_option,a.set_option FROM pg_auth_members a JOIN pg_roles parent ON parent.oid=a.roleid JOIN pg_roles member ON member.oid=a.member WHERE member.rolname=ANY(%s) ORDER BY 1,2""",
        (names,),
    ).fetchall()
    require(
        all(row[0] in names and not row[2] and row[3] and row[4] for row in memberships),
        "nonstandard role membership requires a reviewed restore plan",
    )
    return {
        "source_ddl_role": required_row(c.execute("SELECT current_user"))[0],
        "names": names,
        "memberships": [list(r[:2]) for r in memberships],
        "restore_policy": "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; fresh service logins provisioned separately",
    }


def object_references(c, bucket):
    rows = []
    for row in c.execute(
        "SELECT id,bucket,object_key,storage_version_id,sha256,mime,byte_size FROM app.asset_versions"
    ):
        rows.append(
            {
                "kind": "asset",
                "id": str(row[0]),
                "bucket": row[1],
                "key": row[2],
                "version": row[3],
                "sha256": row[4],
                "mime": row[5],
                "size": row[6],
            }
        )
    for row in c.execute(
        "SELECT id,report_bucket,report_object_key,report_storage_version_id,report_sha256 FROM app.compliance_checks"
    ):
        rows.append(
            {
                "kind": "report",
                "id": str(row[0]),
                "bucket": row[1],
                "key": row[2],
                "version": row[3],
                "sha256": row[4],
                "mime": "application/json",
            }
        )
    for row in c.execute(
        "SELECT id,evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256 FROM app.platform_rules WHERE evidence_object_key IS NOT NULL"
    ):
        rows.append(
            {
                "kind": "rule_evidence",
                "id": str(row[0]),
                "bucket": row[1],
                "key": row[2],
                "version": row[3],
                "sha256": row[4],
                "mime": "application/octet-stream",
            }
        )
    if required_row(c.execute("SELECT to_regclass('ops.supplier_bill_reviews')"))[0]:
        for row in c.execute(
            "SELECT id,evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256 FROM ops.supplier_bill_reviews"
        ):
            rows.append(
                {
                    "kind": "supplier_bill_evidence",
                    "id": str(row[0]),
                    "bucket": row[1],
                    "key": row[2],
                    "version": row[3],
                    "sha256": row[4],
                    "mime": "application/octet-stream",
                }
            )
    if required_row(c.execute("SELECT to_regclass('ops.supplier_price_versions')"))[0]:
        for row in c.execute(
            "SELECT id,evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256 FROM ops.supplier_price_versions"
        ):
            rows.append(
                {
                    "kind": "supplier_price_evidence",
                    "id": str(row[0]),
                    "bucket": row[1],
                    "key": row[2],
                    "version": row[3],
                    "sha256": row[4],
                    "mime": "application/octet-stream",
                }
            )
    for row in c.execute(
        "SELECT id,temp_key,expected_mime,expected_size FROM app.upload_intents WHERE status IN ('pending','validating')"
    ):
        rows.append(
            {
                "kind": "pending_upload",
                "id": str(row[0]),
                "bucket": bucket,
                "key": row[1],
                "version": None,
                "mime": row[2],
                "expected_size": row[3],
                "optional": True,
            }
        )
    return rows


def read_object(client, row):
    params = {"Bucket": row["bucket"], "Key": row["key"]}
    if row["version"]:
        params["VersionId"] = row["version"]
    response = client.get_object(**params)
    try:
        require(response["ContentLength"] <= 80 * 1024 * 1024, "backup object too large")
        data = response["Body"].read(80 * 1024 * 1024 + 1)
        require(len(data) <= 80 * 1024 * 1024, "backup object too large")
        if row.get("sha256"):
            require(sha(data) == row["sha256"], "referenced object hash mismatch")
        if row.get("size") is not None:
            require(len(data) == row["size"], "referenced object size mismatch")
        return data, response.get("VersionId"), response.get("ContentType")
    finally:
        response["Body"].close()


def backup_database(
    dsn,
    storage_settings,
    pg_bin,
    directory,
    fake_directory,
    *,
    source_root=None,
    runtime_settings=None,
):
    dsn, u = database_url(dsn)
    directory = Path(directory)
    require(not directory.exists(), "backup directory must be new")
    directory.mkdir(parents=True, mode=0o700)
    (directory / "objects").mkdir(mode=0o700)
    (directory / "fake").mkdir(mode=0o700)
    started = now()
    configuration = capture_configuration(
        source_root or Path(__file__).resolve().parents[3], directory, runtime_settings or {}
    )
    storage = S3Storage(storage_settings)
    try:
        with psycopg.connect(dsn) as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            c.execute("SET LOCAL idle_in_transaction_session_timeout='300000'")
            snapshot = required_row(c.execute("SELECT pg_export_snapshot()"))[0]
            manifest = {
                "format_version": 2,
                "configuration_snapshot": configuration,
                "backup_id": str(uuid4()),
                "started_at": started,
                "snapshot_at": now(),
                "source_system_identifier": str(
                    required_row(c.execute("SELECT system_identifier FROM pg_control_system()"))[0]
                ),
                "source_s3_endpoint": storage_settings.s3_endpoint,
                "source_bucket": storage.bucket,
                "postgres_version": required_row(c.execute("SHOW server_version"))[0],
                "migration_head": required_row(
                    c.execute("SELECT version_num FROM public.alembic_version")
                )[0],
                "tables": table_fingerprints(c),
                "functions_sha256": function_fingerprint(c),
                "roles": roles(c),
                "invariants": invariant_counts(c),
                "objects": [],
                "fake_files": [],
            }
            command = [
                native(pg_bin, "pg_dump"),
                "--format=custom",
                "--no-password",
                "--file",
                str(directory / "database.dump"),
                "--snapshot",
                snapshot,
            ]
            command.extend("--schema=" + s for s in SCHEMAS)
            run_pg(command, dsn, directory / "pg_dump.log")
            manifest["database_sha256"] = sha_file(directory / "database.dump")
            for row in object_references(c, storage.bucket):
                try:
                    data, version, content_type = read_object(storage.client, row)
                except ClientError as exc:
                    if row.get("optional") and exc.response["Error"]["Code"] in (
                        "NoSuchKey",
                        "NoSuchVersion",
                        "404",
                    ):
                        manifest["objects"].append({**row, "missing_at_snapshot": True})
                        continue
                    raise
                digest = sha(data)
                relative = "objects/" + digest
                path = directory / relative
                if not path.exists():
                    path.write_bytes(data)
                manifest["objects"].append(
                    {
                        **row,
                        "sha256": digest,
                        "size": len(data),
                        "file": relative,
                        "observed_version": version,
                        "mime": content_type or row["mime"],
                    }
                )
            attempts = c.execute(
                "SELECT id FROM app.provider_attempts WHERE provider='fake'"
            ).fetchall()
            for (identity,) in attempts:
                for step in ("generate", "detect"):
                    for extension in ("intent", "json", "png"):
                        name = step + "-" + str(identity) + "." + extension
                        path = Path(fake_directory) / name
                        if not path.exists():
                            continue
                        require(
                            path.is_file()
                            and not path.is_symlink()
                            and path.stat().st_size <= 80 * 1024 * 1024,
                            "invalid fake provider archive",
                        )
                        data = path.read_bytes()
                        (directory / "fake" / name).write_bytes(data)
                        manifest["fake_files"].append(
                            {"name": name, "sha256": sha(data), "size": len(data)}
                        )
        manifest["finished_at"] = now()
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        (directory / "manifest.json").write_bytes(data)
        (directory / "manifest.sha256").write_text(sha(data))
        return manifest
    finally:
        storage.close()


def load_backup(directory):
    directory = Path(directory).resolve()
    data = (directory / "manifest.json").read_bytes()
    require(
        sha(data) == (directory / "manifest.sha256").read_text().strip(),
        "manifest checksum mismatch",
    )
    m = json.loads(data)
    require(
        type(m.get("format_version")) is int and m["format_version"] in (1, 2),
        "unsupported backup format",
    )
    if m["format_version"] == 2:
        validate_configuration(directory, m["configuration_snapshot"])
    require(
        sha_file(checked_file(directory, "database.dump")) == m["database_sha256"],
        "database archive checksum mismatch",
    )
    for obj in m["objects"]:
        if obj.get("missing_at_snapshot"):
            continue
        require(
            re.fullmatch(r"objects/[0-9a-f]{64}", obj["file"])
            and obj["file"] == "objects/" + obj["sha256"],
            "invalid object archive path",
        )
        path = checked_file(directory, obj["file"])
        require(
            path.stat().st_size <= 80 * 1024 * 1024 and sha_file(path) == obj["sha256"],
            "object archive checksum mismatch",
        )
    for item in m["fake_files"]:
        require(
            re.fullmatch(r"(generate|detect)-[0-9a-f-]{36}\.(intent|json|png)", item["name"]),
            "invalid fake archive path",
        )
        path = checked_file(directory, "fake/" + item["name"])
        require(
            path.stat().st_size <= 80 * 1024 * 1024 and sha_file(path) == item["sha256"],
            "fake archive checksum mismatch",
        )
    return m


def restore_database(
    dsn, storage_settings, pg_bin, directory, fake_directory, *, configuration_output=None
):
    dsn, _ = database_url(dsn)
    directory = Path(directory).resolve()
    m = load_backup(directory)
    started = now()
    require(
        storage_settings.s3_endpoint.rstrip("/") != m["source_s3_endpoint"].rstrip("/"),
        "restore requires an independent object service",
    )
    fake_directory = Path(fake_directory)
    require(
        not fake_directory.exists() and not fake_directory.is_symlink(),
        "restored fake directory must be new",
    )
    if m["format_version"] == 2:
        require(
            configuration_output is not None,
            "explicit separate configuration output required for version 2 backup",
        )
        require(not Path(configuration_output).is_symlink(), "configuration output symlink refused")
        configuration_output = Path(configuration_output).resolve()
        fake_resolved = fake_directory.resolve()
        require(
            not configuration_output.exists()
            and not configuration_output.is_relative_to(directory)
            and not directory.is_relative_to(configuration_output),
            "configuration output must be new and separate from archive",
        )
        require(
            not configuration_output.is_relative_to(fake_resolved)
            and not fake_resolved.is_relative_to(configuration_output),
            "configuration and fake output directories must be separate",
        )
    storage = S3Storage(storage_settings)
    try:
        require(
            not storage.client.list_buckets()["Buckets"], "restore object service must be empty"
        )
        with psycopg.connect(dsn) as c:
            require(
                required_row(c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))[
                    0
                ],
                "isolated restore DDL role required",
            )
            require(
                str(required_row(c.execute("SELECT system_identifier FROM pg_control_system()"))[0])
                != m["source_system_identifier"],
                "restore requires an independent PostgreSQL cluster",
            )
            require(
                not c.execute(
                    "SELECT 1 FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
                ).fetchall(),
                "restore target database is not empty",
            )
            require(
                not c.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')"
                ).fetchall(),
                "restore target has nondefault schemas",
            )
            require(
                not c.execute(
                    "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
                ).fetchall(),
                "restore target has public functions",
            )
            current = required_row(c.execute("SELECT current_user"))[0]
            for name in m["roles"]["names"]:
                exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
                if exists:
                    require(
                        name == current and name == m["roles"].get("source_ddl_role"),
                        "restore target has existing platform roles",
                    )
                else:
                    c.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT"
                        ).format(sql.Identifier(name))
                    )
            for parent, member in m["roles"]["memberships"]:
                c.execute(
                    sql.SQL("GRANT {} TO {}").format(sql.Identifier(parent), sql.Identifier(member))
                )
            # Only the independently verified, empty initdb namespace is replaced. No CASCADE.
            c.execute("DROP SCHEMA public")
        buckets = {obj["bucket"] for obj in m["objects"]}
        buckets.add(m["source_bucket"])
        for bucket in buckets:
            storage.client.create_bucket(Bucket=bucket)
        locations = []
        for obj in m["objects"]:
            if obj.get("missing_at_snapshot"):
                continue
            data = (directory / obj["file"]).read_bytes()
            response = storage.client.put_object(
                Bucket=obj["bucket"],
                Key=obj["key"],
                Body=data,
                ContentType=obj["mime"],
                Metadata={"sha256": obj["sha256"]},
                IfNoneMatch="*",
            )
            version = response.get("VersionId")
            stored, _, _ = read_object(storage.client, {**obj, "version": version})
            require(sha(stored) == obj["sha256"], "restored object hash mismatch")
            locations.append((obj, version))
        run_pg(
            [
                native(pg_bin, "pg_restore"),
                "--no-password",
                "--single-transaction",
                "--exit-on-error",
                "--dbname",
                unquote(urlsplit(dsn).path.lstrip("/")),
                str(directory / "database.dump"),
            ],
            dsn,
            directory / "pg_restore.log",
        )
        with psycopg.connect(dsn) as c:
            require(table_fingerprints(c) == m["tables"], "restored database data mismatch")
            require(
                function_fingerprint(c) == m["functions_sha256"],
                "restored function or owner mismatch",
            )
            before = invariant_counts(c)
            c.execute("SET LOCAL session_replication_role=replica")
            for obj, version in locations:
                if obj["kind"] == "asset":
                    c.execute(
                        "UPDATE app.asset_versions SET storage_version_id=%s WHERE id=%s",
                        (version, obj["id"]),
                    )
                elif obj["kind"] == "report":
                    c.execute(
                        "UPDATE app.compliance_checks SET report_storage_version_id=%s WHERE id=%s",
                        (version, obj["id"]),
                    )
                elif obj["kind"] == "rule_evidence":
                    c.execute(
                        "UPDATE app.platform_rules SET evidence_storage_version_id=%s WHERE id=%s",
                        (version, obj["id"]),
                    )
                elif obj["kind"] == "supplier_price_evidence":
                    c.execute(
                        "UPDATE ops.supplier_price_versions SET evidence_storage_version_id=%s WHERE id=%s",
                        (version, obj["id"]),
                    )
                elif obj["kind"] == "supplier_bill_evidence":
                    c.execute(
                        "UPDATE ops.supplier_bill_reviews SET evidence_storage_version_id=%s WHERE id=%s",
                        (version, obj["id"]),
                    )
            c.execute("SET LOCAL session_replication_role=origin")
            if required_row(c.execute("SELECT to_regclass('ops.service_heartbeats')"))[0]:
                c.execute("DELETE FROM ops.service_heartbeats")
            c.execute("UPDATE auth.session_families SET revoked_at=COALESCE(revoked_at,now())")
            c.execute("UPDATE auth.refresh_sessions SET revoked_at=COALESCE(revoked_at,now())")
            live_policy_revocations = 0
            if required_row(
                c.execute("SELECT to_regprocedure('ops.revoke_restored_live_policies()')")
            )[0]:
                live_policy_revocations = required_row(
                    c.execute("SELECT ops.revoke_restored_live_policies()")
                )[0]
            provider_budget_revocations = 0
            if required_row(c.execute("SELECT to_regclass('ops.provider_budgets')"))[0]:
                # An unconsumed quote may have spent money after the snapshot.
                # Restoring its old authorization must never enable new submissions.
                provider_budget_revocations = c.execute(
                    "UPDATE ops.provider_budgets SET state='blocked' WHERE state='open'"
                ).rowcount
            tasks = c.execute(
                "SELECT id,status,credits_status FROM app.generation_tasks WHERE status IN ('queued','processing','uncertain') FOR UPDATE"
            ).fetchall()
            restore_id = uuid4()
            for task, status, credits in tasks:
                c.execute(
                    "UPDATE app.generation_tasks SET status='uncertain',error_code='restore_reconciliation_required',lease_owner=NULL,lease_until=NULL,fencing_token=fencing_token+1,revision=revision+1,updated_at=now() WHERE id=%s",
                    (task,),
                )
                c.execute(
                    "UPDATE ops.task_dispatch SET state='manual',lease_until=NULL,fencing_token=fencing_token+1 WHERE task_id=%s",
                    (task,),
                )
                c.execute(
                    "INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id) VALUES(current_user,'restore_quarantine','task',%s,'post-snapshot supplier activity must be reconciled',%s,%s,%s,%s)",
                    (
                        task,
                        m["database_sha256"],
                        Jsonb({"status": status, "credits_status": credits}),
                        Jsonb({"status": "uncertain", "restore_id": str(restore_id)}),
                        uuid5(restore_id, str(task)),
                    ),
                )
            after = invariant_counts(c)
        fake_directory.mkdir(parents=True, mode=0o700)
        for item in m["fake_files"]:
            (fake_directory / item["name"]).write_bytes(
                (directory / "fake" / item["name"]).read_bytes()
            )
        configuration = (
            restore_configuration(directory, m["configuration_snapshot"], configuration_output)
            if m["format_version"] == 2
            else {"status": "unavailable_legacy_format", "applied_to_runtime": False}
        )
        result = {
            "backup_id": m["backup_id"],
            "restore_id": str(restore_id),
            "started_at": started,
            "finished_at": now(),
            "snapshot_at": m["snapshot_at"],
            "table_count": len(m["tables"]),
            "object_references_verified": len(locations),
            "fake_files_restored": len(m["fake_files"]),
            "database_exact_before_recovery_guards": True,
            "function_owners_preserved": True,
            "old_logins_restored_as_nologin": True,
            "sessions_revoked": True,
            "provider_budgets_revoked": provider_budget_revocations,
            "live_provider_policies_revoked": live_policy_revocations,
            "quarantined_tasks": len(tasks),
            "before_invariants": before,
            "after_invariants": after,
            "schema_head": m["migration_head"],
        }
        result["configuration_restore"] = configuration
        return result
    finally:
        storage.close()
