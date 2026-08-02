from sdk.python.kip_client import KipClient

client = KipClient(
    base_url="http://127.0.0.1:8080",
    api_key="replace-me",
    workspace="default",
    principal_id="example-app",
    acl_scopes=["workspace:default"],
)

for hit in client.search("협약 변경 승인", limit=5):
    evidence = client.read_unit(hit["unit_id"])
    print(evidence["source_uri"], evidence["unit"]["locator"])
