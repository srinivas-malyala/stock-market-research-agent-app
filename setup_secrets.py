"""Create/update Databricks secrets required by both independent apps."""
import getpass
from databricks.sdk import WorkspaceClient

client = WorkspaceClient()
for scope in ("massive", "database"):
    try: client.secrets.create_scope(scope=scope)
    except Exception as error:
        if "already exists" not in str(error).lower(): raise

client.secrets.put_secret(scope="massive", key="api-key", string_value=getpass.getpass("Massive API key: "))
client.secrets.put_secret(scope="database", key="lakebase-url", string_value=getpass.getpass("Lakebase Postgres URL: "))
print("Stored massive/api-key and database/lakebase-url. Grant both App service principals READ access to these scopes.")

