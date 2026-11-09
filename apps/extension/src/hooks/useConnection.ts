import { useEffect, useMemo, useState } from "react";
import { Api, type Connection, ConnectionSchema } from "../services/api";

const initial: Connection = { url: "http://127.0.0.1:8000", token: "" };
export function useConnection() {
  const [connection, setConnection] = useState(initial);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    const load = async () => {
      const data: unknown =
        typeof chrome !== "undefined" && chrome.storage?.session
          ? (await chrome.storage.session.get("connection")).connection
          : undefined;
      const parsed = ConnectionSchema.safeParse(data);
      if (live) {
        if (parsed.success) setConnection(parsed.data);
        setReady(true);
      }
    };
    void load().catch(() => {
      if (live) setReady(true);
    });
    return () => {
      live = false;
    };
  }, []);
  async function save(value: Connection) {
    if (typeof chrome !== "undefined" && chrome.storage?.session)
      await chrome.storage.session.set({ connection: value });
    setConnection(value);
  }
  const api = useMemo(() => new Api(connection), [connection]);
  return { connection, save, ready, api };
}
