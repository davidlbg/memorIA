const token = process.env.CONNECTOR_SECRET;
if (!token) process.exit(1);
void fetch("http://127.0.0.1:3001/health", {
  headers: { Authorization: `Bearer ${token}` },
  signal: AbortSignal.timeout(3000),
}).then(
  (response) => process.exit(response.ok ? 0 : 1),
  () => process.exit(1),
);
