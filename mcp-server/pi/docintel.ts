import { createMCPClient } from "./mcp-bridge";

export default function (pi: any) {
  // Needs the Document Intelligence server running. Settings come from the environment:
  // DOCINTEL_URL (default http://localhost:8000), DOCINTEL_API_KEY (if the server has one).
  const env: Record<string, string> = {};
  if (process.env.DOCINTEL_URL) env.DOCINTEL_URL = process.env.DOCINTEL_URL;
  if (process.env.DOCINTEL_API_KEY) env.DOCINTEL_API_KEY = process.env.DOCINTEL_API_KEY;

  const client = createMCPClient({
    cmd: "/home/jerichosiahaya/code/doc-intelligence/mcp-server/.venv/bin/python",
    args: ["/home/jerichosiahaya/code/doc-intelligence/mcp-server/server.py"],
    env,
    label: "docintel",
  });

  // get_page_image is left out: the bridge would pass the image on as about 90 KB of text.
  client
    .registerTools(pi, "docintel_", ["ask_documents", "list_documents", "list_conversations", "get_conversation"])
    .then((names) => console.error("[docintel] registered:", names.join(", ")))
    .catch((e) => console.error("[docintel] could not register tools:", e.message));

  pi.on("session_shutdown", () => client.shutdown());
}
