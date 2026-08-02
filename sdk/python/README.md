# Python client example

```python
from kip_client import KipClient

client = KipClient(
    base_url="http://127.0.0.1:8080",
    api_key="...",
    workspace="company",
    acl_scopes=["workspace:company", "project:A"],
)

hits = client.search("참여율 변경 승인")
evidence = client.read_unit(hits[0]["unit_id"])
```

Applications should use the REST/OpenAPI contract. AI agents can use the same
application behavior through CLI JSON or MCP tools.
